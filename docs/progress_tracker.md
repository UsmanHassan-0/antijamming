# Implementation and Verification Progress

This is the repository's single living progress ledger. Add timestamped entries
here instead of creating another progress-tracker or current-state audit. Exact
run-specific evidence remains under `docs/audits/`; this file summarizes and
links it without rewriting historical observations.

## Claim boundaries

- Cleanup baseline: `main` at
  `0672377aa6c3fa53a11e09747e0cbd300c815579`.
- Active cleanup branch: `cleanup/no-usrp-verification-20260831`.
- `main` and `per-prn-fifo-experimental` are outside this branch's mutations.
- No USRP is attached. Physical TwinRX/X300/UHD timing, RF, antenna, OTA, and
  live GNSS-SDR behavior are not verified by this cleanup.
- A passing test proves only its named inputs and exercised schedules. It does
  not prove that the repository is generally correct or race-free.
- Architecture splitting, including `runtime/backend.py`, is deferred.

## Current dashboard

| Area | Current state | Next evidence gate |
| --- | --- | --- |
| Proven stale code/config | Bounded corrections and final software gates passed | Hardware/external integration remains separate |
| Thread/resource ownership | Deterministic failure paths and repeated software gates passed | Unknown schedules and hardware timing remain unproven |
| Concurrent transitions | Selected start/stop/connect/publish schedules regression-tested | Repeated stress; unknown schedules remain unproven |
| Configuration/input boundaries | Focused regressions passed | Final broad gates and attached-runtime validation |
| Numerical/DSP boundaries | Focused deterministic regressions passed | Final broad gates; OTA correctness remains separate |
| Documentation | One tracker, consecutive conceptual docs, retained audit index, and provenance map | Maintain these documents with future changes |
| Hardware integration | Unverified: no attached USRP | Separate dated hardware run |

## Timestamped change log

### 2026-08-31T15:25:57+05:00 — final hardware-free verification checkpoint

- Full suite passed twice after the last material code batch: `345 passed, 1
  skipped`. One run enabled `PYTHONFAULTHANDLER=1`, `PYTHONMALLOC=debug`,
  `PYTHONDEVMODE=1`, and `PYTHONWARNINGS=error`.
- Coverage-instrumented full suite passed three consecutive times at `345
  passed, 1 skipped`; the reported aggregate source coverage was 77%.
- The skip was explicit: `RUN_USRP_TESTS=1` is required for the exclusive USRP
  hardware smoke test. No attached-hardware conclusion replaces that skip.
- Selected lifecycle failure schedules passed ten consecutive repetitions, 14
  focused tests per run.
- Configured project Ruff, high-confidence Vulture, `compileall`, shell syntax,
  changed-module isolated mypy, and `git diff --check` passed.
- Extended non-gate Ruff diagnostics remain classified rather than hidden: 70
  style/complexity findings (mostly suppressible-exception and redundant-cast
  suggestions) and 41 security diagnostics (mostly best-effort teardown catches
  and subprocess review prompts). No parser/error-class or async-rule finding
  appeared. See “Deliberately unchanged or deferred” for boundaries.
- Documentation existence/numbering tests enforce consecutive conceptual files
  `00`–`08` plus the single tracker, provenance map, and audit index.
- Status: hardware-free branch evidence complete; physical X300/TwinRX/UHD,
  GNSS-SDR receiver, RF, antenna, and OTA verification remains outstanding.

### 2026-08-31T14:52:48+05:00 — documentation consolidation

- Replaced multiple living cleanup/progress documents with this one tracker.
- Kept run-specific records as evidence under `docs/audits/`; a historical
  audit is not treated as current implementation truth.
- Separated conceptual numbered guides from dated evidence and added a stable
  implementation-provenance map.
- Removed the stale source inventory only after confirming that its proposed
  `gnss/gnss_sdr.py` split had already happened and its `runtime/worker.py`
  path no longer described the product runtime.
- Status: documentation mutation in progress; final reference and test gates
  have not yet run.

### 2026-08-31 — hardware-free lifecycle and hygiene cleanup

The following statements are deliberately bounded. “Regression passed” means
the injected schedule was reproduced and exercised; it does not exclude all
possible thread schedules.

