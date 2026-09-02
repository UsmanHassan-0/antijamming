# Shared measured-U1 optimization and provenance

## Scope and immutable baseline

This audit started from local and remote `main` commit
`435945af4a174392fbeedfc0a3b09d4f7456bdb5` (`Add shared measured-U1
phase-continuous GNSS fanout`). It changes the anti-jamming repository only.
No source, build product, or configuration below `gnss-sdr/` is changed by this
audit, and no USRP, bladeRF, SynthUSB, or jammer was powered or commanded.

The archived G10-rising experiment used ten pinned GPS L1 C/A PRNs including
G10, a 5 degree PVT mask, USRP gain 45 dB, recorded bladeRF software gain 29,
and recorded jammer attenuation 50 dB. Those values remain in the archived run
logs as experiment provenance; the executable overlay was removed after it
proved that a BIN-specific PRN list made the production behavior inconsistent.
The product profile now uses dynamic GNSS-SDR channel-to-PRN assignment.

## Exact active signal path

1. One timed four-channel UHD receive produces `X`, shaped `4 x N`.
2. The configured static complex channel correction `C` is applied using the
   same convention as the GNSS output, `y[n] = w^H C x[n]`.
3. One calibrated covariance is computed for each DoA work item:
   `R = X_cal X_cal^H / N`.
4. One eigendecomposition of that exact `R` supplies the eigenvalues, MUSIC
   noise subspace, and measured dominant vector `u1`. Bartlett uses the same
   `R`. Healthy-reference capture, LCMV, spatial diagnostics, and full-angle
   logging reuse these objects rather than recomputing them.
5. Both selectable LCMV constraint choices call the same diagonal-loaded
   covariance solver:

   ```text
   w = R_loaded^-1 Cc (Cc^H R_loaded^-1 Cc)^-1 f
   ```

   `covariance_lcmv_ideal` supplies an ideal jammer steering vector;
   `covariance_lcmv_measured_u1` supplies measured `u1`. There is no identity
   covariance, constraint-only, covariance-free, or independent per-PRN LCMV
   implementation.
6. When shared phase fanout is enabled, each accepted measured-U1 LCMV update
   is the one shared spatial row. The PRN monitor uses the receiver's tracking
   sample counter, Doppler, and GPS C/A code to correlate the retained raw
   four-channel IQ. It rejects correlations spanning a weight change, requires
   at least 6 dB prompt-to-wrong-code separation, and aggregates phase-invariant
   projectors before publishing a PRN spatial vector.
7. For desired PRN vector `a_k`, previous row `w_old`, and requested shared row
   `w_shared`, the code computes:

   ```text
   r_old = w_old^H a_k
   r_new = w_shared^H a_k
   gamma_k = conjugate(r_old / r_new)
   w_k = gamma_k w_shared
   ```

   Therefore `w_k^H a_k = r_old` to floating-point tolerance. The scalar does
   not move or fill the shared null. Every later accepted covariance update is
   rephased; it is not frozen at the first jammer-on solution.
8. Because all `w_k` rows are scalar multiples of one shared row, realtime IQ
   applies the four-channel beam once and fans out `conjugate(gamma_k)` scalar
   copies. A general matrix multiply remains only for the non-collinear
   jammer-off transition. GNSS-SDR receives one FIFO per source slot.
   Production sources are dynamic channel slots: GNSS-SDR may acquire any GPS
   L1 C/A PRN, and a changed channel assignment discards the old PRN phase
   state before learning the new one. Pinned sources remain a unit-test
   facility only.
9. A later hardware failure audit superseded this document's original
   `GNSS-SDR.synchronize_signal_sources=true` configuration. That option made
   GNSS-SDR's 20 ms sample-counter `gr::sync_decimator` consume all ten source
   branches; one temporarily unscheduled branch stopped the receiver clock,
   every FIFO reader, and eventually the Python raw queue. The deployed
   renderer now sets `GNSS-SDR.synchronize_signal_sources=false`, so the sample
   counter follows conditioner zero. This does not permit silent sample loss:
   the producer writes equal sample counts in fair 4,096-sample stripes,
   requires every FIFO to finish each stripe, bounds a stalled reader at 250
   ms, and reports source-byte spread and maximum lead. Tracking channels carry
   their own sample counters for observable alignment.

