from __future__ import annotations

import logging
import subprocess
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class Light(ABC):
    @abstractmethod
    def on(self) -> None: ...

    @abstractmethod
    def off(self) -> None: ...

    def blink(self, times: int = 1, on_s: float = 0.35, off_s: float = 0.25) -> None:
        for i in range(times):
            self.on()
            time.sleep(on_s)
            self.off()
            if i < times - 1:
                time.sleep(off_s)


class UhubctlLight(Light):
    """Toggles a USB port's power via uhubctl. Failures are logged, never raised —
    a dark lamp should not stop the message from being read."""

    def __init__(self, location: str, port: str):
        self.location = location
        self.port = port

    def _set(self, state: str) -> None:
        try:
            subprocess.run(
                ["uhubctl", "-l", self.location, "-p", self.port, "-a", state],
                check=True,
                capture_output=True,
                timeout=5,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("uhubctl -a %s failed: %s", state, exc)

    def on(self) -> None:
        self._set("on")

    def off(self) -> None:
        self._set("off")


class NullLight(Light):
    """No physical light — logs instead. Used for local testing off-device."""

    def on(self) -> None:
        logger.info("light: ON")

    def off(self) -> None:
        logger.info("light: OFF")
