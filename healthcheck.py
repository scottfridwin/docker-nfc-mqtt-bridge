#!/usr/bin/env python3
"""Container health check: healthy while the bridge's monitor loop keeps touching the heartbeat file."""

import os
import sys
import time

HEARTBEAT_FILE = os.environ.get("HEARTBEAT_FILE", "/tmp/nfc-mqtt-bridge.heartbeat")  # noqa: S108
MAX_AGE_SECONDS = 30


def is_healthy(path=HEARTBEAT_FILE, max_age=MAX_AGE_SECONDS, now=None):
    try:
        age = (time.time() if now is None else now) - os.path.getmtime(path)
    except OSError:
        return False
    return age <= max_age


if __name__ == "__main__":
    sys.exit(0 if is_healthy() else 1)
