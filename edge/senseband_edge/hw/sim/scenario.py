"""Scenario timeline: segments with smooth ramps, events (fall/collapse) and SpO2 dips."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .config import load_config, load_scenario_dict

# Parameters that change smoothly between segments.
PHYSIO = ("hr", "spo2", "temp", "ecg_noise")        # ramp over ramp_s (slow physiology)
KINEMATIC = ("cadence_hz", "vert_g", "lat_g", "gyro_dps", "motion", "breathing_g",
             "arm_swing_deg")                       # ramp over trans_s (movement changes fast)
NUMERIC = PHYSIO + KINEMATIC


@dataclass
class Segment:
    start: float
    end: float
    p: dict[str, Any]            # fully resolved parameters
    ramp_s: float
    trans_s: float               # orientation transition time
    event: str | None = None     # event that begins this segment ("fall"/"collapse")
    rep_amps: np.ndarray = field(default_factory=lambda: np.ones(0))


def _smooth(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


class Timeline:
    def __init__(self, scenario: dict[str, Any], cfg: dict[str, Any] | None = None,
                 seed: int | None = None):
        self.cfg = cfg or load_config()
        self.name: str = scenario.get("name", "scenario")
        self.description: str = scenario.get("description", "")
        self.defaults: dict[str, Any] = scenario.get("defaults", {})
        self.seed: int = int(seed if seed is not None else scenario.get("seed", 0))
        rng = np.random.default_rng([self.seed, 99])
        self.segments: list[Segment] = []
        t = 0.0
        for raw in scenario["segments"]:
            t = self._add(raw, t, rng)
        if not self.segments:
            raise ValueError("scenario has no segments")
        self.duration_s = t
        self._starts = np.array([s.start for s in self.segments])
        self._num: dict[str, np.ndarray] = {
            k: np.array([float(s.p[k]) for s in self.segments]) for k in NUMERIC
        }
        self._ramps = {
            "physio": np.array([s.ramp_s for s in self.segments]),
            "kin": np.array([s.trans_s for s in self.segments]),
        }
        self._ramps["kin"][0] = 0.0
        self.events = [(s.start, s.event) for s in self.segments if s.event]
        self.dips = [(s.start + d["at_s"], float(d["depth"]), float(d["dur_s"]))
                     for s in self.segments for d in s.p.get("spo2_dips", [])]

    # ---- construction -------------------------------------------------------------------
    def _resolve(self, raw: dict[str, Any]) -> dict[str, Any]:
        acts = self.cfg["activities"]
        act = raw.get("activity", "sitting")
        if act not in acts:
            raise ValueError(f"unknown activity '{act}' (known: {', '.join(acts)})")
        p = {"temp": self.cfg["temp"]["base_c"], "ecg_noise": 0.0, "rhythm": "normal",
             "lead_off": False, "finger_off": False, "grip_test": None, "joint": None,
             "spo2_dips": [], "label": act, "ref_euler_deg": [0, 0, 0], "hinge_sign": 1}
        p.update(copy.deepcopy(acts[act]))
        p.update(copy.deepcopy(self.defaults))
        p.update(copy.deepcopy({k: v for k, v in raw.items() if k != "events"}))
        p["activity"] = act
        return p

    def _add(self, raw: dict[str, Any], t: float, rng: np.random.Generator) -> float:
        dur = float(raw["duration_s"])
        p = self._resolve(raw)
        ramp = float(raw.get("ramp_s", self.cfg["default_ramp_s"]))
        trans = float(raw.get("trans_s", self.cfg["default_transition_s"]))
        events = sorted(raw.get("events", []), key=lambda e: e["at_s"])
        seg_start, seg_t = t, t
        event_name: str | None = None
        for ev in events:
            e_abs = seg_start + float(ev["at_s"])
            if not seg_t < e_abs < seg_start + dur:
                raise ValueError(f"event at {ev['at_s']} s is outside its segment")
            self._push(seg_t, e_abs, p, ramp, trans, event_name, rng)
            kind = ev["type"]
            if kind not in ("fall", "collapse"):
                raise ValueError(f"unknown event type '{kind}'")
            p = self._resolve({**raw, "activity": ev.get("then", "lying_still"),
                               **{k: v for k, v in ev.items() if k.endswith("_after")}})
            for k in list(p):
                if k.endswith("_after"):
                    p[k[: -len("_after")]] = p.pop(k)
            ramp = float(ev.get("ramp_after_s", 3.0))
            trans = float(self.cfg["imu"][kind]["rotate_s"])
            event_name, seg_t = kind, e_abs
        self._push(seg_t, seg_start + dur, p, ramp, trans, event_name, rng)
        return seg_start + dur

    def _push(self, a: float, b: float, p: dict[str, Any], ramp: float, trans: float,
              event: str | None, rng: np.random.Generator) -> None:
        if not self.segments:
            ramp = 0.0
        seg = Segment(a, b, copy.deepcopy(p), ramp, min(trans, max(b - a - 1e-3, 1e-3)), event)
        j = seg.p.get("joint")
        if j:
            seg.rep_amps = 1.0 + rng.normal(0.0, float(j.get("rep_jitter", 0.03)), int(j["reps"]))
        self.segments.append(seg)

    # ---- queries ------------------------------------------------------------------------
    def index(self, t: np.ndarray | float) -> np.ndarray:
        i = np.searchsorted(self._starts, np.atleast_1d(t), side="right") - 1
        return np.clip(i, 0, len(self.segments) - 1)

    def num(self, name: str, t: np.ndarray | float) -> np.ndarray:
        """Numeric parameter at times t, ramping smoothly from the previous segment."""
        t = np.atleast_1d(np.asarray(t, dtype=float))
        vals = self._num[name]
        i = self.index(t)
        cur = vals[i]
        prev = vals[np.maximum(i - 1, 0)]
        ramp = self._ramps["physio" if name in PHYSIO else "kin"][i]
        with np.errstate(divide="ignore", invalid="ignore"):
            w = np.where(ramp > 0, _smooth((t - self._starts[i]) / ramp), 1.0)
        return prev + (cur - prev) * w

    def step(self, name: str, t: float) -> Any:
        return self.segments[int(self.index(t)[0])].p[name]

    def spo2(self, t: np.ndarray | float) -> np.ndarray:
        """SpO2 including desaturation dips (triangular, smoothed)."""
        t = np.atleast_1d(np.asarray(t, dtype=float))
        out = self.num("spo2", t)
        for at, depth, dur in self.dips:
            c = at + dur / 2
            out = out - depth * _smooth(1.0 - np.abs(t - c) / (dur / 2))
        return np.clip(out, 50.0, 100.0)

    def flag(self, name: str, t: np.ndarray) -> np.ndarray:
        idx = self.index(t)
        return np.array([bool(self.segments[i].p[name]) for i in idx])

    def joint_angle(self, t: np.ndarray) -> np.ndarray:
        """Signed hinge angle (deg) of the moving limb relative to the reference limb."""
        t = np.atleast_1d(np.asarray(t, dtype=float))
        out = np.zeros_like(t)
        idx = self.index(t)
        for i in np.unique(idx):
            seg = self.segments[i]
            j = seg.p.get("joint")
            if not j:
                continue
            m = idx == i
            tau = t[m] - seg.start - float(j.get("rest_s", 0.0))
            rep_s, hold = float(j["rep_s"]), float(j.get("hold_s", 0.0))
            rise = max((rep_s - hold) / 2.0, 1e-3)
            k = np.floor(tau / rep_s).astype(int)
            u = tau - k * rep_s
            frac = np.where(u < rise, 0.5 - 0.5 * np.cos(np.pi * u / rise),
                            np.where(u < rise + hold, 1.0,
                                     0.5 + 0.5 * np.cos(np.pi * (u - rise - hold) / rise)))
            amp = seg.rep_amps[np.clip(k, 0, len(seg.rep_amps) - 1)]
            active = (tau >= 0) & (k < int(j["reps"]))
            out[m] = np.where(active, frac * amp * float(j["rom_deg"]), 0.0) * seg.p["hinge_sign"]
        return out

    def grip_kg(self, t: np.ndarray) -> np.ndarray | None:
        """Grip force (kg) if a grip test is running at these times, else None."""
        t = np.atleast_1d(np.asarray(t, dtype=float))
        seg = self.segments[int(self.index(t[len(t) // 2])[0])]
        g = seg.p.get("grip_test")
        if not g:
            return None
        tau = t - seg.start - float(g.get("at_s", 2.0))
        ramp, hold = float(g.get("ramp_s", 1.5)), float(g.get("hold_s", 2.0))
        shape = np.where(tau < ramp, _smooth(tau / ramp),
                         np.where(tau < ramp + hold, 1.0, _smooth(1 - (tau - ramp - hold) / ramp)))
        shape = np.where(tau < 0, 0.0, shape)
        return float(g.get("peak_kg", 25.0)) * shape


def load_timeline(name_or_path: str, cfg: dict[str, Any] | None = None,
                  seed: int | None = None) -> Timeline:
    return Timeline(load_scenario_dict(name_or_path), cfg, seed)
