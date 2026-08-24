# Jammer-first spatial acquisition audit — 2026-08-23

## Outcome

The experimental anti-jamming worktree now has a cold-start GPS L1 C/A acquisition path that does not require a pre-existing PVT solution or a previously tracked satellite vector.

It searches the jammer-null spatial subspace with one common PRN, code-phase, and Doppler hypothesis across every remaining spatial degree of freedom. A candidate is not allowed to become a desired-signal steering vector until four separated observations agree in all of these ways:

- correlation peak strength;
- carrier Doppler;
- absolute C/A-code phase trajectory;
- the physical code-Doppler/carrier-Doppler relationship; and
- projected spatial-vector coherence.

This closes the architectural gap where per-PRN preservation needed a PRN vector before GNSS-SDR had acquired that PRN. It does not yet prove physical jammer-first reacquisition because the live tests in this audit did not contain a proven bladeRF desired signal.

The detector now also has a separate broadband path. Spatial rank plus calibrated excess power creates only a candidate. It cannot publish a null until the raw-array PCPS monitor has accumulated four separated observations and found no physically consistent GPS PRN. A valid PRN is an explicit veto. This distinction is necessary because a GNSS simulator waveform and a broadband jammer can both look like one coherent spatial source.

## Why the stock acquisition setting was insufficient

GNSS-SDR's documented PCPS acquisition searches code phase and Doppler on one input stream. Its `max_dwells` option adds noncoherent time dwells to that stream; it does not combine two simultaneous outputs from a jammer-null spatial subspace. The official acquisition description and implementation parameters are documented at <https://gnss-sdr.org/docs/sp-blocks/acquisition/>.

The chosen extension follows the array-acquisition principle described by Arribas, Fernández-Prades, and Closas: spatially mitigate interference and then perform a conventional acquisition, while preserving the common acquisition hypothesis across the spatial data. See <https://link.springer.com/article/10.1186/1687-6180-2013-143>.

The code-delay gate is based on the physical relationship between carrier Doppler and code Doppler. For GPS L1 C/A, the L1 carrier contains 1,540 carrier cycles per C/A-code chip; a technical treatment is available at <https://www.mitre.org/sites/default/files/pdf/06_1183.pdf>.

## Implemented signal path

1. The X300 receives four phase-calibrated channels.
2. The detector estimates the active spatial mode. A concentrated line can take the existing narrowband path immediately. A broadband high-power mode enters classification only.
3. Broadband classification searches the un-nulled array sensors four times. Any physically consistent GPS PRN vetoes automatic jammer activation.
4. After confirmed interference, an SVD produces a nonredundant, equal-noise-gain basis orthogonal to the measured jammer vectors.
5. A fresh 600 kHz FIR notch is used only for the narrowband mode. Broadband protection does not invent a notch frequency.
6. GPS L1 C/A PCPS searches all 32 PRNs. Correlation powers are summed noncoherently across the available spatial outputs and five 1 ms dwells.
7. The prompt correlations estimate a projected desired spatial vector for each PRN.
8. Four separated observations must pass the physical-consistency tests before that vector is published.
9. Once GNSS-SDR tracks the PRN, the existing quality-gated tracking vector supersedes the acquisition-stage vector.

The worker is outside the lossless GNSS FIFO path. Its input queue is deliberately latest-only. Replaced snapshots are logged as `latest_only_replacements`; they are not RF overflows, FIFO rejections, or lost GNSS output samples.

## Code changed in the experimental worktree

- `src/antijamming/gnss/cold_start_acquisition.py`
- `src/antijamming/gnss/cold_start_acquisition_monitor.py`
- `src/antijamming/gnss/shared_u1_phase_compensation.py`
- `src/antijamming/dsp/jammer_detection.py`
- `src/antijamming/runtime/backend.py`
- `src/antijamming/config/loader.py`
- `src/antijamming/config/schemas/runtime.py`
- `configs/antijamming/x300_realtime.json`
- `tests/test_cold_start_spatial_acquisition.py`
- `tests/test_cold_start_spatial_acquisition_monitor.py`
- `tests/test_cold_start_jammer_detection.py`
- `tools/validate_jammer_first_spatial_acquisition.py`
- `tools/capture_jammer_first_consistency.py`
- `tools/validate_measured_jammer_attenuation_boundary.py`

No commit or merge was made. The work remains isolated in `/home/qvise/antijamming-per-prn-validation-20260822` on branch `per-prn-fifo-resilience-validation-20260822`.

