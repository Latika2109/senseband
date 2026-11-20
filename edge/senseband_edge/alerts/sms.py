"""SMS gateways (SIM800L real, mock for tests/sim) and the retrying sender."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


class SmsGateway(Protocol):
    def send(self, number: str, text: str) -> bool: ...
    def status(self) -> dict: ...


@dataclass
class SmsRecord:
    number: str
    text: str
    ok: bool
    attempt: int
    wall_ts: float


@dataclass
class MockSms:
    """Records messages instead of sending. `fail_first` makes the first N attempts fail."""

    fail_first: int = 0
    log: list[SmsRecord] = field(default_factory=list)
    _calls: int = 0

    def send(self, number: str, text: str) -> bool:
        self._calls += 1
        ok = self._calls > self.fail_first
        self.log.append(SmsRecord(number, text, ok, self._calls, time.time()))
        return ok

    def status(self) -> dict:
        return {"modem": "mock", "ok": True}

    @property
    def delivered(self) -> list[SmsRecord]:
        return [r for r in self.log if r.ok]


class Sim800Gateway:
    """SIM800L over serial (ported from edge/prototype/comms.py). pyserial is imported lazily
    so the service still starts on a laptop or when the modem is missing."""

    def __init__(self, port: str, baud: int):
        import threading
        self.lock = threading.Lock()
        self.ser = None
        self.error: str | None = None
        try:
            import serial  # type: ignore[import-not-found]
            self.ser = serial.Serial(port, baud, timeout=2)
            if "OK" not in self._cmd("AT"):
                self.error = "no reply to AT (check power / wiring)"
        except Exception as e:  # noqa: BLE001 - any failure must degrade, never crash
            self.error = str(e)

    def _cmd(self, c: str, wait: float = 1.0) -> str:
        assert self.ser is not None
        self.ser.reset_input_buffer()
        self.ser.write((c + "\r").encode())
        time.sleep(wait)
        return self.ser.read(self.ser.in_waiting or 1).decode(errors="ignore")

    def send(self, number: str, text: str) -> bool:
        if self.ser is None:
            return False
        try:
            with self.lock:
                self._cmd("AT+CMGF=1")
                self.ser.write(f'AT+CMGS="{number}"\r'.encode())
                time.sleep(0.5)
                self.ser.write(text[:160].encode() + b"\x1a")
                time.sleep(6)
                resp = self.ser.read(self.ser.in_waiting or 1).decode(errors="ignore")
            return "+CMGS" in resp
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            return False

    def status(self) -> dict:
        return {"modem": "sim800", "ok": self.ser is not None and self.error is None,
                "error": self.error}


@dataclass
class SmsResult:
    delivered: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    attempts: int = 0


def send_with_retries(gw: SmsGateway, numbers: list[str], text: str, retries: int,
                      delay_s: float, sleep: Callable[[float], None] = time.sleep) -> SmsResult:
    """Try each number up to `retries` times (the modem drops messages on weak signal)."""
    res = SmsResult()
    text = text[:160]
    for n in numbers:
        for attempt in range(1, retries + 1):
            res.attempts += 1
            if gw.send(n, text):
                res.delivered.append(n)
                break
            if attempt < retries:
                sleep(delay_s)
        else:
            res.failed.append(n)
    return res