The monitor records GNSS-SDR's reported carrier phase for audit, but it does
not need that absolute common phase to recover the relative four-channel PRN
vector. Doppler wipeoff and C/A despreading leave the same common scalar on all
four channels; the phase-invariant projector removes it. A synthetic one-code-
period test proves this with an arbitrary carrier phase and more than 40 dB
wrong-code separation.

## GNSS-SDR source boundary and exact post-vendoring changes

The array calibration, shared measured-U1 beam, `gamma_k` calculation,
per-PRN continuity transition, and scalar fanout are all implemented in the
anti-jamming Python runtime **before** FIFO writes. GNSS-SDR receives already
combined complex64 streams. It does not calculate the array weights, measured
`u1`, null, or continuity scalar.

Since the GNSS-SDR tree was first vendored in anti-jamming commit `4f352d0`,
exactly six GNSS-SDR C++ files have changed, in two commits:

- Commit `78d81f9`:
  - `gps_l1_ca_telemetry_decoder_gs.cc` treats every parity-valid subframe with
    continuous HOW as valid transport even when it publishes no new navigation
    object. The former return path counted valid SF4/SF5 and partial ephemeris
    transitions as decode failures; after three, TOW was cleared and the
    receiver developed a deterministic navigation/PVT gap.
  - `rtklib_pvt_gs.cc` applies a newly active channel's accumulated
    carrier-phase ambiguity offset before the user PPP solver's first attempt,
    rather than only after a valid solution. This breaks the circular condition
    in which PPP required an aligned phase before the code performed alignment.
    It also emits ephemeris, ambiguity-initialization, and PVT-pipeline audit
    records.
  - `rtklib_solver.cc` emits RTKLIB error and residual records on captured
    stdout so the anti-jamming run archive can tie PVT state to the measurements
    used or rejected.
- Commit `435945a`:
  - `gnss_sdr_sample_counter.{h,cc}` accepts a configurable input count instead
    of exactly one.
  - `gnss_flowgraph.cc` adds the optional
    `GNSS-SDR.synchronize_signal_sources` configuration, validates equal item
    sizes, and can connect all conditioner outputs to that counter. The feature
    remains in the vendored source but is disabled in the product after the
    later backpressure failure described above.

The GNSS-SDR carrier-phase ambiguity correction is distinct from the
anti-jamming per-PRN spatial phase compensation. The former aligns a receiver
observable with pseudorange for PPP; the latter preserves each PRN's complex
array response while a shared beam/null changes.

## Removed work and retained evidence

The audit removes or avoids the following unnecessary work without removing
the observables needed to diagnose GNSS tracking:

- repeated covariance/eigendecomposition for source rank, MUSIC, Bartlett,
  healthy capture, LCMV, spatial diagnostics, and full-angle logging;
- ten separate four-channel matrix products when all PRN rows are collinear;
- per-IQ-chunk construction of large PRN status dictionaries and repeated
  desired-vector snapshots; status remains at 1 Hz;
- duplicate spatial-vector and complete LCMV-pattern records in `analysis.log`;
- repeated aliases and pre-sorted copies of the same MUSIC/Bartlett arrays;
- duplicated transition, weight, power, and protobuf PVT objects in automatic
  runtime evidence;
- the unused PRN display-hold store and its never-called merge routine—the
  chart now has one rule only: show a current tracking PRN when a current,
  finite, positive C/N0 measurement exists, and clear it when that measurement
  is absent;
- orphan GUI format/range helpers, an unused PVT accuracy-summary formatter,
  unused observable/output/file-age helpers, and duplicate IQ/LCMV response
  wrappers that had no production callers.

An AST reachability pass over all 103 Python source/test files was followed by
an explicit symbol search for every removed helper. It found no remaining call
site or stale import. CEP remains cumulative, and current latitude, longitude,
altitude, DOP, carrier/code observables, and PVT state remain available; the
removed accuracy formatter never supplied the GUI or the durable audit.

