# bladeRF preservation and anti-jam audit — 2026-08-21

This is a living evidence ledger. Add new test runs and code changes here rather than replacing earlier results. A result is called proven only to the boundary supported by its recorded evidence.

## Current conclusion

Four independently proven results must not be mixed:

1. Exact clean commit `3419536` preserved every established PRN's full complex response during synchronized jammer-on run `20260821T033327.934359Z_pid664706`; PVT and C/N0 survived the valid BIN interval.
2. A later uncommitted shared-row family changed that operation to phase-only scaling. Raw surviving logs prove that the same pre-jammer PRN vectors were applied after activation with 3.91–24.41 dB less modeled response. Four runs then lost C/N0/PVT; one higher-margin run survived. Phase continuity alone was therefore not a preservation proof.
3. The 2026-08-22 per-PRN monitor was correlating pre-filter raw IQ at a post-filter tracking counter. The 55-tap GNSS input FIR adds 27 samples of group delay. Correcting that offset raised live cross-PRN vector coherence from failed-run minimum/median `0.097/0.810` to `0.987/0.997` and projector concentration to `0.953–0.981`.
4. The integrated TramiqSDR transport failure was downstream FIFO throughput. A 4,096-sample stripe required 80 FIFO writes per ten-source chunk and filled both queues. One full-chunk write per source reduced measured FIFO time from about 8.5 ms to 1.94 ms average and completed the same previously failing interval with raw/output high-waters of only `7/512` and `2/64`.
5. A second full TramiqSDR run, `20260822T090357.700904Z_pid67524`, independently retained PVT through its complete 198 s run with 10 tracked and 7 used PRNs, C/N0 near `49.6–50.0 dB-Hz`, raw/output queue high-waters `14/512` and `4/64`, zero rejected chunks, zero RF overflows, and an empty error log. No jammer activation occurred, so this is baseline/transport evidence only.

The current tree restores full amplitude-and-phase response preservation, freezes each established clean PRN vector for the full jammer interval, allows a newly acquired PRN to adopt its first valid vector, prevents covariance updates from restarting an unfinished transition, and retains finite/norm/conditioning safety checks. Automated coverage is complete, but the corrected independent per-PRN path still requires one synchronized live jammer-on run.

The stale bladeRF-gain manifest field remains a provenance defect. It records 50 dB even when authoritative bladeRF CLI readback proves 60 dB. Hardware settings in conclusions below therefore come from the bladeRF CLI, never the GUI manifest.

## 2026-08-22 audit, fixes, and live evidence

### Historical amplitude regression

The exact `3419536` code used `scale = conjugate(old_response / raw_response)` and applied the resulting rows to actual IQ. The retained audit ledger independently reconstructed 79 active statuses: magnitude drift was below `6e-14 dB`, phase drift below `3e-13 degrees`, and required scalar gain was `+5.544` to `+10.032 dB`. During the valid active window, average C/N0 was `44.960/46.180/46.720 dB-Hz` minimum/median/maximum with no PVT-false snapshot and at least nine tracked PRNs.

The later dirty implementation normalized that scalar to unit magnitude. Independent reconstruction from raw surviving status weights gave:

| Run suffix | Pre-to-post desired-response change | RF result |
|---|---:|---|
| `004435` | `-24.413` to `-16.209 dB` | C/N0 and PVT failed |
| `010046` | `-15.553` to `-9.509 dB` | C/N0 and PVT failed |
| `012459` | `-4.784` to `-3.909 dB` | C/N0 and PVT failed |
| `013622` | `-9.537` to `-7.497 dB` | C/N0 and PVT failed |
| `025209` | `-8.948` to `-6.406 dB` | RF/PVT survived; FIFO later failed |

The desired vectors matched their last pre-active values with coherence 1.0, and their applied response phase remained exact. The lost quantity was amplitude. This is a proven regression/contributor, not a claim that it alone explains every failure.

### FIR/code-epoch alignment

Every GNSS source passes through a 55-tap `Freq_Xlating_Fir_Filter` before tracking, giving `(55 - 1) / 2 = 27` input samples of group delay at 4 MS/s. `SharedU1DesiredVectorMonitor` formerly used the post-filter `tracking_sample_counter` directly against pre-filter raw IQ and searched only about three samples, so it despread roughly 6.9 C/A chips from the correct code epoch. The former prompt-versus-one-wrong-code scalar gate could still pass this wrong epoch.

