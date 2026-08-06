#!/usr/bin/env python3
"""Append an explicit operator RF event to the current run log."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


EVENTS = (
    "jammer_on",
    "jammer_off",
    "bladeRF_on",
    "bladeRF_off",
    "lcmv_on",
    "lcmv_off",
    "attenuation_db",
    "bladeRF_gain_db",
    "notes",
    "bladerf_on",
    "bladerf_off",
    "bladerf_gain_db",
)

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

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": EVENT_ALIASES.get(args.event, args.event),
        "attenuation_db": args.attenuation_db,
        "bladeRF_gain_db": args.bladerf_gain_db,
        "notes": str(args.notes),
    }
    path = args.logs / "operator_events.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o664)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
