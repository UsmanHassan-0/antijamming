"""Jammer detection helpers for the realtime backend."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class JammerDetectorConfig:
    """Runtime thresholds for raw-power jammer detection."""

    enabled: bool = True
    min_power_db: float = -150.0
    power_rise_db: float = 6.0
    baseline_alpha: float = 0.02
    consecutive_alarms: int = 1


class JammerDetector:
    """Assess jammer state from raw IQ power and coherent spatial evidence."""

    def __init__(self, config: JammerDetectorConfig) -> None:
        self._config = config
        self._power_baseline_db = float("nan")
        self._raw_power_alarm_count = 0
        self._spatial_alarm_count = 0

    @property
    def power_baseline_db(self) -> float:
        return self._power_baseline_db

    @power_baseline_db.setter
    def power_baseline_db(self, value: float) -> None:
        self._power_baseline_db = float(value)

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    def set_enabled(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if normalized == bool(self._config.enabled):
            return
        self._config.enabled = normalized
        self.reset()

    def reset(self) -> None:
        self._power_baseline_db = float("nan")
        self._raw_power_alarm_count = 0
        self._spatial_alarm_count = 0

    def assess(
        self,
        *,
        doa_deg: float,
        input_power_db: float,
        spatial_peak_db: float = float("nan"),
    ) -> dict[str, object]:
        """Return the current jammer decision without mutating backend state."""

        if not bool(self._config.enabled):
            return {
                "assessed": False,
                "detected": False,
                "state": "disabled",
                "confidence": 0.0,
                "reason": "Jammer detection disabled",
                "doa_deg": float(doa_deg),
                "input_power_db": input_power_db,
                "power_baseline_db": float("nan"),
                "power_rise_db": float("nan"),
                "power_rise_threshold_db": float(self._config.power_rise_db),
                "spatial_peak_db": float(spatial_peak_db),
                "spatial_peak_threshold_db": float(self._config.power_rise_db),
            }

        min_power_db = float(self._config.min_power_db)
        power_rise_threshold_db = max(0.0, float(self._config.power_rise_db))
        spatial_peak_threshold_db = power_rise_threshold_db
        baseline_db, power_rise_db, baseline_ready = self._update_power_baseline(
            input_power_db=input_power_db
        )
        power_floor_ok = (not np.isfinite(min_power_db)) or input_power_db >= min_power_db
        raw_power_alarm = bool(
            baseline_ready
            and power_floor_ok
            and np.isfinite(power_rise_db)
            and power_rise_db >= power_rise_threshold_db
        )
        spatial_alarm = bool(
            baseline_ready
            and power_floor_ok
            and np.isfinite(doa_deg)
            and np.isfinite(spatial_peak_db)
            and spatial_peak_db >= spatial_peak_threshold_db
        )
        if raw_power_alarm:
            self._raw_power_alarm_count += 1
        else:
            self._raw_power_alarm_count = 0
        if spatial_alarm:
            self._spatial_alarm_count += 1
        else:
            self._spatial_alarm_count = 0
        required_alarms = max(1, int(self._config.consecutive_alarms))
        raw_alarm_count = int(self._raw_power_alarm_count)
        spatial_alarm_count = int(self._spatial_alarm_count)
        raw_detected = bool(raw_power_alarm and raw_alarm_count >= required_alarms)
        spatial_detected = bool(spatial_alarm and spatial_alarm_count >= required_alarms)
        detected = bool(raw_detected or spatial_detected)
        raw_confidence = power_rise_db / max(power_rise_threshold_db * 1.5, 1e-6)
        spatial_confidence = spatial_peak_db / max(spatial_peak_threshold_db * 1.5, 1e-6)
        confidence = max(0.0, min(1.0, max(raw_confidence, spatial_confidence)))

        if raw_detected and spatial_detected:
            state = "detected"
            reason = (
                f"Raw IQ power rise {power_rise_db:.1f} dB above baseline "
                f"{baseline_db:.1f} dB and spatial peak {spatial_peak_db:.1f} dB"
            )
        elif raw_detected:
            state = "detected"
            reason = (
                f"Raw IQ power rise {power_rise_db:.1f} dB above baseline "
                f"{baseline_db:.1f} dB"
            )
        elif spatial_detected:
            state = "detected"
            reason = f"Spatial DoA peak {spatial_peak_db:.1f} dB above spectrum floor"
        elif raw_power_alarm:
            state = "suspected"
            reason = (
                f"Raw IQ power rise {power_rise_db:.1f} dB above baseline "
                f"{baseline_db:.1f} dB, waiting for {required_alarms} consecutive alarms"
            )
        elif spatial_alarm:
            state = "suspected"
            reason = (
                f"Spatial DoA peak {spatial_peak_db:.1f} dB above spectrum floor, "
                f"waiting for {required_alarms} consecutive alarms"
            )
        elif not baseline_ready:
            state = "monitoring"
            reason = "Raw IQ power baseline initializing"
        elif not power_floor_ok:
            state = "not_detected"
            reason = f"Raw IQ power {input_power_db:.1f} dB below floor {min_power_db:.1f} dB"
        else:
            state = "not_detected"
            reason = (
                f"Raw IQ power rise {power_rise_db:.1f} dB below "
                f"{power_rise_threshold_db:.1f} dB"
            )

        return {
            "assessed": True,
            "detected": detected,
            "raw_power_alarm": raw_power_alarm,
            "spatial_alarm": spatial_alarm,
            "raw_power_alarm_count": raw_alarm_count,
            "spatial_alarm_count": spatial_alarm_count,
            "required_consecutive_alarms": required_alarms,
            "state": state,
            "confidence": confidence,
            "reason": reason,
            "doa_deg": float(doa_deg),
            "input_power_db": input_power_db,
            "min_power_db": min_power_db,
            "power_baseline_db": baseline_db,
            "power_rise_db": power_rise_db,
            "power_rise_threshold_db": power_rise_threshold_db,
            "spatial_peak_db": float(spatial_peak_db),
            "spatial_peak_threshold_db": spatial_peak_threshold_db,
        }

    def _update_power_baseline(self, *, input_power_db: float) -> tuple[float, float, bool]:
        if not np.isfinite(input_power_db):
            return float("nan"), float("nan"), False

        baseline_db = float(self._power_baseline_db)
        if not np.isfinite(baseline_db):
            self._power_baseline_db = float(input_power_db)
            return float(input_power_db), 0.0, False

        power_rise_db = float(input_power_db - baseline_db)
        threshold_db = max(0.0, float(self._config.power_rise_db))
        if power_rise_db < threshold_db:
            alpha = max(0.0, min(1.0, float(self._config.baseline_alpha)))
            baseline_db = (1.0 - alpha) * baseline_db + alpha * float(input_power_db)
            self._power_baseline_db = baseline_db
            power_rise_db = float(input_power_db - baseline_db)
        return baseline_db, power_rise_db, True
