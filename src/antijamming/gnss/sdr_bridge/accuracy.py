"""PVT coordinate and truth-error snapshot helpers."""

from __future__ import annotations

import math


class AccuracyMixin:
    def _build_accuracy_snapshot(self, points: list[dict[str, float]]) -> dict[str, object]:
        latest = points[-1]
        window_count = max(1, int(self._cfg.gnss_accuracy_window_points))
        recent_points = points[-window_count:]
        mean_lat = sum(point["latitude"] for point in recent_points) / len(recent_points)
        mean_lon = sum(point["longitude"] for point in recent_points) / len(recent_points)
        mean_alt = sum(point["altitude"] for point in recent_points) / len(recent_points)
        truth = self._latest_truth_position
        hdop = latest.get("hdop")
        vdop = latest.get("vdop")
        pdop = latest.get("pdop")
        gdop = latest.get("gdop")
        fix_type = self._fix_type_from_latest_point(latest)
        snapshot: dict[str, object] = {
            "fix_count": len(points),
            "accuracy_window_points": len(recent_points),
            "fix_type": fix_type,
            "lat_deg": latest["latitude"],
            "lon_deg": latest["longitude"],
            "alt_m": latest["altitude"],
            "truth_available": truth is not None,
            "hdop": hdop,
            "vdop": vdop,
            "pdop": pdop,
            "gdop": gdop,
        }
        utm_position = self._utm_from_lat_lon(latest["latitude"], latest["longitude"])
        if utm_position is not None:
            snapshot.update(utm_position)
        if truth is not None:
            east_m, north_m, up_m = self._enu_error_m(
                lat_deg=latest["latitude"],
                lon_deg=latest["longitude"],
                alt_m=latest["altitude"],
                ref=truth,
            )
            window_east_m, window_north_m, window_up_m = self._enu_error_m(
                lat_deg=mean_lat,
                lon_deg=mean_lon,
                alt_m=mean_alt,
                ref=truth,
            )
            horizontal_m = math.hypot(east_m, north_m)
            three_d_m = math.sqrt(east_m * east_m + north_m * north_m + up_m * up_m)
            window_horizontal_m = math.hypot(window_east_m, window_north_m)
            window_three_d_m = math.sqrt(
                window_east_m * window_east_m
                + window_north_m * window_north_m
                + window_up_m * window_up_m
            )
            snapshot.update(
                {
                    "east_error_m": east_m,
                    "north_error_m": north_m,
                    "up_error_m": up_m,
                    "horizontal_error_m": horizontal_m,
                    "three_d_error_m": three_d_m,
                    "window_horizontal_error_m": window_horizontal_m,
                    "window_three_d_error_m": window_three_d_m,
                }
            )
        else:
            self._maybe_log_truth_warning()

        return snapshot

    def _format_accuracy_summary(self, accuracy: dict[str, object]) -> str:
        parts = [
            f"PVT accuracy: fix={accuracy.get('fix_type', '--')} fixes={accuracy.get('fix_count', 0)}"
        ]
        lat = self._to_float(accuracy.get("lat_deg"))
        lon = self._to_float(accuracy.get("lon_deg"))
        alt = self._to_float(accuracy.get("alt_m"))
        if lat is not None and lon is not None and alt is not None:
            parts.append(f"position=lat {lat:.7f}, lon {lon:.7f}, alt {alt:.2f} m")
        utm_easting = self._to_float(accuracy.get("utm_easting_m"))
        utm_northing = self._to_float(accuracy.get("utm_northing_m"))
        utm_zone = str(accuracy.get("utm_zone") or "").strip()
        if utm_easting is not None and utm_northing is not None and utm_zone:
            parts.append(
                f"utm={utm_zone} east {utm_easting:.3f}, north {utm_northing:.3f}"
            )
        valid_sats = self._to_float(accuracy.get("valid_sats"))
        if valid_sats is not None:
            parts.append(f"valid_sats={valid_sats:.0f}")
        horizontal_error = self._to_float(accuracy.get("horizontal_error_m"))
        up_error = self._to_float(accuracy.get("up_error_m"))
        three_d_error = self._to_float(accuracy.get("three_d_error_m"))
        east_error = self._to_float(accuracy.get("east_error_m"))
        north_error = self._to_float(accuracy.get("north_error_m"))
        if horizontal_error is not None and up_error is not None and three_d_error is not None:
            parts.append(
                "epoch_error="
                f"H {horizontal_error:.2f} m, U {up_error:+.2f} m, 3D {three_d_error:.2f} m "
                f"(E {east_error:+.2f}, N {north_error:+.2f})"
            )
        else:
            parts.append("epoch_error=truth unavailable")
        window_horizontal_error = self._to_float(accuracy.get("window_horizontal_error_m"))
        window_three_d_error = self._to_float(accuracy.get("window_three_d_error_m"))
        if window_horizontal_error is not None and window_three_d_error is not None:
            parts.append(
                f"window_error({accuracy.get('accuracy_window_points', '--')} fixes)="
                f"H {window_horizontal_error:.2f} m, 3D {window_three_d_error:.2f} m"
            )
        dop_parts: list[str] = []
        hdop = self._to_float(accuracy.get("hdop"))
        vdop = self._to_float(accuracy.get("vdop"))
        pdop = self._to_float(accuracy.get("pdop"))
        gdop = self._to_float(accuracy.get("gdop"))
        if hdop is not None:
            dop_parts.append(f"HDOP {hdop:.2f}")
        if vdop is not None:
            dop_parts.append(f"VDOP {vdop:.2f}")
        if pdop is not None:
            dop_parts.append(f"PDOP {pdop:.2f}")
        if gdop is not None:
            dop_parts.append(f"GDOP {gdop:.2f}")
        if dop_parts:
            parts.append("DOP=" + ", ".join(dop_parts))
        return " | ".join(parts)

    def _fix_type_from_latest_point(self, latest: dict[str, float]) -> str:
        vdop = latest.get("vdop")
        pdop = latest.get("pdop")
        altitude = latest.get("altitude")
        if vdop is not None and pdop is not None and altitude is not None:
            return "3D Fix"
        return "2D Fix"

    def _maybe_log_truth_warning(self) -> None:
        if self._truth_warning_logged:
            return
        self._truth_warning_logged = True
        self._handoff_log.warning(
            "GNSS static truth position is incomplete: lat=%s lon=%s alt=%s",
            self._cfg.gnss_truth_static_lat_deg,
            self._cfg.gnss_truth_static_lon_deg,
            self._cfg.gnss_truth_static_alt_m,
        )

    def _load_truth_position(self) -> dict[str, float] | None:
        lat = self._to_float(self._cfg.gnss_truth_static_lat_deg)
        lon = self._to_float(self._cfg.gnss_truth_static_lon_deg)
        alt = self._to_float(self._cfg.gnss_truth_static_alt_m)
        if lat is None or lon is None or alt is None:
            return None
        return {
            "latitude": lat,
            "longitude": lon,
            "altitude": alt,
        }

    def _to_float(self, value: object) -> float | None:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _utm_from_lat_lon(self, lat_deg: float, lon_deg: float) -> dict[str, object] | None:
        lat = self._to_float(lat_deg)
        lon = self._to_float(lon_deg)
        if lat is None or lon is None:
            return None
        if lat < -80.0 or lat > 84.0 or lon < -180.0 or lon > 180.0:
            return None

        semi_major_axis_m = 6_378_137.0
        flattening = 1.0 / 298.257_223_563
        eccentricity_sq = flattening * (2.0 - flattening)
        second_eccentricity_sq = eccentricity_sq / (1.0 - eccentricity_sq)
        scale = 0.9996

        zone = max(1, min(60, int((lon + 180.0) // 6.0) + 1))
        hemisphere = "N" if lat >= 0.0 else "S"
        central_meridian_deg = (zone - 1) * 6.0 - 180.0 + 3.0

        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        central_meridian_rad = math.radians(central_meridian_deg)
        sin_lat = math.sin(lat_rad)
        cos_lat = math.cos(lat_rad)
        tan_lat = math.tan(lat_rad)

        radius_prime_vertical = semi_major_axis_m / math.sqrt(
            1.0 - eccentricity_sq * sin_lat * sin_lat
        )
        tan_sq = tan_lat * tan_lat
        c_term = second_eccentricity_sq * cos_lat * cos_lat
        a_term = cos_lat * (lon_rad - central_meridian_rad)

        meridian_arc = semi_major_axis_m * (
            (
                1.0
                - eccentricity_sq / 4.0
                - 3.0 * eccentricity_sq * eccentricity_sq / 64.0
                - 5.0 * eccentricity_sq * eccentricity_sq * eccentricity_sq / 256.0
            )
            * lat_rad
            - (
                3.0 * eccentricity_sq / 8.0
                + 3.0 * eccentricity_sq * eccentricity_sq / 32.0
                + 45.0 * eccentricity_sq * eccentricity_sq * eccentricity_sq / 1024.0
            )
            * math.sin(2.0 * lat_rad)
            + (
                15.0 * eccentricity_sq * eccentricity_sq / 256.0
                + 45.0 * eccentricity_sq * eccentricity_sq * eccentricity_sq / 1024.0
            )
            * math.sin(4.0 * lat_rad)
            - (35.0 * eccentricity_sq * eccentricity_sq * eccentricity_sq / 3072.0)
            * math.sin(6.0 * lat_rad)
        )

        easting_m = scale * radius_prime_vertical * (
            a_term
            + (1.0 - tan_sq + c_term) * a_term**3 / 6.0
            + (
                5.0
                - 18.0 * tan_sq
                + tan_sq * tan_sq
                + 72.0 * c_term
                - 58.0 * second_eccentricity_sq
            )
            * a_term**5
            / 120.0
        ) + 500_000.0
        northing_m = scale * (
            meridian_arc
            + radius_prime_vertical
            * tan_lat
            * (
                a_term * a_term / 2.0
                + (5.0 - tan_sq + 9.0 * c_term + 4.0 * c_term * c_term)
                * a_term**4
                / 24.0
                + (
                    61.0
                    - 58.0 * tan_sq
                    + tan_sq * tan_sq
                    + 600.0 * c_term
                    - 330.0 * second_eccentricity_sq
                )
                * a_term**6
                / 720.0
            )
        )
        if lat < 0.0:
            northing_m += 10_000_000.0

        return {
            "utm_easting_m": easting_m,
            "utm_northing_m": northing_m,
            "utm_zone": f"{zone}{hemisphere}",
        }

    def _enu_error_m(
        self,
        lat_deg: float,
        lon_deg: float,
        alt_m: float,
        ref: dict[str, float],
    ) -> tuple[float, float, float]:
        lat0 = math.radians(ref["latitude"])
        meters_per_lat = 111_132.954 - 559.822 * math.cos(2 * lat0) + 1.175 * math.cos(4 * lat0)
        meters_per_lon = (
            111_132.954 * math.cos(lat0)
            - 93.5 * math.cos(3 * lat0)
            + 0.118 * math.cos(5 * lat0)
        )
        east = (lon_deg - ref["longitude"]) * meters_per_lon
        north = (lat_deg - ref["latitude"]) * meters_per_lat
        up = alt_m - ref["altitude"]
        return east, north, up