The runtime now computes the exact FIR tap count with the same GNU Radio formula, converts the tracking counter to `raw_counter = tracking_counter - 27`, records both counter domains, and has a deterministic delayed-FIR regression test. A noise test proves the uncorrected path can pass the old scalar gate while recovering coherence below 0.85; the corrected path recovers coherence above 0.998.

Live corrected run `20260822T084109.916484Z_pid78154` produced 997 accepted records for nine PRNs. The latest vectors had pairwise coherence `0.986617/0.997484/0.999255` minimum/median/maximum, projector concentration `0.953147–0.980645`, and every record showed tracking-minus-raw counter `27`.

### Pre-jammer freeze and transition policy

At jammer activation, every established PRN now keeps its last clean desired vector until jammer release. Measurements made after activation cannot redefine the preserve constraint. A PRN first acquired during the jammer may adopt its first valid vector, then freezes it. The status now reports the real frozen state and age of the adopted vector.

Jammer onset is no longer an immediate one-chunk row replacement. The configured transition runs to completion, and later covariance/shared-context publications cannot reset its progress while it is active. Full complex response is preserved at every transition point. There is no arbitrary +/-6 dB veto; a non-finite row, excessive final row norm, or invalid LCMV conditioning is rejected.

### Integrated FIFO failure and fix

Pre-fix integrated session `20260822T084839.347985Z_pid60727` remained RF-healthy through receiver time 161 s: C/N0 `44.28 dB-Hz`, PVT FIX, seven used PRNs, zero USRP overflow. However, the output queue stayed `64/64`, the raw queue reached `511/512`, and at 13:51:28.924 one raw chunk was rejected. The fail-closed policy paused handoff and stopped GNSS-SDR instead of silently dropping contiguous IQ. Its FIFO summary was:

- writes: `19,622` chunks / `51,435,575,440` bytes;
- average / maximum write: `7.35 / 46.62 ms`;
- pipe: `1,048,576` bytes per source;
- stripe: `4,096` samples;
- maximum source lead: `4,096` samples;
- raw/output lifetime high-water: `512/512` and `64/64`;
- RF overflows: zero.

At 4 MS/s a 32,768-sample chunk arrives every 8.192 ms. Ten sources with eight 4,096-sample stripes required 80 writes per chunk, about 9,766 writes/s. The fix uses one 32,768-sample write per source because the 262,144-byte source chunk fits inside its 1 MiB pipe. Sample order and final byte counts remain identical; maximum inter-source lead rises to one chunk and returns to zero after every fanout.

Post-fix integrated session `20260822T085439.930131Z_pid64224` passed the prior failure point with TramiqSDR near seven CPU cores. Through receiver time 206 s it retained PVT FIX and C/N0 near 44 dB-Hz. Final transport/FIFO evidence was:

- writes: `27,169` chunks / `71,221,903,360` bytes;
- average / maximum write: `1.94 / 24.36 ms`;
- stripe and maximum source lead: `32,768` samples;
- final source-byte spread: zero;
- raw/output lifetime high-water: `7/512` and `2/64`;
- raw rejections, FIFO drops, and RF overflows: zero.

At receiver time 211 s, all desired PRNs collapsed while output remained uniform, queues were empty, input power showed no jammer jump, and no RF overflow occurred. The bladeRF transmitter had started before this receiver. A transmitter head start of about 59 s places this event at the known corrupt file position near 270 s. Treat that mapping as a strong inference, not exact provenance, because the current NucBox Evo X2 transmitter (`simulator@192.168.3.116`) was reachable by ICMP but its SSH service was unavailable, so playback epoch/readback could not be recorded.

Full-application baseline session `20260822T090357.700904Z_pid67524` then ran for 198.09 s and stopped normally from Tramiq. Its final evidence retained 3D Fix, 10 tracked PRNs, 7 used PRNs, C/N0 near `49.6–50.0 dB-Hz`, queue high-waters `14/512` and `4/64`, zero raw rejection, zero RF overflow, and zero bytes in `errors.log`. It did not contain jammer evidence and therefore does not validate jammer-on PRN preservation.

