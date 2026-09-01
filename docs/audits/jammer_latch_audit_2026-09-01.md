# Jammer-latch and protection-release audit — 2026-09-01

## Scope and result

This targeted audit inspected the cleanup product at
`2bdb611605ab88caab162586f0c5d9c5030d2c14`. It traced the checked-in profile,
activation calculation, common LCMV worker, Shared-U1 GNSS fanout, GUI status,
focused tests, Git history, and retained hardware records. It did not change
runtime code and did not exercise attached RF hardware.

Automatic jammer-off protection release is **not implemented on the current
product branch**. The current detection latch is intentionally sticky until
LCMV is disabled. A related release design exists only on
`origin/per-prn-fifo-experimental`; it was not ported into the cleanup product.

The audit also reproduced a separate operator-disable race: an in-flight LCMV
calculation can publish ON status, non-uniform target weights, and GNSS
protection availability after the operator has disabled LCMV.

## Current product state machine

The product profile supplies activation thresholds of 3 dB input-power rise and
6 dB generalized covariance gain. There is no release threshold, persistence
count, release hold, or protection-active state in the current schema/profile.

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
  not make that happen on the current branch.
- `_activate_lcmv_test_fallback` schedules uniform common weights but does not
  clear Shared-U1 protection availability. Consequently, common `fallback`
  status is not proof that the GNSS FIFO is uniform.

## Current-code contract harness

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

This mechanically establishes the current software transition for the supplied
synthetic covariances. It does not prove that the two activation thresholds
correctly identify a physical jammer or that a particular hold time is suitable
for OTA operation.

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
