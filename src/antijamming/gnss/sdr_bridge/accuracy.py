"""PVT coordinates and cumulative horizontal repeatability, not absolute accuracy."""

from __future__ import annotations

import math


class AccuracyMixin:
    def _build_accuracy_snapshot(self, points: list[dict[str, float]]) -> dict[str, object]:
        if not points:
            return {}
        for point in points:
            if (not all(math.isfinite(point[key]) for key in
                        ("latitude", "longitude", "altitude"))
                    or not -90.0 <= point["latitude"] <= 90.0
                    or not -180.0 <= point["longitude"] <= 180.0):
                raise ValueError("invalid PVT position")
        latest = points[-1]
        minimum_count = max(2, int(self._cfg.gnss_accuracy_window_points))
        hdop = latest.get("hdop")
        vdop = latest.get("vdop")
        pdop = latest.get("pdop")
        gdop = latest.get("gdop")
        fix_type = self._fix_type_from_latest_point(latest)
        snapshot: dict[str, object] = {
            "fix_count": len(points),
            "accuracy_window_points": len(points),
            "accuracy_scope": "run_cumulative",
            "cep_sample_count": len(points),
            "cep_min_points": minimum_count,
            "cep_scope": "run_cumulative",
            "cep_reference": "run_mean",
            "cep_metric": "horizontal_repeatability",
            "cep_ready": False,
            "fix_type": fix_type,
            "lat_deg": latest["latitude"],
            "lon_deg": latest["longitude"],
            "alt_m": latest["altitude"],
            "hdop": hdop,
            "vdop": vdop,
            "pdop": pdop,
            "gdop": gdop,
        }
        utm_position = self._utm_from_lat_lon(latest["latitude"], latest["longitude"])
        if utm_position is not None:
            snapshot.update(utm_position)
        if len(points) >= minimum_count:
            radii = self._horizontal_repeatability_radii_m(points)
            snapshot.update(
                {
                    "cep50_m": self._empirical_nearest_rank(radii, 0.50),
                    "cep95_m": self._empirical_nearest_rank(radii, 0.95),
                    "cep_ready": True,
                }
            )

        return snapshot

    @staticmethod
    def _empirical_nearest_rank(values: list[float], probability: float) -> float:
        """Return the empirical radius containing the requested fraction of samples."""
        if not values:
            raise ValueError("empirical CEP requires at least one sample")
        ordered = sorted(float(value) for value in values)
        rank = max(1, math.ceil(float(probability) * len(ordered)))
        return ordered[min(rank, len(ordered)) - 1]

    def _fix_type_from_latest_point(self, latest: dict[str, float]) -> str:
        vdop = latest.get("vdop")
        pdop = latest.get("pdop")
        altitude = latest.get("altitude")
        if vdop is not None and pdop is not None and altitude is not None:
            return "3D Fix"
        return "2D Fix"

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

    @staticmethod
    def _horizontal_repeatability_radii_m(points: list[dict[str, float]]) -> list[float]:
        """Center all fixes in one local east/north plane for a stationary run.

        Convert WGS84 lat/lon to ellipsoid-surface ECEF, then project relative
        to the first fix's tangent axes. Height is intentionally excluded from
        this horizontal metric. Recenter on the mean of ALL projected fixes,
        not the first fix and not a known position. ECEF avoids a longitude
        discontinuity at the dateline. This is local scatter, not a geodesic
        metric for a worldwide trajectory or an absolute positioning error.
        """
        flattening = 1.0 / 298.257_223_563
        eccentricity_sq = flattening * (2.0 - flattening)
        lat0 = math.radians(points[0]["latitude"])
        lon0 = math.radians(points[0]["longitude"])
        sin_lat, cos_lat = math.sin(lat0), math.cos(lat0)
        sin_lon, cos_lon = math.sin(lon0), math.cos(lon0)
        positions = []
        for point in points:
            lat = math.radians(point["latitude"])
            lon = math.radians(point["longitude"])
            radius = 6_378_137.0 / math.sqrt(1.0 - eccentricity_sq * math.sin(lat)**2)
            positions.append((radius * math.cos(lat) * math.cos(lon),
                              radius * math.cos(lat) * math.sin(lon),
                              radius * (1.0 - eccentricity_sq) * math.sin(lat)))

        x0, y0, z0 = positions[0]
        east, north = [], []
        for x, y, z in positions:
            dx, dy, dz = x - x0, y - y0, z - z0
            east.append(-sin_lon * dx + cos_lon * dy)
            north.append(-sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz)
        mean_east = math.fsum(east) / len(east)
        mean_north = math.fsum(north) / len(north)
        return [math.hypot(e - mean_east, n - mean_north)
                for e, n in zip(east, north, strict=True)]