Session `20260822T091115.364450Z_pid61481` reached receiver time 296 s with PVT still fixed and transport high-waters only `11/512` and `3/64`, zero raw rejection, and zero RF overflow. Its late C/N0 and spatial-vector values are excluded from RF/preservation conclusions because the session exceeded the user-defined 270 s BIN boundary and the NucBox playback epoch was unavailable. At the end of this audit the NucBox no longer answered ARP, ICMP, or SSH at `192.168.3.116`.

### Exact source-tree provenance for future runs

Every session manifest now records the Git HEAD, full porcelain dirty-file list, SHA-256 and byte count of the tracked working-tree diff, SHA-256 and byte count of the cached diff, and path/size/SHA-256 for every untracked regular file. This records code identity without copying source or diff contents into the manifest.

The first live manifest exercising this mechanism was `20260822T090826.226199Z_pid72296`. It recorded HEAD `d256e61e11aad0a290ef139a05050f7aeeea03ad`, a 168,703-byte tracked diff with SHA-256 `e226781dbc248c5943fb79670edd6f4aee1c00c323e2124a7dd90d744a8f2e62`, and the untracked audit document with its own SHA-256. This closes the source-state provenance defect for future sessions only; it cannot reconstruct the exact uncommitted source of historical runs.

## Evidence identity

- Anti-jam host: `qvise@192.168.3.154`
- Exact clean audit worktree: `/tmp/antijamming-origin-main-audit.Zd3nBg/repo`
- Exact commit: `341953653f476680d7e20020a12153586b45c4f6`
- Synchronized run: `20260821T033327.934359Z_pid664706`
- Run evidence directory: `/tmp/antijamming-origin-main-audit.Zd3nBg/repo/logs/runs/20260821T033327.934359Z_pid664706`
- historical synchronized-run bladeRF host: `u@192.168.3.157`
- current bladeRF host: NucBox Evo X2, `simulator@192.168.3.116`
- bladeRF serial: `a8df023479b1450ea8f9b8b28ef1039c`
- BIN: `/home/u/Documents/Bins/static/l1_static_50_1584.bin`
- BIN size: `60,000,000,000` bytes
- BIN format: SC16 Q11 complex int16, 4 bytes per complex sample
- Playback rate: 50,000,000 samples/s
- Exact file duration: `60,000,000,000 / (4 * 50,000,000) = 300 s`
- SHA-256: `0c4e4f74310fa7db3a9fc4da1f651666b39caf5b351d6c7efe41017690c1ad43`

## Authoritative bladeRF readback

The bladeRF CLI reported after the synchronized run:

- TX channel: TX1
- Center frequency: 1,584,000,000 Hz
- Sample rate: 50,000,000 samples/s
- RF bandwidth: 50,000,000 Hz
- Software gain: 60 dB
- File: `l1_static_50_1584.bin`
- File format: SC16 Q11 binary
- Repetitions: infinite
- Repetition delay: none
- Stream error: none

The anti-jam experiment manifest said bladeRF gain 50 dB. That field was stale and must not be used as proof of the live transmitter setting.

## Why phase compensation was not sufficient

The pre-change runtime produced one shared spatial LCMV row and multiplied it by a different complex scalar for each PRN. A scalar can rotate the row's phase and scale its magnitude, but it cannot change that row's spatial shape. Therefore it cannot make one shared row independently match several different live PRN spatial vectors.

The old code also had two stale-model paths:

- established PRN desired vectors were frozen while jammer protection was active;
- `SharedU1DesiredVectorMonitor` averaged every accepted projector over the entire process lifetime.

That design can preserve `w^H a_old` to machine precision while `a_old` no longer equals the live `a_PRN`. Phase continuity is still valuable, but only as a constraint on a real spatial update.

## Implemented per-PRN design

The current runtime uses `PerPrnMeasuredVectorBeamformerBank`:

