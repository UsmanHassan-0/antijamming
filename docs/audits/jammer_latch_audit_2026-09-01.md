# Jammer-latch and protection-release audit — 2026-09-01

## Scope and result

This targeted audit inspected the cleanup product at
`2bdb611605ab88caab162586f0c5d9c5030d2c14`. It traced the checked-in profile,
activation calculation, common LCMV worker, Shared-U1 GNSS fanout, GUI status,
focused tests, Git history, and retained hardware records. It did not change
runtime code and did not exercise attached RF hardware. The correction
amendment below records the later bounded implementation; the original audit
remains as evidence of the pre-correction state.

At the inspected pre-correction commit, automatic jammer-off protection
release was **not implemented**. Its detection latch was intentionally sticky
until LCMV was disabled. A related release design then existed only on
`origin/per-prn-fifo-experimental`; it was not ported into the cleanup product.

The audit also reproduced a separate operator-disable race: an in-flight LCMV
calculation can publish ON status, non-uniform target weights, and GNSS
protection availability after the operator has disabled LCMV.

## 2026-09-02 correction and software verification amendment

Cleanup commit `86eca715c18b18dff308588fcc20c9e15e3e5617` implements the
bounded release correction without merging the per-PRN experimental branch.
`main` and `per-prn-fifo-experimental` remain unchanged.

The corrected state machine separates two facts:

- `lcmv_jammer_detected_latched` is historical run memory and remains true
  until LCMV is disabled;
- `lcmv_jammer_protection_active` controls whether common and GNSS protection
  is currently applied.

Activation still requires both the configured 3 dB input-power rise and 6 dB
generalized-covariance gain. Active protection releases only when both metrics
are valid and remain at or below 1.5 dB and 3 dB, respectively, for two
seconds. Missing, invalid, or hysteresis-band evidence resets the release timer
while retaining protection. New activation evidence cancels a pending release
and can reactivate protection after release. Configuration validation requires
each release threshold to be lower than its matching activation threshold.

Release evidence is evaluated before MUSIC-target validity and guard exits, so
a disappearing peak does not prevent release. On release, the common target is
scheduled back to uniform, Shared-U1 protection availability is cleared, and
the phase-compensation bank receives a falling transition. The GUI separately
reports armed-uniform, protection-active, and released-uniform states.

A re-entrant control lock serializes the complete enable/disable transaction
with a complete LCMV worker update. Disable may wait for an already-running
solve, but once disable returns that solve cannot republish weights,
protection, or ON status. A deterministic solver-barrier regression exercises
that ordering.

Software verification recorded for the implementation commit:

```text
focused config/beamforming/GUI: 150 passed
full suite:                     412 passed, 1 skipped
warnings-as-errors full suite:  412 passed, 1 skipped
coverage full suite:            412 passed, 1 skipped; 79% aggregate
Ruff, Vulture >=90%, compileall, shell syntax, diff check: passed
```

The skip was the explicitly opt-in exclusive USRP smoke test. Deterministic
tests cover sustained low evidence, ambiguous evidence, hysteresis-band
chatter, post-release reactivation, release without a MUSIC target, actual FIFO
return to uniform, GUI state, and operator-disable publication ordering. This
proves only those inserted software schedules. It does not prove every thread
interleaving, physical jammer classification, the threshold choices, or a
physical jammer ON-to-OFF cycle.

## 2026-09-07 follow-up — malformed evidence can bridge the release hold

This is a newly reproduced software gap, not a correction. The same harness
reproduced it on main `d8aef5f` before rollback and cleanup based on
`6b45b24` (including the measured-only migration working tree). Conversion of
the supplied covariance to complex128 can raise before
`_lcmv_jammer_activation_evidence` updates the timer. The outer update catches
that error and publishes fallback but does not clear
`_lcmv_jammer_release_candidate_since_monotonic_s`.

The following focused harness uses the real runtime entrypoint; no RF runs:

```python
import logging
import numpy as np
from unittest.mock import patch
from antijamming.config import StreamConfig
from antijamming.runtime import BackendRuntime

names = ('app hw stream transport handoff phase doa lcmv analysis '
         'lcmv_pattern spatial_vector gnss health errors').split()
r = BackendRuntime(StreamConfig(lcmv_test_enabled=True,
    lcmv_jammer_release_hold_s=2.0), {k: logging.getLogger(k) for k in names})
r._realtime_preserve_frozen_covariance = np.eye(4, dtype=np.complex128)
r._realtime_preserve_frozen_raw_power_linear = 1.0
r._realtime_preserve_frozen_cal_power_linear = 1.0
r._lcmv_jammer_protection_active = True
for t, covariance in [(10.0, np.eye(4)), (11.0, 'malformed'), (12.1, np.eye(4))]:
    with patch('antijamming.runtime.backend.time.monotonic', return_value=t):
        r._update_lcmv_test_from_music(
            np.ones((4, 32), dtype=np.complex64), float('nan'), float('nan'),
            covariance_matrix=covariance,
            raw_power_metrics={'raw_avg_channel_power_linear': 1.0},
            cal_power_metrics={'cal_avg_channel_power_linear': 1.0},
            target_selection_source='audit_missing_target')
    print(t, r._lcmv_jammer_protection_active,
          r._lcmv_jammer_release_candidate_since_monotonic_s)
```

Run from the checkout with `PYTHONPATH=src .aj/bin/python`. Observed output:

```text
10.0 True 10.0
11.0 True 10.0
12.1 False None
```

If invalid evidence interrupts the required continuous hold, the 12.1 update
should instead start a new hold and keep protection active. This does not prove
malformed covariance occurs on attached hardware. It disproves the broader
software assertion that every invalid update resets the release timer. The
earlier successful missing-data test supplied a shape mismatch that reached
the state-update function, unlike this conversion-exception path.

## Pre-correction product state machine

At the inspected commit, the product profile supplied activation thresholds of
3 dB input-power rise and 6 dB generalized covariance gain. There was no
release threshold, persistence count, release hold, or protection-active state
in that schema/profile.

| Condition | Current behavior |
| --- | --- |
| Armed, neither activation gate passes | Latch false; common and GNSS paths remain uniform |
| Both activation gates pass | `lcmv_jammer_detected_latched` becomes true |
| Current evidence later becomes false | `lcmv_jammer_activation_evidence_now` becomes false, but the latch remains true |
| Evidence false, an outside-guard MUSIC peak remains | Common covariance LCMV continues and is recomputed from current covariance |
| Evidence false, no valid outside-guard peak remains | Common path reports uniform fallback, but the GNSS FIFO can retain the last measured-U1 protection row because the latch is still true |
| Operator disables LCMV without an in-flight publication race | Latch clears, protection availability clears, and return to uniform is scheduled |

The implementation is explicit:

- `BackendRuntime._lcmv_jammer_activation_evidence` computes
  `latched_after = latched_before or evidence_now` and documents that the latch
  remains until operator disable.
- `_gnss_shared_u1_phase_output_matrix` sets `enabled_now` from
  `lcmv_test_enabled AND lcmv_jammer_detected_latched`, not from current evidence.
- `SharedU1PhaseCompensationBank` has a smooth falling transition, but it runs
  only when `enabled_now` becomes false. A physical jammer-off transition does
  did not make that happen in the inspected implementation.
- `_activate_lcmv_test_fallback` schedules uniform common weights but does not
  clear Shared-U1 protection availability. Consequently, common `fallback`
  status is not proof that the GNSS FIFO is uniform.

## Pre-correction code-contract harness

A read-only synthetic harness used the same measured healthy-U1 baseline,
activation gates, common LCMV solver, and Shared-U1 protection publication as
the product. No test-only alternate algorithm was selected.

```text
ON {'mode': 'on', 'evidence_now': True, 'latched': True,
    'common_uniform': False, 'fifo_protection_available': True}
OFF_WITH_OUTSIDE_PEAK {'mode': 'on', 'evidence_now': False, 'latched': True,
    'common_uniform': False, 'fifo_protection_available': True,
    'fifo_protection_retained_from_on': True}
OFF_WITH_NO_OUTSIDE_PEAK {'mode': 'fallback', 'latched': True,
    'common_target_uniform': True, 'fifo_protection_available': True,
    'fifo_protection_still_retained': True}
MANUAL_DISABLE {'mode': 'off', 'latched': False,
    'common_target_uniform': True, 'fifo_protection_available': False}
```

This mechanically establishes the pre-correction software transition for the
supplied synthetic covariances. It does not prove that the two activation
thresholds correctly identify a physical jammer or that a particular hold time
is suitable for OTA operation.

