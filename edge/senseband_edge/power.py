"""ECG duty-cycling during sleep (docs/03 'software savings'). OFF by default, because pausing the ECG slows
collapse detection. While paused the finger SpO2 sensor is the safety net: any trouble with it, any movement, any
alert, or the periodic wake-up brings the ECG straight back."""
from __future__ import annotations

from collections import deque

from .config import PowerCfg


class PowerManager:
    def __init__(self, cfg: PowerCfg):
        self.cfg = cfg
        self.paused = False
        self._asleep_since: float | None = None
        self._stable_since: float | None = None
        self._bad_since: float | None = None
        self._paused_at = 0.0
        self._woke_at = -1e9
        self._hrs: deque[float] = deque(maxlen=60)
        self.pauses = 0

    def update(self, now: float, activity: str, ppg_hr: float | None, alert_open: bool, hr_low: float) -> bool:
        """Returns the desired ECG state: True = ECG on. Call once per second."""
        c = self.cfg
        if not c.ecg_duty_cycle:
            return True
        asleep = activity == "sleeping"
        self._asleep_since = (self._asleep_since if self._asleep_since is not None else now) if asleep else None
        if ppg_hr is None:
            self._bad_since = self._bad_since if self._bad_since is not None else now
            self._stable_since = None
            self._hrs.clear()
        else:
            self._bad_since = None
            self._hrs.append(ppg_hr)
            med = sorted(self._hrs)[len(self._hrs) // 2]
            steady = abs(ppg_hr - med) <= c.ppg_stable_band_bpm and ppg_hr > hr_low + 10
            self._stable_since = (self._stable_since if self._stable_since is not None else now) if steady else None
        if self.paused:
            bad_long = self._bad_since is not None and now - self._bad_since >= c.resume_if_ppg_bad_s
            unstable = ppg_hr is not None and self._stable_since is None      # pulse present but jumping around
            if (not asleep) or alert_open or bad_long or unstable or now - self._paused_at >= c.wake_every_s:
                self.paused = False
                self._woke_at = now
            return not self.paused
        ready = (asleep and self._asleep_since is not None and now - self._asleep_since >= c.sleep_min_s
                 and self._stable_since is not None and now - self._stable_since >= c.ppg_stable_s
                 and not alert_open and now - self._woke_at >= c.wake_for_s)
        if ready:
            self.paused, self._paused_at = True, now
            self.pauses += 1
        return not self.paused