- every currently mapped GPS L1 C/A PRN gets its own logical four-channel row;
- before and after a jammer, the row is the minimum-norm measured-vector combiner that preserves the channel's immediately preceding complex response;
- during a jammer, the row is an independent covariance LCMV solution with that PRN's measured vector as the preserve constraint and the measured jammer U1 as the null constraint;
- the LCMV row is scaled as a whole to keep `w_new^H a_PRN = w_old^H a_PRN`; scalar scaling cannot move or fill the spatial null;
- a newly mapped PRN uses the common acquisition row only until it has enough quality-passed code-despread measurements, then it moves to its independent row;
- measured PRN vectors update while jammer protection is inactive; each
  established vector freezes for the complete active interval and resumes
  updating only after release;
- the vector estimator uses the latest 12 quality-passed projectors, not an unlimited lifetime average;
- source reassignment creates a new state for the new PRN rather than inheriting the previous PRN's row;
- there is no arbitrary +/-6 dB update veto. The only remaining row guard is the configured finite/norm/conditioning safety validation.

For a normalized current desired vector `a` and current row `w_old`, the jammer-off target is:

`r = w_old^H a`

`w_matched = a * conjugate(r) / (a^H a)`

which gives exactly:

`w_matched^H a = r`

For the jammer-active row, covariance LCMV first enforces the PRN preserve constraint and jammer null. A common scalar then enforces the same continuity equation without changing the null location or depth.

## Current per-PRN jammer-off hardware run

Run identity: `20260821T040236.597304Z_pid726612` in `/home/qvise/antijamming/logs/runs/20260821T040236.597304Z_pid726612`.

Authoritative transmitter settings:

- bladeRF serial: `a8df023479b1450ea8f9b8b28ef1039c`;
- file: `/home/u/Documents/Bins/static/l1_static_50_1584.bin`;
- TX1 frequency: 1,584,000,000 Hz;
- sample rate: 50,000,000 samples/s;
- RF bandwidth: 50,000,000 Hz;
- software gain: 60 dB;
- SC16 Q11, infinite repetition, zero repetition delay;
- bladeRF was restarted before the receiver and stopped idle after the run;
- jammer remained off;
- the receiver auto-stopped after 219.010 s of session time;
- playback was stopped at approximately BIN position 236 s, safely below the 260 s operational cutoff and the corrupt region near 270 s.

Measured GNSS result from 1,420 archived runtime snapshots:

- first current PVT: session elapsed 40.602 s;
- current-PVT snapshots after first fix: 1,177;
- PVT-false snapshots after first fix: zero;
- average tracking C/N0 minimum / median / maximum: `47.541 / 48.292 / 48.913 dB-Hz`;
- tracking count minimum / median / maximum: `9 / 10 / 10`;
- a 0.8 s startup reconciliation interval reported current PVT before the used-PRN list populated; after that interval seven PRNs remained used;
- final state at receiver time 215 s: PVT current, seven PRNs used, nine tracked, average C/N0 `48.297 dB-Hz`;
- 78,270 positive-C/N0 tracking rows after first PVT;
- cycle slips: zero.

Per established PRN C/N0 after first PVT:

| PRN | Rows | Minimum | Median | Maximum |
|---|---:|---:|---:|---:|
| G03 | 8,697 | 46.888 | 48.441 | 49.822 |
| G04 | 8,696 | 47.070 | 48.266 | 49.596 |
| G07 | 8,696 | 46.369 | 48.202 | 49.595 |
| G08 | 8,697 | 46.475 | 48.341 | 50.160 |
| G09 | 8,696 | 46.741 | 48.343 | 49.837 |
| G14 | 8,697 | 46.672 | 48.251 | 49.550 |
| G16 | 8,697 | 46.536 | 48.258 | 49.514 |
| G27 | 8,697 | 46.369 | 48.326 | 49.964 |
| G30 | 8,697 | 46.782 | 48.344 | 49.488 |

Per-PRN beamformer evidence from 214 archived handoff-status records:

- maximum active independent PRN rows: nine;
- jammer-active per-PRN LCMV rows: zero, as expected because the jammer remained off;
- output path after acquisition: `independent_per_prn_spatial_matrix`;
- maximum complex-response continuity residual: `4.578e-16`;
- maximum accepted measured-vector step: `15.105 degrees`;
- maximum row norm: `1.571`, below the configured limit of 8;
- row guards/rejections: none;
- final matrix-path chunks: 25,277;
- early collinear/common-row optimized chunks: 882.

Transport and runtime evidence:

