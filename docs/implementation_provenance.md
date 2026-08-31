# Implementation Provenance

This map distinguishes implemented code from external definitions, published
algorithm lineage, measurements, assumptions, and proposals. It is not a claim
that citing a paper or specification proves this implementation correct.

## Algorithm and interface map

| Area | Classification | Concrete implementation and boundary | Source |
| --- | --- | --- | --- |
| MUSIC DoA spectrum | Paper-derived algorithm family | `dsp/doa/music.py` estimates a per-chunk covariance, takes its Hermitian eigendecomposition, treats the configured strongest eigenvectors as signal subspace, and scans the complementary noise-subspace projection using the repository's fixed four-element square URA. Source-count diagnostics, normalization floors, array geometry, and angle convention are project choices. | R. O. Schmidt, “Multiple Emitter Location and Signal Parameter Estimation,” IEEE TAP 34(3), 1986, [DOI 10.1109/TAP.1986.1143830](https://doi.org/10.1109/TAP.1986.1143830) |
| Bartlett spectrum | Implemented conventional method; exact paper provenance not established | `dsp/doa/music.py` evaluates `aᴴRa/(aᴴa)²` on the same URA scan. It is retained as an angle-by-angle diagnostic, not proof that the MUSIC peak is physically correct. | No repository design source was found; classify as conventional method, not a claimed adaptation of a specific paper |
| Covariance LCMV | Paper-lineage plus project-specific closed-form implementation | `dsp/beamforming/lcmv.py` applies diagonal loading, solves `R⁻¹C`, then uses `R⁻¹C(CᴴR⁻¹C)⁻¹f`, with condition-number and weight-norm guards. Frost is the classic constrained adaptive-array lineage, but the repository code is a batch closed-form solve—not Frost's iterative constrained LMS algorithm. | O. L. Frost III, “An Algorithm for Linearly Constrained Adaptive Array Processing,” Proceedings of the IEEE 60(8), 1972, [DOI 10.1109/PROC.1972.8817](https://doi.org/10.1109/PROC.1972.8817) |
| Measured-vector constraints | Project-specific application | The only null solver uses current measured dominant U1 and the frozen measured healthy-U1 preserve vector. Loading, conditioning and norm limits are project choices. The angle-derived ideal-null solver is removed, not disabled. Uniform remains a safety state. Steering-model scans and coherence remain diagnostics, not physical suppression measurements. | Code, runtime profile, progress tracker, and retained historical records |
| Shared measured-U1 fanout | Project-specific | `gnss/shared_u1_phase_compensation.py` uses one shared spatial LCMV vector. Each tracked PRN can receive a complex scalar intended to preserve its previous complex response; scalar multiplication does not create an independent spatial solution. The monitor currently measures only GPS L1 C/A tracking entries (`system G/GPS`, signal `1C`, PRNs 1–32). | Repository design and `audits/shared_u1_optimization_provenance.md` |
| GPS L1 C/A replica | Standard-defined signal, local implementation | The Shared-U1 monitor implements the 1.023 Mcps, length-1023 C/A generator and PRN 1–32 phase selections used for local despreading. The applicable public L1/L2 interface definition is IS-GPS-200. The current bounded tests check local sequences/behavior; they do not certify every signal-interface requirement. | [GPS interface specifications](https://www.gps.gov/interface-control-documents-icds-interface-specifications-iss), [IS-GPS-200N PDF](https://archive.gps.gov/technical/icwg/IS-GPS-200N.pdf), [GPS PRN assignments](https://www.gps.gov/pseudorandom-noise-code-assignments) |
| GNSS-SDR tracking monitor | Upstream-interface-defined | The bridge deserializes GNSS-SDR `Gnss_Synchro` Protocol Buffer messages over UDP. PRN, signal, C/N0, Doppler, counters, and carrier fields are receiver outputs; the anti-jamming app does not independently establish their truth. | [GNSS-SDR Monitor documentation](https://gnss-sdr.org/docs/sp-blocks/monitor/) |
| GNSS-SDR PVT and NMEA | Upstream-interface-defined; NMEA format externally standardized | The bridge consumes GNSS-SDR PVT Protocol Buffer data and NMEA output. Tests with generated/local messages establish parser behavior, not full NMEA or live-PVT conformance. | [GNSS-SDR PVT documentation](https://gnss-sdr.org/docs/sp-blocks/pvt/) |
| Static phase/complex-gain calibration | Project-specific, measured-input transformation | `dsp/phase/alignment.py` validates a persisted vector and applies it per channel. Phase-only and complex-gain modes are implemented; the checked-in artifact is a conducted measurement and is not an assumed full OTA antenna-manifold calibration. | Calibration artifact, code, and dated run records |
| RF hardware boundary | Measured or assumed only in retained external evidence | Product code and active analysis do not accept bladeRF distance/gain, jammer distance/attenuation, expected bearing, bench geometry, or an RF-link-budget model. Historical values remain in dated audits and require field-level measured/derived/assumed labels. | `01_rf_hardware_boundary.md`, `hardware.md`, and retained audits |
| Jammer confidence and thresholds | Implemented heuristics with measured tuning still required | Input-power/covariance gates exist, but angle motion alone is not physical jammer truth. Threshold behavior without labeled OTA runs is software behavior, not anti-jamming effectiveness proof. | Runtime code, profile, and progress tracker |
| GSC and desired multi-vector subspace | Proposed | These are open research/design options and are not current product implementations. | `docs/progress_tracker.md` |

## How to extend this map

For every material algorithm or protocol change, record:

1. the exact source file and formula/interface used;
2. whether it is implemented, standard-defined, paper-derived, measured,
   derived, assumed, or proposed;
3. deviations and project-specific constants;
4. the focused regression or retained run that exercises it; and
5. what remains unverified.

Reading or citing a source establishes provenance only. Verification still
requires tests for the implementation and, where applicable, attached-hardware
or OTA evidence.
