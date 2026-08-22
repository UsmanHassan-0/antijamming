"""Runtime constants for the GNSS-SDR FIFO bridge."""

# Keep GPS L1 explicit because the bridge validates capture bandwidth against
# the receiver signal rendered into the GNSS-SDR config.
GPS_L1_CA_FREQ_HZ = 1_575_420_000.0

# The bundled GNSS-SDR GPS_L1_CA.h defines the C/A code rate as 1.023 Mcps.
# BPSK(1) has its first spectral null at +/- one code rate, so its two-sided
# main lobe is 2.046 MHz. Preserve 277 kHz on each side for Doppler, oscillator
# error, and filter roll-off. Keep these as physical bandwidths: changing the
# runtime sample rate must not silently change which part of GPS L1 C/A is fed
# to GNSS-SDR.
GPS_L1_CA_CODE_RATE_HZ = 1_023_000.0
GPS_L1_CA_NULL_TO_NULL_BANDWIDTH_HZ = 2.0 * GPS_L1_CA_CODE_RATE_HZ
GPS_L1_CA_PROCESSING_BANDWIDTH_HZ = 2_600_000.0
GNSS_INPUT_FILTER_STOPBAND_HZ = 3_000_000.0
GNSS_INPUT_FILTER_PASSBAND_RIPPLE_DB = 0.5
GNSS_INPUT_FILTER_STOPBAND_ATTENUATION_DB = 40.0

# Freq_Xlating_Fir_Filter in lowpass mode passes these physical parameters to
# GNU Radio firdes.low_pass(). GNU Radio derives the taps; the rendered config
# deliberately has no number_of_taps setting. These values retain the requested
# +/-1.3 MHz GPS passband and reach the stopband by +/-1.5 MHz at 4 MS/s.
GNSS_INPUT_FILTER_CUTOFF_HZ = 1_385_000.0
GNSS_INPUT_FILTER_TRANSITION_WIDTH_HZ = 175_000.0

# GNU Radio's firdes.low_pass() uses the Hamming-window attenuation estimate
# below when GNSS-SDR renders Freq_Xlating_Fir_Filter in low-pass mode.  Keep
# the calculation here because the per-PRN monitor observes IQ before this FIR
# while tracking_sample_counter is reported after it.
GNSS_INPUT_FILTER_HAMMING_ATTENUATION_DB = 53.0


def gnss_input_filter_tap_count(sample_rate_hz: float) -> int:
    """Return the odd Hamming FIR length produced by GNU Radio firdes."""

    sample_rate = float(sample_rate_hz)
    if sample_rate <= 0.0:
        raise ValueError("sample_rate_hz must be positive")
    taps = int(
        GNSS_INPUT_FILTER_HAMMING_ATTENUATION_DB
        * sample_rate
        / (22.0 * GNSS_INPUT_FILTER_TRANSITION_WIDTH_HZ)
    )
    taps = max(1, taps)
    if taps % 2 == 0:
        taps += 1
    return taps


def gnss_input_filter_group_delay_samples(sample_rate_hz: float) -> int:
    """Return the linear-phase FIR group delay in samples."""

    return (gnss_input_filter_tap_count(sample_rate_hz) - 1) // 2

# GNSS-SDR tracking monitor UDP exposes current C/N0. The receiver view
# qualifies non-PVT bars from decoded telemetry plus stable C/N0 history;
# GNSS-SDR still decides actual loss of lock.
PRN_CNO_STABILITY_WINDOW = 20
PRN_CNO_MIN_STABLE_DB_HZ = 25.0
PRN_CNO_MAX_STDEV_DB = 0.75
PRN_CNO_MAX_PEAK_TO_PEAK_DB = 2.0
PRN_CNO_REQUIRED_STABLE_WINDOWS = 1
PRN_CARRIER_LOCK_THRESHOLD = 0.7
SKY_GEOMETRY_TIMEOUT_S = 15.0
USED_IN_FIX_TIMEOUT_S = 5.0
PVT_ACCURACY_TIMEOUT_S = 5.0
PVT_DEGRADED_PDOP_THRESHOLD = 6.0
PVT_LOW_OBSERVATION_COUNT = 3
PVT_LOW_USED_SATELLITE_COUNT = 3

__all__ = [
    "GNSS_INPUT_FILTER_CUTOFF_HZ",
    "GNSS_INPUT_FILTER_HAMMING_ATTENUATION_DB",
    "GNSS_INPUT_FILTER_PASSBAND_RIPPLE_DB",
    "GNSS_INPUT_FILTER_STOPBAND_HZ",
    "GNSS_INPUT_FILTER_STOPBAND_ATTENUATION_DB",
    "GNSS_INPUT_FILTER_TRANSITION_WIDTH_HZ",
    "gnss_input_filter_group_delay_samples",
    "gnss_input_filter_tap_count",
    "GPS_L1_CA_CODE_RATE_HZ",
    "GPS_L1_CA_FREQ_HZ",
    "GPS_L1_CA_NULL_TO_NULL_BANDWIDTH_HZ",
    "GPS_L1_CA_PROCESSING_BANDWIDTH_HZ",
    "PRN_CARRIER_LOCK_THRESHOLD",
    "PRN_CNO_MAX_PEAK_TO_PEAK_DB",
    "PRN_CNO_MAX_STDEV_DB",
    "PRN_CNO_MIN_STABLE_DB_HZ",
    "PRN_CNO_REQUIRED_STABLE_WINDOWS",
    "PRN_CNO_STABILITY_WINDOW",
    "PVT_ACCURACY_TIMEOUT_S",
    "PVT_DEGRADED_PDOP_THRESHOLD",
    "PVT_LOW_OBSERVATION_COUNT",
    "PVT_LOW_USED_SATELLITE_COUNT",
    "SKY_GEOMETRY_TIMEOUT_S",
    "USED_IN_FIX_TIMEOUT_S",
]