- USRP overflows: zero;
- USRP timeouts: zero;
- GNSS raw-queue rejections: zero;
- raw-queue lifetime high-water: 21/512;
- error/traceback/failure lines: zero;
- jammer activation-evidence snapshots: zero;
- jammer-protection-active snapshots: zero.

This run proves the independent per-PRN matched-vector path with live bladeRF/USRP/GNSS-SDR hardware. It does not yet prove the new per-PRN covariance-LCMV branch under live jammer RF.

## Synchronized file-position rule

A live `/proc/<bladeRF-cli-pid>/fdinfo` read showed a file offset of 3,155,296,256 bytes. With 200,000,000 file bytes/s, this was file position 15.77648128 s. Aligning that read to the NADS host clock places the bladeRF playback start at approximately 2026-08-21 08:33:19.383 PKT. Read-ahead uncertainty is at most on the order of the configured bladeRF buffers and does not affect the multi-second conclusion.

The receiver began later. Therefore:

`BIN position != GNSS receiver_time_s`

The synchronized C/N0 decline began at about BIN position 268.6 s and reached 29.2 dB-Hz at about 270.6 s. Those observations are excluded from anti-jam conclusions. Valid statistics below use only BIN positions below 267 s, giving margin before the known corruption boundary.

## Valid synchronized test timeline

- bladeRF playback was restarted before the receiver.
- GNSS PVT became current at receiver time 43 s with C/N0 45.27 dB-Hz, ten tracked PRNs, and six used PRNs.
- LCMV auto-armed on healthy uniform output with the frozen bladeRF measured-U1 bearing near 355.08 degrees.
- The jammer RF rise was automatically detected near BIN position 110 s.
- Activation evidence was approximately 12.75–15.38 dB input-power rise and 20.04–23.86 dB generalized gain.
- The jammer-on, phase-compensated valid interval covered approximately BIN positions 110.8–266.5 s.
- The receiver and bladeRF were stopped; observations at and beyond the corrupt boundary are not used.

## End-to-end phase-compensation path

The executed code path is:

1. Four USRP complex channel streams enter `BackendRuntime`.
2. The configured complex-gain calibration vector is applied with the convention `x_corrected[ch] = correction[ch] * x_raw[ch]`.
3. The healthy bladeRF measured dominant eigenvector is frozen before jammer activation.
4. LCMV computes one shared spatial protection/null row using the frozen bladeRF vector as the preserve constraint.
5. `SharedU1PhaseCompensationBank` computes a separate complex scalar for every mapped PRN.
6. `_gnss_shared_u1_phase_output_matrix()` passes the actual raw chunk, logical per-source rows, and calibration vector to `apply_shared_phase_fanout()`.
7. Because one of ten source slots was unmapped, the rows were not all collinear and the real `general_transition_matrix` branch executed: `effective = conj(rows) * correction`, followed by `effective @ raw_channels`.
8. `bridge.write()` converted every row to contiguous complex64 and wrote equal-length stripes to ten named FIFOs.
9. The generated GNSS-SDR configuration consumed those exact ten FIFO paths as ten synchronized signal sources.

The phase-continuity equation is:

`old_response = start_weights^H * desired_vector`

`raw_response = requested_shared_weights^H * desired_vector`

`scale = conjugate(old_response / raw_response)`

`target_weights = scale * requested_shared_weights`

This makes `target_weights^H * desired_vector = old_response`.

### Independent live-log recomputation

Across 79 consecutive active status records inspected during the live run:

- nine mapped PRNs used `phase_compensated_shared_measured_u1`;
- the tenth source slot had no current PRN assignment and used the common row;
- every seven-PRN PVT-used set was inside the nine compensated rows;
- independently recomputed `applied_weights^H * desired_vector` exactly matched the logged complex response at stored precision;
- maximum response-magnitude drift by PRN was below `6e-14 dB`;
- maximum response-phase drift by PRN was below `3e-13 degrees`;
- the largest recorded continuity residual in the complete valid interval was `7.022e-16`;
- compensation amplitude ranged from about `+5.544` to `+10.032 dB`;
- the active output label was `shared_measured_u1_phase_compensated_prn_fanout`.

This proves exact continuity against each frozen desired vector. It does not prove that a frozen vector remains a perfect model of arbitrary later multipath or hardware drift.

