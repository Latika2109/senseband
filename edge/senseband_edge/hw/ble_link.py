"""BLE link to the small band: notify lines in, command characteristic out (RATE/VIB/CAL)."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable


class SmallBandLink:
    """bleak runs in its own thread/event loop; reconnects forever. bleak is imported lazily."""

    def __init__(self, name: str, data_uuid: str, cmd_uuid: str, on_line: Callable[[str], None]):
        self.name, self.data_uuid, self.cmd_uuid, self.on_line = name, data_uuid, cmd_uuid, on_line
        self.connected = False
        self.error: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None

    def start(self) -> None:
        threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True,
                         name="ble").start()

    def send_command(self, text: str) -> None:
        if self._loop and self._client is not None and self.connected:
            asyncio.run_coroutine_threadsafe(
                self._client.write_gatt_char(self.cmd_uuid, text.encode()), self._loop)

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            from bleak import BleakClient, BleakScanner  # type: ignore[import-not-found]
        except Exception as e:  # noqa: BLE001
            self.error = f"bleak not installed: {e}"
            return
        while True:
            try:
                dev = await BleakScanner.find_device_by_name(self.name, timeout=10)
                if dev is not None:
                    async with BleakClient(dev) as client:
                        self._client = client
                        await client.start_notify(self.data_uuid, self._handler)
                        self.connected, self.error = True, None
                        while client.is_connected:
                            await asyncio.sleep(1)
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
            self.connected, self._client = False, None
            await asyncio.sleep(2)

    def _handler(self, _sender, data: bytearray) -> None:  # noqa: ANN001
        for line in data.decode(errors="ignore").strip().splitlines():
            self.on_line(line.strip())