The dedicated tracking archive remains detailed. It retains prompt I/Q,
prompt magnitude/phase, C/N0, Doppler, carrier phase in radians and cycles,
code phase in samples and seconds, tracking counter, acquisition/symbol/word/
pseudorange validity, PLL lock, and cycle-slip flags. Removing those fields
would make carrier/code/bit/word provenance impossible, so they are not treated
as disposable logging.

Projected from archived session
`logs/runs/20260816T033902.832348Z_pid871220`:

- 422 full-angle records: 313.94 MiB to 134.81 MiB (57.1% reduction);
- duplicate spatial diagnostics removed from `analysis.log`: 6.85 MiB;
- total projected `analysis.log` reduction: 58.0%;
- automatic runtime evidence: 63.85 MiB to 39.02 MiB (38.9% reduction).

Current readers retain compatibility with legacy duplicated runs.

## Deterministic performance evidence

Benchmarks used deterministic NumPy input on this DGX, 12 warm-up iterations,
the median of 101 measured iterations, and one BLAS thread
(`OPENBLAS_NUM_THREADS=OMP_NUM_THREADS=MKL_NUM_THREADS=1`). They compare equal
numerical outputs, not different algorithms:

| Hot path | Previous median | Current median | Result |
| --- | ---: | ---: | ---: |
| DoA plus the formerly repeated covariance/eigen work, 4 x 32768 IQ and 721 angles | 4.9415 ms | 1.2983 ms | 73.7% lower |
| Ten-PRN four-channel output, general matrix versus one shared beam plus scalars | 0.6432 ms | 0.1331 ms | 4.83x faster |
| Ten-PRN phase-bank call with full status versus status-free IQ hot path | 238.35 us | 23.09 us | 10.32x faster; full status still emitted at 1 Hz |

The equality tests use the original independent MUSIC, Bartlett, covariance,
and matrix-product formulas. MUSIC/Bartlett/eigen outputs agree at relative
tolerances between `1e-13` and `1e-14`; the complex64 fanout agrees within
`rtol=5e-6, atol=2e-6`.

## Archived live evidence and its limit

The archived run contains 206,543 tracking rows and a carrier-phase archive.
All 3,101 automatic snapshots contain complete applied weight state. Its
transport summary reports queue high-water 59/512 (11.5%), zero raw
rejections, zero RX overflows, and zero RX timeouts. The final snapshot at
session elapsed 466.142 s / receiver time 463 s has current PVT, ten tracking
satellites, ten used satellites, and average C/N0 42.678 dB-Hz.

This run is not proof of uninterrupted PVT. PVT first became current at session
elapsed 36.784 s, then was non-current in 164 snapshots from 288.684 through
312.570 s (receiver time 286 through 310 s), while 9-10 satellites continued
tracking at 43.321-43.763 dB-Hz. `gnss_handoff.log` shows RTKLIB position errors
and multiple loss-of-lock events followed by GNSS time moving backward from
`06:02:22.xx` to `05:57:36.xx`. This coincides with the repeated five-minute IQ
file boundary and is evidence of a source-time discontinuity, not queue
overload. It recovered afterward.

The audit calculates 21.468 dB suppression of automatically inferred
added-scene covariance over 1,255 snapshots. It must not be called proven
jammer-only suppression: the archive contains no explicit physical
`jammer_on`/`jammer_off` markers. A future physical run must mark or otherwise
establish ground truth for jammer/bladeRF state, movement, LCMV re-arm, and
strong-J/S cases before those claims are closed.

## Verification gates

The offline gate covers:

- exact shared null and per-PRN response preservation;
- every later measured-U1 covariance update;
- every intermediate jammer-off ramp chunk;
- scalar fast-path equality and non-collinear transition fallback;
- GPS C/A code/Doppler correlation and phase-invariant vector recovery;
- dynamic and pinned multi-source GNSS-SDR configuration, safe PRN
  reassignment, and FIFO write ordering;
- covariance/MUSIC/Bartlett numerical equivalence;
- prompt/code/carrier/symbol/bit/word/C/N0/PVT audit fields;
- non-duplicated logs and compact runtime evidence;
- source inspection forbidding an independent per-PRN covariance solver.

Passing deterministic tests proves the code invariants and numerical
equivalence. It does not replace the pending physical jammer scenarios listed
above.
