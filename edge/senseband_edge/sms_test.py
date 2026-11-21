"""Check the SMS setup without guessing:

    python -m senseband_edge.sms_test            # prints what is configured / missing, sends NOTHING
    python -m senseband_edge.sms_test --send     # sends ONE test SMS to every number in ALERT_TO_NUMBERS (costs a message)
"""
from __future__ import annotations

import argparse
import sys

from .alerts.twilio import TwilioGateway
from .config import load_settings
from .envfile import load_env_files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SenseBand SMS (Twilio) check")
    ap.add_argument("--send", action="store_true", help="really send a test SMS")
    a = ap.parse_args(argv)
    load_env_files()
    cfg = load_settings({"mode": "hw"})
    import os
    gw = TwilioGateway.from_env(os.environ, cfg.sms.twilio.api_base, cfg.sms.twilio.timeout_s)
    print("recipients (ALERT_TO_NUMBERS / sms.contacts):", cfg.sms.contacts)
    problems = gw.problems()
    if problems:
        print("NOT READY:")
        for p in problems:
            print("  -", p)
        return 2
    print("Twilio configuration looks complete; from:", gw.status()["from"])
    if not a.send:
        print("Dry run only. Add --send to send one real test SMS.")
        return 0
    ok = True
    for n in cfg.sms.contacts:
        sent = gw.send(n, "SenseBand test: SMS alerts are working.")
        ok &= sent
        print(f"  {n}: {'sent' if sent else 'FAILED - ' + str(gw.last_error)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
