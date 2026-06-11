# speaker_mode.py – Toggle-switch management: volume control + live call trigger.
#
# Toggle switch (SWITCH_SPEAKER pin) has two effects:
#
#   OFF (helmet on)   → normal volume (VOLUME_NORMAL)
#                       live call stopped (if it was running)
#   ON  (helmet off)  → max volume     (VOLUME_SPEAKER)
#                       live call started (full-duplex intercom with partner)
#
# Volume is controlled via amixer / pactl on Linux/Pi.
# On the manager laptop (Windows) volume control is silently skipped.

import threading
import time
import subprocess
import platform
import logging

import config
import gpio_handler as gpio

log = logging.getLogger(__name__)


class SpeakerModeManager:
    """
    Polls the toggle-switch pin every 500 ms.
    On state change: adjusts volume AND starts/stops the LiveCall instance.
    """

    def __init__(self, switch_pin: int, live_call=None):
        """
        switch_pin : GPIO pin number of the toggle switch
        live_call  : LiveCall instance (optional, injected from main.py)
        """
        self._pin       = switch_pin
        self._live_call = live_call
        self._active    = False     # False = normal, True = speaker/call mode
        self._lock      = threading.Lock()

    # ─── Public interface ─────────────────────────────────────────────────────

    def set_live_call(self, live_call) -> None:
        """Inject the LiveCall instance after construction (avoids circular import)."""
        self._live_call = live_call

    def start(self) -> None:
        """Start background monitoring thread and apply initial switch state."""
        initial = gpio.get_switch_state(self._pin)
        self._apply(initial)

        t = threading.Thread(target=self._monitor, name="speaker-mode", daemon=True)
        t.start()
        log.info("[SPK] Speaker mode monitor started (pin %d).", self._pin)

    @property
    def is_speaker_mode(self) -> bool:
        with self._lock:
            return self._active

    # ─── Internal ─────────────────────────────────────────────────────────────

    def _monitor(self) -> None:
        prev = gpio.get_switch_state(self._pin)
        while True:
            time.sleep(0.5)
            current = gpio.get_switch_state(self._pin)
            if current != prev:
                prev = current
                self._apply(current)

    def _apply(self, is_speaker: bool) -> None:
        with self._lock:
            self._active = is_speaker

        if is_speaker:
            log.info("[SPK] Speaker mode ON – helmet removed, starting live call.")
            self._set_volume(config.VOLUME_SPEAKER)
            if self._live_call and not self._live_call.is_active:
                self._live_call.start()
                log.info("[SPK] Live call started.")
        else:
            log.info("[SPK] Speaker mode OFF – helmet on, stopping live call.")
            self._set_volume(config.VOLUME_NORMAL)
            if self._live_call and self._live_call.is_active:
                self._live_call.stop()
                log.info("[SPK] Live call stopped.")

    @staticmethod
    def _set_volume(level: int) -> None:
        if platform.system() != "Linux":
            return
        try:
            subprocess.run(["amixer", "sset", "Master", f"{level}%"],
                           check=True, capture_output=True)
        except FileNotFoundError:
            try:
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"],
                               check=True, capture_output=True)
            except Exception as exc:
                log.warning("[SPK] Volume control failed: %s", exc)
        except Exception as exc:
            log.warning("[SPK] amixer error: %s", exc)