## GNSS outcome inside the valid jammer-on interval

For 154 one-second receiver snapshots from approximately BIN 110.8–266.5 s:

- average tracking C/N0 minimum / median / maximum: `44.960 / 46.180 / 46.720 dB-Hz`;
- PVT-current false snapshots: `0`;
- minimum PRNs used in PVT: `7`;
- minimum tracked PRNs: `9`;
- FIFO source was phase-compensated fanout in all 154 snapshots.

For the nine established PRNs over the stable jammer window (approximately BIN 120–267 s), every PRN had about 7,349–7,350 archived tracking rows over 148 wall-clock seconds. Their individual minimum C/N0 values were 44.50–45.15 dB-Hz, medians were 46.11–46.31 dB-Hz, and recorded cycle slips were zero.

Short one- or two-row zero-C/N0 acquisition candidates for other PRNs were not established tracking channels and are not counted as protected established PRNs.

## RF, DSP, and transport integrity

Inside the valid active interval:

- runtime evidence rows checked: 1,243;
- stream-running false rows: zero;
- LCMV fallback reasons: none;
- any raw or FIFO near-full-scale percentage: 0%;
- USRP stream summary: 32,678 raw chunks, zero overflows, zero timeouts, zero suspected clipping intervals;
- transport heartbeats checked: 157;
- largest interval raw-queue high-water: 8/512;
- lifetime raw-queue high-water: 19/512;
- raw queue rejections: zero;
- GNSS FIFO summary: 32,675 writes, 85,653,086,800 bytes, zero drops;
- byte spread across the ten FIFO sources: zero;
- maximum inter-source lead: one 4,096-sample stripe, as designed.

Measured suppression varied while the selected MUSIC/null peak moved. Over the stable valid section its median was about 26.8 dB; the 5th–95th percentile range was approximately 24.2–32.9 dB. This is measured total-output reduction, not a direct PRN-specific desired-signal suppression measurement.

## Direction finding finding still under audit

The frozen bladeRF bearing was near 355 degrees. During the jammer-on interval the selected MUSIC/null bearing visited clusters near about 214 and 323 degrees. PVT remained healthy, but the physical reason for the two bearing clusters is not yet proven. Possibilities such as multipath, array ambiguity, or multiple RF components must not be stated as fact without an independent geometry/reference measurement.

## Earlier-run finding and why it is not an anti-jam verdict

In the earlier unsynchronized run, C/N0/PVT failures repeated at receiver times separated by exactly 300 seconds. The BIN itself is exactly 300 seconds long. Aligning the receiver clock to the bladeRF file descriptor placed the first collapse at the known bad region near BIN position 267–270 s. The same pattern appeared with jammer/LCMV active and with jammer off/uniform output. Those data are useful for identifying the BIN boundary but are invalid for judging anti-jam preservation.

## Run and revision audit

The local-midnight manifest inventory contains 91 main-working-tree sessions and seven exact-`origin/main` sessions. Forty-three main sessions and one exact session lasted less than 30 seconds and are startup/configuration attempts, not RF survival verdicts. Forty-eight main sessions and six exact sessions lasted at least 30 seconds. The table below lists the sessions used directly to test the C/N0/PVT/beamformer hypotheses; the remaining earlier substantive sessions predate the synchronized BIN-position procedure and have no bladeRF file-position anchor, so they cannot distinguish RF/DSP behavior from the old BIN boundary.