| Component | Previous behavior observed | Current cleanup-branch behavior | Evidence status |
| --- | --- | --- | --- |
| TwinRX LO polling | Sleep was unreachable after the polling loop, allowing timeout busy-polling | Sleep occurs between fake sensor polls | Focused fake-clock transition/timeout regressions passed; hardware untested |
| IPC accept/close | Final accepted session could be absent from an early session snapshot | Acceptor is stopped before the final session shutdown pass | Local Unix-socket multi-client and injected partial-start regressions passed |
| Shared-U1 sample ownership | Monitor queue could retain a view of producer-owned mutable IQ | Submission owns a contiguous copy; stop retains live thread/handle on timeout | Deterministic blocked-worker and mutation regression passed |
| UHD event monitor | Stop could discard a live thread then mutate shared scanner state | Timed-out owner is retained; shared finalization follows worker exit | Deterministic blocked-worker regression passed; UHD hardware untested |
| IPC client | Close/connect publication and reader ownership were not synchronized | State publication is locked; reader is joined/retained according to liveness | Barrier-controlled late-connect and reader-shutdown regressions passed |
| IPC server/session | Thread-start failures could leave partial session/server resources | Startup is transactional and rollback closes owned resources | Injected accept/sender start failures and FD checks passed |
| GNSS-SDR bridge | PTY/FIFO/log/monitor resources could survive startup exceptions | Startup rolls back all resources acquired before failure | Injected `Popen` failure with FD/path/owner assertions passed; real receiver untested |
| Backend | Device stop followed RX join and timed-out thread refs could be discarded | Device is stopped before RX join; live timed-out refs remain owned | Blocking fake-device order, join-timeout, stage-failure, and concurrent-start regressions passed |
| Remote worker | Immediate start reply could arrive before request ID publication; rejected start could remain requested | Request ID is installed before send; negative/failed starts release state | Deterministic immediate reply/failure regressions passed |
| GUI subprocess | Partial GUI startup could leave marker/service child; normal exit did not guarantee escalation/reap | Idempotent cleanup performs wait, TERM, KILL, and final reap | Fake child escalation/reaping regression passed |
| Logging | Reconfiguration detached file handlers without closing them | Replaced handlers are flushed and closed | Warning-as-error suite exposed the leak; focused and full suite then passed |
| Qt teardown | Coverage run reproduced deferred graphics-item destruction abort | Timers are widget-owned and deferred deletes are drained in fixture | Five earlier repeated coverage runs passed; other Qt/platform schedules unproven |
| Runtime profile | Unread accuracy-log interval and bench-only RF metadata appeared as product controls | Unread field removed; bench metadata moved to an explicit experiment overlay | Schema/search/config tests passed; RF meaning retained in overlay |
| Runtime JSON | Duplicate keys and non-standard `NaN`/`Infinity` could be accepted; `NaN` bypassed the positive-rate comparison | Duplicate and non-finite values fail at parse/load boundary, including nested experiment data | Duplicate/non-finite/string-rate regressions passed |
| Experiment overlay | Fields were mutated in iteration order before a later invalid key failed | All keys are checked/coerced before updates publish; sample-rate followers publish in the same batch | Partial-invalid and derived-follower regressions passed |
| DSP entry contracts | Empty/non-finite data could yield warnings, NaN payloads, or a meaningless first-grid DoA | Phase/DoA/steering/combiner/diagnostic paths reject named invalid shapes and non-finite inputs | Focused shape/empty/non-finite regressions passed |
| LCMV covariance contract | Solver guarded conditioning/weight norm but did not reject non-Hermitian/non-PSD covariance or non-finite control limits explicitly | Covariance and control parameters now have explicit mathematical-domain checks | Invalid-domain tests plus 100 deterministic random PSD constraint cases passed |

Earlier verification checkpoints retained from the in-progress batches:

- `PYTHONDEVMODE=1 PYTHONWARNINGS=error`: `313 passed, 1 skipped`.
- Focused bridge suite: `84 passed`.
- Focused IPC injected failures: `9 passed`.
- Focused config/DSP/lifecycle set after the input batch: `94 passed`.
- Ten consecutive selected lifecycle stress repetitions: each run `14 passed,
  142 deselected`.
- Changed-module mypy run with imports skipped/ignored: clean for the eight named
  modules. Whole dependency-following mypy remains unclean: `477 errors in 24
  files`, dominated by generated protobuf, dynamic mixin, untyped UHD/Qt, and
  backend dictionary typing debt.
- Configured project Ruff gate: clean after excluding the vendored GNSS-SDR
  subtree from project-owned lint.
- These earlier counts are superseded by the final checkpoint above, but remain
  recorded to show when each correction batch was exercised.

Known diagnostic debt, not represented as clean:

- Whole-package mypy has a substantial existing backlog involving generated
  protobuf modules, dynamic mixins, Qt stubs, untyped dependencies, and backend
  dictionary types. This cleanup does not claim type-checker cleanliness.
- Extended Ruff security/style diagnostics include deliberate teardown catches,
  controlled list-form subprocess launches, a predictable `/tmp` socket with
  permission/type defenses, and style findings. Each needs classification;
  their existence alone is not proof of a vulnerability or behavior defect.

## Deliberately unchanged or deferred

- `runtime/backend.py` and `ui/main_window.py` remain large. Splitting them is a
  separate architecture task because moving ownership boundaries during this
  lifecycle cleanup would make behavior comparison harder.
