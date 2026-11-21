"""Twilio SMS gateway (REST API, stdlib only) and the provider factory.

Credentials are read ONLY from environment variables (never from tracked config files):
  TWILIO_ACCOUNT_SID   starts with AC
  TWILIO_AUTH_TOKEN    32 characters
  TWILIO_FROM_NUMBER   your Twilio number in E.164 (+1...), OR
  TWILIO_MESSAGING_SERVICE_SID   (starts with MG) instead of a from number
Nothing secret is ever logged, put in an exception message, or returned by status().
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from ..config import Settings
from .sms import MockSms, Sim800Gateway, SmsGateway

log = logging.getLogger("senseband.sms")
E164 = re.compile(r"^\+[1-9]\d{7,14}$")
SID = re.compile(r"^AC[0-9a-fA-F]{32}$")


def is_e164(n: str) -> bool:
    return bool(E164.match(n or ""))


def parse_numbers(raw: str) -> list[str]:
    """ALERT_TO_NUMBERS may be a JSON list (["+91..."]) or comma/space separated."""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            items = json.loads(raw)
        except ValueError:
            items = re.findall(r"\+?\d+", raw)
    else:
        items = re.split(r"[,\s;]+", raw)
    return [str(i).strip() for i in items if str(i).strip()]


class TwilioGateway:
    def __init__(self, account_sid: str, auth_token: str, from_number: str = "", messaging_service_sid: str = "",
                 api_base: str = "https://api.twilio.com", timeout_s: float = 15.0,
                 opener: Callable[..., Any] = urllib.request.urlopen):
        self._sid, self._token = (account_sid or "").strip(), (auth_token or "").strip()
        self.from_number, self.msg_service = (from_number or "").strip(), (messaging_service_sid or "").strip()
        self.api_base, self.timeout, self._open = api_base.rstrip("/"), timeout_s, opener
        self.last_error: str | None = None
        self.sent = self.failed = 0

    @classmethod
    def from_env(cls, env: Mapping[str, str], api_base: str, timeout_s: float) -> TwilioGateway:
        return cls(env.get("TWILIO_ACCOUNT_SID", ""), env.get("TWILIO_AUTH_TOKEN", ""), env.get("TWILIO_FROM_NUMBER", ""),
                   env.get("TWILIO_MESSAGING_SERVICE_SID", ""), api_base, timeout_s)

    # ---- configuration check (no network) ----------------------------------------------------------------
    def problems(self) -> list[str]:
        p = []
        if not SID.match(self._sid):
            p.append("TWILIO_ACCOUNT_SID missing or not an AC... id")
        if len(self._token) != 32:
            p.append("TWILIO_AUTH_TOKEN missing or not 32 characters")
        if not (is_e164(self.from_number) or self.msg_service.startswith("MG")):
            p.append("TWILIO_FROM_NUMBER missing or not E.164 (+...) (or set TWILIO_MESSAGING_SERVICE_SID)")
        return p

    @property
    def configured(self) -> bool:
        return not self.problems()

    # ---- sending -----------------------------------------------------------------------------------------
    def send(self, number: str, text: str) -> bool:
        probs = self.problems()
        if probs:
            self.last_error = "; ".join(probs)
            self.failed += 1
            return False
        if not is_e164(number):
            self.last_error = "destination is not an E.164 number"
            self.failed += 1
            return False
        form = {"To": number, "Body": text[:160]}
        form["MessagingServiceSid" if self.msg_service.startswith("MG") and not is_e164(self.from_number) else "From"] = (
            self.msg_service if self.msg_service.startswith("MG") and not is_e164(self.from_number) else self.from_number)
        url = f"{self.api_base}/2010-04-01/Accounts/{self._sid}/Messages.json"
        req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST")
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{self._sid}:{self._token}".encode()).decode())
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self._open(req, timeout=self.timeout) as r:        # noqa: S310 - https URL from config
                body = json.loads(r.read().decode() or "{}")
                ok = 200 <= getattr(r, "status", 201) < 300 and body.get("status") not in ("failed", "undelivered")
        except urllib.error.HTTPError as e:
            self.last_error = self._explain(e)
            self.failed += 1
            log.warning("Twilio rejected the SMS: %s", self.last_error)
            return False
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            self.last_error = f"network error: {type(e).__name__}"
            self.failed += 1
            log.warning("Twilio unreachable: %s", self.last_error)
            return False
        if ok:
            self.last_error = None
            self.sent += 1
        else:
            self.last_error = f"Twilio reported status '{body.get('status')}'"
            self.failed += 1
        return ok

    @staticmethod
    def _explain(e: urllib.error.HTTPError) -> str:
        try:
            j = json.loads(e.read().decode())
            code, msg = j.get("code"), str(j.get("message", ""))[:160]
        except (ValueError, OSError):
            code, msg = None, ""
        hint = {20003: "authentication failed: check the account SID / auth token",
                21211: "invalid destination number", 21608: "trial account: the destination number is not verified in Twilio",
                21606: "the From number is not a Twilio number you own / not SMS-capable",
                21614: "destination is not a valid mobile number", 21408: "sending to this country is not enabled for the account",
                30007: "message filtered by the carrier (India needs DLT registration)"}.get(code or 0)
        return f"HTTP {e.code}" + (f" code {code}" if code else "") + (f": {hint}" if hint else (f": {msg}" if msg else ""))

    def status(self) -> dict[str, Any]:
        probs = self.problems()
        return {"modem": "twilio", "ok": not probs and self.last_error is None, "configured": not probs,
                "error": "; ".join(probs) if probs else self.last_error, "sent": self.sent, "failed": self.failed,
                "from": (self.from_number[:3] + "***" + self.from_number[-3:]) if is_e164(self.from_number) else None}


class FallbackSms:
    """Try each gateway in order until one delivers (e.g. Twilio over the internet, then the SIM800L)."""

    def __init__(self, gateways: list[SmsGateway]):
        self.gateways = gateways

    def send(self, number: str, text: str) -> bool:
        for g in self.gateways:
            try:
                if g.send(number, text):
                    return True
            except Exception:  # noqa: BLE001 - one broken gateway must not stop the next
                log.exception("SMS gateway raised")
        return False

    def status(self) -> dict[str, Any]:
        subs = [g.status() for g in self.gateways]
        return {"modem": "+".join(str(s.get("modem")) for s in subs), "ok": any(s.get("ok") for s in subs),
                "error": next((s.get("error") for s in subs if s.get("error") and not s.get("ok")), None), "gateways": subs}


def make_sms(cfg: Settings, env: Mapping[str, str] | None = None) -> SmsGateway:
    """provider: auto = Twilio when its env vars are complete, plus the SIM800L as a backup on a real band.
    In simulation `auto` is always the mock; set provider: twilio explicitly to send real SMS from a simulated run."""
    env = os.environ if env is None else env
    prov = cfg.sms.provider
    if prov == "mock":
        return MockSms()
    tw = TwilioGateway.from_env(env, cfg.sms.twilio.api_base, cfg.sms.twilio.timeout_s)
    if prov == "twilio":
        return tw
    if prov == "sim800":
        return Sim800Gateway(cfg.sms.sim800.port, cfg.sms.sim800.baud)
    if cfg.is_sim:                                             # a demo/simulation must never text a real phone by accident
        return MockSms()
    gws: list[SmsGateway] = []
    if tw.configured:
        gws.append(tw)
    if not cfg.is_sim:
        gws.append(Sim800Gateway(cfg.sms.sim800.port, cfg.sms.sim800.baud))
    return gws[0] if len(gws) == 1 else FallbackSms(gws)