| Tree | Run | Duration s | Direct finding | Classification |
|---|---|---:|---|---|
| main | `20260821T003838.373240Z_pid379673` | 217 | Baseline/angle-label work; no synchronized BIN anchor | Inconclusive for preservation |
| main | `20260821T004435.196491Z_pid396081` | 309 | Gain-50 jammer-on failure reproduced; run crossed a full 300 s file cycle | Invalid as a beamformer-only verdict |
| main | `20260821T010046.260129Z_pid436655` | 278 | Gain-50/source-count-2 comparison; unsynchronized file position | Inconclusive |
| main | `20260821T012459.422989Z_pid436655` | 286 | Gain-60/pad-20 interval survived, but total run duration alone does not prove BIN position | Partial positive evidence only |
| main | `20260821T013055.578594Z_pid436655` | 269 | Desired BIN signal failed near 270 s before jammer onset | Direct BIN-corruption evidence; anti-jam test invalid |
| main | `20260821T013622.351860Z_pid436655` | 259 | Gain-55/pad-20 interval survived before controlled stop | Positive but not file-offset anchored |
| main | `20260821T022940.317436Z_pid599059` | 192 | Jammer-off PVT remained current | Valid no-jammer control |
| main | `20260821T023254.344950Z_pid599059` | 185 | Jammer-off PVT remained current | Valid no-jammer control |
| main | `20260821T025209.477981Z_pid599059` | 308 | PVT remained current until GNSS raw queue filled; one queue rejection and pipeline failure | Transport failure, not RF/null failure |
| main | `20260821T030543.630438Z_pid599059` | 201 | Source setting changed after measured-U1 arm | Invalid test ordering |
| main | `20260821T031030.681090Z_pid599059` | 60 | A/B handoff to exact `origin/main` | Setup/short comparison only |
| exact | `20260821T020930...` | 5 | Startup error 699 | Startup failure; no RF verdict |
| exact | `20260821T021056.076560Z_pid592328` | 200 | PVT later false without a synchronized BIN anchor | Inconclusive |
| exact | `20260821T021417.806882Z_pid592328` | 208 | Jammer-off PVT remained current | Valid no-jammer control |
| exact | `20260821T022134.729214Z_pid592328` | 271 | No PVT-false snapshot, but run approached old BIN boundary | Positive control; boundary margin insufficient |
| exact | `20260821T022607.675386Z_pid592328` | 162 | Jammer-on PVT remained current | Positive jammer interval |
| exact | `20260821T031204.476783Z_pid664706` | 1,227 | Failures repeated on the 300 s BIN cycle | Direct periodic-BIN evidence; post-boundary intervals invalid |
| exact | `20260821T033327.934359Z_pid664706` | 273 | File-position-synchronized valid jammer window; zero PVT loss before corrupt boundary | Clean `origin/main` anti-jam pass |
| current per-PRN | `20260821T040236.597304Z_pid726612` | 219 | Synchronized jammer-off run; zero PVT loss, zero cycle slips, stable C/N0 | Clean per-PRN no-jammer pass |

Revision findings:

- `3419536` is exact `origin/main` and contains the shared measured-U1 phase-safe architecture.
- `f211b1f` removed automatic source-structure/effective-rank veto logic. It did not modify the PRN beamformer or GNSS FIFO mapping.
- `d256e61` changed the LCMV UI/status presentation and C/N0 fallback display. It did not modify the PRN beamformer or GNSS FIFO mapping.
- Therefore the two local commits after `origin/main` cannot explain a change in physical PRN preservation.
- The pre-per-PRN uncommitted tree did modify `backend.py` and `shared_u1_phase_compensation.py`, including phase-only shared-row behavior and acquisition fallbacks. Both this dirty family and exact `origin/main` produced valid-window successes. The logs do not identify one uncommitted edit as the cause of the repeated 270 s collapses.
- Historical session manifests do not record Git commit, dirty-tree fingerprint, authoritative bladeRF readback, or synchronized BIN offset. This is why older sessions are not assigned to an exact source state when the evidence does not support it. Commit/dirty/untracked source fingerprinting is now implemented for future runs; hardware readback and BIN-offset anchoring remain separate requirements.

## Automated test ledger

- Exact clean commit, first targeted run: `88 passed in 0.23 s`
- Files: `tests/test_shared_u1_phase_compensation.py`, `tests/test_headless_ipc.py`, and `tests/test_gnss_sdr_bridge.py`
- Pre-change dirty-tree focused baseline: `103 passed in 0.20 s`
- Per-PRN isolated staging focused suite: `75 passed in 0.22 s`
- Per-PRN isolated staging broad suite: `257 passed, 1 skipped in 5.12 s` (`test_main_defaults.py` was excluded only because a `/tmp` staging path intentionally violates its repository-location assertion)
- Installed current-tree full suite: `292 passed, 1 skipped in 4.59 s`
- Installed per-PRN focused repeat 1: `75 passed in 0.19 s`
- Installed per-PRN focused repeat 2: `75 passed in 0.18 s`
- Installed per-PRN focused repeat 3: `75 passed in 0.19 s`
- Installed 2026-08-22 focused suite after FIR/freeze/response/FIFO fixes:
  `26 passed in 0.35 s`