## Deterministic known-truth proof

Artifact: `logs/diagnostics/jammer_first_known_truth_20260823.json`

- Two jammer dimensions occupied about 84.81% and 15.07% of array covariance power.
- Jammer-subspace estimation left a normalized truth residual of 0.000283.
- Desired PRN 14 was recovered at its exact injected Doppler and code phase with a 12.813 dB peak-to-median result.
- Desired PRN 22 was recovered at its exact injected Doppler and code phase with an 8.188 dB peak-to-median result.
- The largest false candidate was 5.914 dB.
- The desired jammer-to-signal ratios were approximately 51.13 and 53.06 dB.

An ordinary raw-sensor acquisition placed PRN 14 in the wrong cell. The common-hypothesis spatial search recovered both injected PRNs exactly.

## Real-IQ negative-control proof

Artifacts:

- `logs/diagnostics/jammer_first_consistency_negative_control_20260823.json`
- `logs/diagnostics/jammer_first_consistency_negative_control_20260823.npz`

The external scene contained a stable line near -372.070 kHz and produced repeatable-looking PCPS peaks. A one-shot threshold would therefore have been unsafe. For example, a false PRN 4 candidate had about 8.05 dB peak strength, stable -8.25 kHz Doppler, and 0.991 spatial coherence, but its measured code-phase motion was about -3,820.8 samples/s while its carrier Doppler predicts only about +20.95 samples/s.

After applying the repeated physical-consistency tests, zero of 32 PRNs were accepted in both the spatial-only and notch-plus-spatial searches.

## Automated test evidence

The final application environment result was:

`360 passed, 1 skipped in 6.70 s`

The added tests cover:

- rank-two jammer-first recovery of two exact known PRNs;
- acceptance of a physical code/carrier trajectory;
- rejection of a repeatable nonphysical jammer correlation;
- cooperative cancellation of PCPS;
- orthogonality, rank, and noise gain of the jammer-null basis;
- no vector publication before four consistent observations;
- publication of only the accepted PRN vector; and
- clean cancellation of an in-progress monitor search;
- broadband high-power candidate creation without treating it as a confirmed jammer;
- GNSS consistency veto after four classifier observations; and
- live transition from broadband classification to jammer-null acquisition without a frequency notch.

## Measured attenuation-boundary replay

Artifact: `logs/diagnostics/measured_jammer_attenuation_boundary_20260823.json`

This replay uses the measured old-file bladeRF capture, measured pad-20 jammer capture, and measured receiver-noise capture. The desired capture was scaled from gain 55 to gain 66 by the measured 10.576 dB difference.

- At 20 dB jammer attenuation, input J/S was +19.512 dB. Neither raw-sensor nor measured rank-one-null acquisition accepted a PRN. The source was a broadband high-power candidate: 95.70% dominant spatial power and 30.76 dB above the noise reference.
- At 50 dB jammer attenuation, input J/S was -10.488 dB. Raw acquisition accepted G03, G04, G07, G08, G09, G14, G16, and G27. The measured rank-one jammer null retained G03, G04, G08, G09, G14, G16, and G27.
- The 50 dB case was not mislabeled as a high-power jammer candidate.

This is a linear replay of separately measured captures, not a simultaneous conducted RF proof.

## Live X300 evidence

### Run 20260823T060605.759954Z_pid510440

- Runtime: approximately 90 seconds.
- Monitor evaluations: 42.
- Accepted cold-start PRNs: 0.
- GNSS raw queue high-water: 7/512.
- GNSS output queue high-water: 2/64.
- FIFO rejections: 0.
- UHD overflows: 0.
- UHD timeouts: 0.
- Suspected clipping intervals: 0.
- Error log size: 0 bytes.

GNSS-SDR reported transient tracking candidates but no stable bars, no observations used for PVT, and no PVT. The independent monitor correctly refused to turn those transient candidates into desired steering vectors.

### Run 20260823T061318.745644Z_pid517298

- Runtime: approximately 45 seconds.
- Monitor evaluations: 14.
- Accepted cold-start PRNs: 0.
- Monitor submissions: 4,911.
- Intentional latest-only replacements: 1,916.
- Monitor worker stopped cleanly: true.
- GNSS raw queue high-water: 5/512.
- GNSS output queue high-water: 1/64.
- FIFO rejections: 0.
- UHD overflows: 0.
- UHD timeouts: 0.
- Suspected clipping intervals: 0.
- Error log size: 0 bytes.

The corrected counter names separate diagnostic snapshot coalescing from the lossless FIFO health counters.