## Operator-disable publication race

A deterministic barrier paused the current common covariance solver after the
worker had accepted jammer evidence but before it published its calculated
weights. The control thread then disabled LCMV and was observed in a correct
intermediate state. Releasing the old worker produced:

```text
AFTER_DISABLE_BEFORE_RELEASE
enabled_flag=False status_mode=off target_uniform=True
fifo_protection_available=False

AFTER_INFLIGHT_UPDATE_COMPLETED
enabled_flag=False config_enabled=False latched=False
status_mode=on status_enabled=True target_uniform=False
fifo_protection_available=True
```

The worker exited normally; this was not an injected exception. The control
path protects individual fields with locks, but the calculation has no control
generation/epoch check before publication. Therefore operator OFF is not an
atomic invalidation boundary for already-running LCMV work.

The same schedule can make the GUI/status disagree with the enable flags and
can leave a non-uniform common target available to the GNSS falling path. The
reproduction is deterministic for the inserted solver barrier; unknown natural
hardware scheduling frequency is not measured.

## UI and diagnostics

The automatic evidence record correctly distinguishes
`likely_present` from `no_current_evidence_latch_retained`. The primary GUI LCMV
row does not expose that distinction. It renders every fallback as “Uniform
fallback,” even when the actual GNSS FIFO still has retained measured-U1
protection. Handoff logs record the real per-source FIFO labels, but the main
status wording can misdescribe the receiver input.

## Existing tests and their boundary

Focused current-branch tests passed:

```text
3 passed in 0.14s
```

They establish that angle motion alone does not activate, evidence sets a
sticky latch, and the Shared-U1 bank preserves complex response during an
explicit falling transition. They do not integrate physical jammer-off with
automatic release. In particular:

- the evidence-drop assertion checks only that `evidence_now` becomes false and
  the latch stays true;
- the jammer-off ramp test manually supplies `enabled_now=False`;
- no current test requires physical/evidence disappearance to deactivate GNSS
  protection;
- no test covered operator disable racing an in-flight solver before this audit
  harness;
- no GUI test distinguishes common fallback from the actual retained GNSS FIFO
  spatial row.

## Git provenance

- Sticky activation was introduced in `5ad82768` on 2026-08-07. Current code,
  manifests, tests, and conceptual docs consistently say “latches until
  disable.” It is not an accidental missing assignment.
- Experimental commit `9a23c86` on 2026-08-22 separates historical detection
  from current protection using `lcmv_jammer_protection_active`, a configured
  two-second `lcmv_jammer_release_hold_s`, and tests that return output to
  uniform after current evidence disappears.
- That commit is a large per-PRN/transport change and also contains experimental
  cold-start jammer rescue. Its release code must be reviewed and ported as a
  bounded product change; merging the experimental branch wholesale would
  violate the current branch boundary.

## Retained hardware evidence

The 2026-08-07 record observed input power return to the inferred jammer-off
region while LCMV remained enabled, but C/N0 stayed around 41.5 dB-Hz versus an
approximately 49.5 dB-Hz uniform baseline and recovered to 49.11 dB-Hz after
LCMV disable. That run used older code and jammer state was inferred from a
25–26 dB power step rather than an independent marker. It demonstrates the
risk pattern, not current-commit hardware behavior.

The most recent cleanup RF run had no jammer and never passed the activation
gates. There is therefore no current-product, physically marked jammer ON/OFF
cycle proving release or recovery.

## Required correction boundary

A bounded correction should:

1. keep historical detection separate from current protection state;
2. require persistent below-threshold evidence with explicit hysteresis/hold
   before releasing protection;
3. invalidate in-flight computations with a control generation/epoch and check
   it immediately before every weight, protection, and status publication;
4. clear/transition common and actual GNSS FIFO protection coherently while
   preserving per-PRN complex continuity;
5. make GUI/status display historical latch, current evidence, current
   protection, common weights, and actual FIFO weights separately;
6. regression-test evidence chatter, persistent jammer-off, reappearance during
   release, missing MUSIC targets, invalid covariance, operator disable racing
   every publication stage, and byte/sample-continuous GNSS fanout; and
7. finish with physically marked jammer ON/OFF cycles at bounded J/S and verify
   PRN C/N0, tracking continuity, cycle slips, used-in-fix count, and PVT.

The experimental two-second value is implementation provenance, not a validated
product constant.
