"""Buttons, buzzer and status LED (docs/03_HARDWARE.md pins). SimIO for laptops/tests."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Hardware


class BandIO:
    """Interface. `buzzer(True)` sounds, `led(mode)` mode in off|on|slow|fast."""

    def buzzer(self, on: bool) -> None: ...
    def led(self, mode: str) -> None: ...
    def on_buttons(self, short: Callable[[], None], long_press: Callable[[], None]) -> None: ...
    def close(self) -> None: ...


@dataclass
class SimIO(BandIO):
    buzzer_on: bool = False
    led_mode: str = "off"
    log: list[tuple[str, object]] = field(default_factory=list)
    _short: Callable[[], None] | None = None
    _long: Callable[[], None] | None = None

    def buzzer(self, on: bool) -> None:
        if on != self.buzzer_on:
            self.log.append(("buzzer", on))
        self.buzzer_on = on

    def led(self, mode: str) -> None:
        if mode != self.led_mode:
            self.log.append(("led", mode))
        self.led_mode = mode

    def on_buttons(self, short, long_press) -> None:  # noqa: ANN001
        self._short, self._long = short, long_press

    # test / API helpers: "press the physical button"
    def press_short(self) -> None:
        if self._short:
            self._short()

    def press_long(self) -> None:
        if self._long:
            self._long()

    def close(self) -> None:
        pass


class GpioIO(BandIO):
    """Raspberry Pi GPIO via gpiozero (lazy import; degrades to a no-op without GPIO)."""

    def __init__(self, hw: Hardware):
        self.hw, self.ok, self.error = hw, False, None
        self._buzzer = self._led = self._button = None
        try:
            from gpiozero import LED, Button, OutputDevice  # type: ignore[import-not-found]
            self._buzzer = OutputDevice(hw.pin_buzzer, initial_value=False)
            self._led = LED(hw.pin_led)
            self._button = Button(hw.pin_button, pull_up=True, hold_time=hw.sos_hold_s)
            self.ok = True
        except Exception as e:  # noqa: BLE001
            self.error = str(e)

    def buzzer(self, on: bool) -> None:
        if self._buzzer is not None:
            self._buzzer.on() if on else self._buzzer.off()

    def led(self, mode: str) -> None:
        if self._led is None:
            return
        if mode == "off":
            self._led.off()
        elif mode == "on":
            self._led.on()
        else:
            self._led.blink(on_time=0.5 if mode == "slow" else 0.15,
                            off_time=1.0 if mode == "slow" else 0.15)

    def on_buttons(self, short, long_press) -> None:  # noqa: ANN001
        if self._button is None:
            return
        held = {"v": False}

        def _held() -> None:
            held["v"] = True
            long_press()

        def _released() -> None:
            if not held["v"]:
                short()
            held["v"] = False

        self._button.when_held = _held
        self._button.when_released = _released

    def close(self) -> None:
        for d in (self._buzzer, self._led, self._button):
            try:
                d and d.close()
            except Exception:  # noqa: BLE001
                pass