- `runtime/worker.py` remains a legacy/test Qt adapter and public compatibility
  export. It is not the active product GUI path, but repository tests and UI
  type imports still reference it, so it was not deleted as “unused.”
- The per-user/PID Unix sockets remain under sticky `/tmp` with socket-type
  refusal and mode `0600`. Static security lint flags predictability; changing
  the operational path needs a compatibility/threat-model decision. No command
  injection was established in list-form controlled subprocess launches.
- Best-effort exception handling in low-level UHD teardown was not mass-rewritten
  without hardware evidence. Silent signal-handler registration failures were
  changed to logged warnings because that path was independently observable.
- The customized vendored `gnss-sdr/` tree is excluded from project-owned Ruff
  lint. It is not claimed clean; it has its own upstream/build/test boundary.
- `main` and `per-prn-fifo-experimental` remain unchanged.

## Review and reading scope

- Completely read for this task: applicable `AGENTS.md` files, the reusable
  review playbook, the active tracker, all conceptual numbered docs, selected
  current architecture/hardware/audit docs, and the full MUSIC, LCMV,
  calibration, diagnostic, and DSP-stage source files.
- Read targeted ownership sections to EOF-relevant boundaries for backend,
  headless/GUI startup, IPC, Shared-U1, GNSS bridge monitors, logging, and UHD
  event monitoring; combined these with call-site searches, coverage, static
  tools, and deterministic regressions.
- Performed repository-wide searches for thread starts/joins, subprocesses,
  sockets/files/FD acquisition, broad exception handlers, stale doc references,
  and high-confidence dead code.
- This was not a manual reading of every line, word, or symbol in the 7,000+
  line backend, UI implementation, vendored GNSS-SDR, or every historical audit.
  Coverage/static/search results are not represented as exhaustive manual
  review.

### Pre-2026-08-31 RF/DSP implementation snapshot — source date absent

This table preserves the useful content of the former progress document. Its
original source did not record a timestamp or exact artifact for every “yes,”
so these are historical project-status notes, not newly established proof.

| Capability | Implemented snapshot | Historical RF status / limitation |
| --- | --- | --- |
| USRP stream to GNSS-SDR and baseline PVT | Yes | Partial live evidence; repeat with attached X300 |
| Phase and complex-gain calibration | Yes | Conducted calibration, not a complete OTA manifold calibration |
| Steering/angle convention | Yes | Physical orientation still needs controlled RF proof |
| MUSIC and Bartlett diagnostics | Yes | Partial live evidence; disagreement requires controlled angle sweep |
| Covariance LCMV and measured-U1 candidate paths | Yes | Candidate behavior depends on preserve/null model and labeled live data |
| Desired-loss/noise-gain/spatial-vector diagnostics | Yes | Healthy reference can be absent and dominant U1 is not always a jammer |
| Shared measured-U1 fanout with per-PRN continuity scalar | Yes | One shared spatial solution, not an independent per-PRN LCMV solve |
| GSC exploration | No; proposed | Design only after the present covariance LCMV path has authoritative live evidence |
| Automated jammer confidence/gating | Partial | Thresholds require labeled hardware tuning |

Detailed historical evidence is indexed in `docs/audits/README.md`.

## Open hardware and research questions

- How stable is the OTA manifold compared with conducted splitter calibration?
- Which candidate improves PVT/C/N0 under a real jammer without harming the
  jammer-off baseline?
- What receiver-health thresholds distinguish healthy baseline from a
  jammer-like event?
- Does the frozen measured bladeRF U1 preserve every tracked PRN through a
  sustained jammer interval, or is a desired multi-vector subspace required?
- Tune white-noise-gain, predicted suppression, input-power, and generalized
  covariance gates only from labeled retained runs; angle motion alone is not
  physical jammer truth.
- GSC remains proposed, not implemented.

## Required verification gates

After each material batch, run focused regressions followed by the applicable
repository gates:

```bash
.aj/bin/python -m pytest -q
.aj/bin/python -m pytest --cov=src/antijamming --cov-report=term-missing -q
.aj/bin/ruff check .
.aj/bin/vulture src tests --min-confidence 90
.aj/bin/python -m compileall -q src tests
PYTHONDEVMODE=1 PYTHONWARNINGS=error .aj/bin/python -m pytest -q
git diff --check
```

Record counts, repetition counts, failures, and skipped/deselected hardware
tests here. Do not turn the gate list itself into a claim that it ran.

## Provenance vocabulary

- **Implemented:** present in the named code path.
- **Standard-defined:** required or described by a named external standard.
- **Paper-derived:** adapted from a named publication, with deviations stated.
- **Measured:** retained run/log with configuration and hardware context.
- **Derived:** calculated from stated inputs or equations.
- **Assumed:** used without direct measurement.
- **Proposed:** not implemented or validated.

Do not collapse these categories into “works” or “fixed.”