- Installed 2026-08-22 full suite: `298 passed, 1 skipped in 14.02 s`
- Installed 2026-08-22 full suite after source-provenance coverage:
  `299 passed, 1 skipped in 5.08 s`
- `git diff --check`: pass

New regression coverage includes:

- distinct jammer-off measured rows for different PRNs;
- exact complex-response preservation for each PRN;
- independent covariance LCMV rows that preserve two different PRNs and null the same measured jammer vector;
- adoption of a newly mapped PRN while jamming;
- a changed/moving desired vector without a response jump;
- a rolling vector window that completely forgets an old spatial vector after enough new quality measurements;
- non-collinear row rendering through the real synchronized FIFO matrix path.

## Proven defects

1. The live test process previously did not synchronize bladeRF file position zero before starting GNSS-SDR, allowing the receiver test to enter the corrupt BIN region unexpectedly.
2. The anti-jam experiment manifest recorded bladeRF gain 50 dB while the authoritative bladeRF CLI readback was 60 dB.
3. Historical manifests do not fingerprint the Git commit plus dirty tree or record an authoritative bladeRF readback/file offset. Source-tree fingerprinting is fixed for future runs; transmitter readback/file position still require access to the bladeRF host.
4. The old protection architecture used one shared spatial row for all PRNs. Per-PRN scalar phase rotation could not make that one spatial row match several independent PRN vectors.
5. The old desired-vector monitor used an unlimited lifetime projector average, preventing it from following a sufficiently changed current vector. The current window is the latest 12 quality-passed measurements.
6. The monitor formerly correlated pre-filter raw IQ at an uncompensated
   post-filter counter; the missing 27-sample FIR delay produced wrong-code,
   noise/interference-dominated desired vectors.
7. A dirty shared-row change preserved PRN response phase but discarded the
   exact amplitude scale, causing 3.91–24.41 dB modeled pre-to-post losses.
8. Repeated covariance/context updates restarted one-second transitions before
   they could complete.
9. Ten 4,096-sample FIFO stripes created 80 writes per chunk and could not
   sustain integrated TramiqSDR load. Full-source-chunk writes fixed the
   measured failure interval.

## Not yet proven

- A physical sensor/GPIO did not record the jammer switch; jammer state was inferred from the measured RF power/eigenspectrum change.
- The run did not dump raw pre-beamformer and post-beamformer IQ to disk, so an offline byte-for-byte replay of the exact live samples is not available.
- The physical cause of the two jammer/null bearing clusters is not proven.
- The app's configured jammer attenuation was 50 dB, but a digital readback from the physical attenuator does not exist.
- The corrected independent per-PRN covariance-LCMV path is proven by repeated
  synthetic covariance/transition tests and by live no-jammer vector
  coherence, but not yet by a synchronized live jammer-on RF run.
- A desired vector cannot be estimated before a new PRN has acquired enough tracking/code-phase information. Such an unmapped/new channel necessarily uses the common protected acquisition row until it passes the measurement threshold.

## Retest policy

For every future RF run:

1. Stop GNSS-SDR.
2. Stop and restart bladeRF playback.
3. Read back bladeRF frequency, sample rate, RF bandwidth, gain, file, format, and TX state.
4. Start the receiver after bladeRF file position zero.
5. Record a synchronized file-position anchor.
6. Obtain healthy PVT before jammer activation.
7. Detect jammer activation from RF evidence, not chat timing.
8. Stop early enough that no analyzed observation can reach BIN position 270 s; use 260 s as the operational cutoff until the file is replaced.
9. Repeat phase, per-PRN, transport, clipping, FIFO, and PVT audits.
10. Rerun the critical automated tests multiple times and record every result here.
11. For the next jammer-on validation, require `active_per_prn_lcmv_source_count` to cover every mapped established PRN, verify each row's PRN response and measured-jammer null residual, and stop without using any BIN position at or above 260 s.
12. Do not substitute receiver elapsed time for transmitter file position. If
    transmitter SSH/readback is unavailable, the run lacks an exact BIN anchor
    and cannot be used past the earliest plausible 260 s transmitter position.