### Run 20260823T064716.840504Z_pid563342 — receiver-only current scene

- The ordinary narrowband path activated on a source at -372.070 kHz.
- Dominant spatial fraction: 92.61%.
- Measured covariance-output reduction: 7.08 dB.
- Cold-start evaluations: 11; accepted PRNs: 0.
- GNSS candidates had no nav word or PVT.
- Raw queue high-water: 7/512; output queue high-water: 2/64.
- FIFO rejections, UHD overflow, UHD timeout, clipping, and error-log bytes: 0.
- The acquisition worker stopped cleanly.

### Run 20260823T064950.850646Z_pid565665 — Strix L1/L5 gain 66 started first

Both exact bladeRF serials were running before receiver start and were stopped after the bounded 45-second receiver run. The RF scene remained dominated by the same -372.070 kHz source. All GNSS-SDR rows had zero C/N0, zero prompt magnitude, invalid acquisition, invalid nav word, and no phase lock. The independent spatial monitor accepted zero PRNs in 19 evaluations.

The receiver therefore did not observe a defensible bladeRF GPS signal through the current physical route. This run is negative physical-route evidence, not an anti-jamming-algorithm failure. Queue high-water was 7/512 raw and 2/64 output, with zero FIFO rejection, UHD overflow, UHD timeout, clipping, or worker-stop error.

The median calibrated array power was 0.032893 with the transmitters off and 0.034402 with them on, a difference of only 0.197 dB across separate runs. The same dominant source remained at about 92.5% of covariance power. This small non-simultaneous difference is not enough to attribute RF energy to the bladeRF transmission, especially because the external scene itself fluctuates.

### Run 20260823T065142.649666Z_pid568742 — forced broadband-path diagnostic

This run used an isolated diagnostic overlay that raised only the narrowband thresholds so the new broadband classifier could be exercised against the same current scene.

- Five raw-array classification evaluations ran with no null and no notch.
- No physically consistent GPS PRN was found, so there was no GNSS veto.
- Broadband protection activated after the classifier decision.
- The acquisition context changed to the measured rank-one jammer null with no frequency notch.
- Six post-null evaluations completed.
- Raw and output queue high-water were both 1; FIFO rejections were 0.
- UHD overflow, timeout, error-log bytes, and worker-stop failures were 0.

This proves the live classifier-to-broadband-null lifecycle. It does not prove desired-signal recovery because the physical bladeRF path was absent.

## What is proven

- The stock one-stream acquisition setting cannot provide the missing spatial integration by increasing `max_dwells`.
- The new common-hypothesis spatial PCPS recovers exact known signals behind a rank-two, greater-than-50 dB simulated jammer scene.
- A one-shot acquisition threshold is unsafe on the measured external scene.
- The physical-consistency gate rejects all measured false candidates in the current no-proven-desired scene.
- The broadband classifier waits for repeated raw-array GNSS checks and can transition to a no-notch jammer-null context live.
- Measured replay distinguishes the unusable 20 dB attenuation case from the recoverable 50 dB case.
- The background worker does not overload the RF/FIFO path in the live runs.
- Stop cancels and joins the acquisition worker cleanly.

## What is not yet proven

- A physically transmitted bladeRF GPS L1 C/A signal has not yet been acquired with the jammer already present.
- The current Strix-to-X300 physical path did not produce a measurable desired-signal step, despite both configured bladeRF processes running first.
- Live per-PRN preservation after a cold acquisition has therefore not yet been demonstrated end to end.
- The present thresholds are validated by deterministic truth and one external negative-control scene, not by a broad RF campaign.
- The implementation currently covers GPS L1 C/A acquisition only.
- The work has not been merged into `main`.

## Required next physical test

Use one bounded run shorter than the known 270-second BIN-file limit:

1. Record at least 20 seconds of jammer-first negative control.
2. Start the known bladeRF GPS L1 C/A transmission without restarting the receiver.
3. Require a measured RF step and preserve its exact transmission start timestamp.
4. Require four consistent spatial-PCPS observations for actual PRNs.
5. Require GNSS-SDR acquisition, stable C/N0, navigation observations, and PVT.
6. Move or change the desired transmitter only after the first proof, then verify that quality-gated per-PRN vectors update instead of freezing.
7. Require zero RF overflow, timeout, clipping, FIFO rejection, and worker-lifecycle error.

Until that test passes, this work is a safe and evidence-backed acquisition correction, not a completed live anti-jamming claim.
