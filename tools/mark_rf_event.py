#!/usr/bin/env python3
"""Append an explicit operator RF event to the current run log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from antijamming.logging import RF_EVENTS, record_event  # noqa: E402


EVENTS = tuple(sorted(RF_EVENTS | {
    "bladerf_on",
    "bladerf_off",
    "bladerf_gain_db",
}))

EVENT_ALIASES = {
    "bladerf_on": "bladeRF_on",
    "bladerf_off": "bladeRF_off",
    "bladerf_gain_db": "bladeRF_gain_db",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, choices=EVENTS)
    parser.add_argument("--attenuation-db", type=float)
    parser.add_argument("--bladerf-gain-db", type=float)
    parser.add_argument("--notes", default="")
    parser.add_argument("--logs", type=Path, default=Path("logs"))
    args = parser.parse_args()

    payload = record_event(
        args.logs,
        EVENT_ALIASES.get(args.event, args.event),
        source="cli",
        attenuation_db=args.attenuation_db,
        bladeRF_gain_db=args.bladerf_gain_db,
        notes=str(args.notes),
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
