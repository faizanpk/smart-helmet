# call_manager.py – Call state machine for manager ↔ worker live calls.
#
# Each helmet runs one CallManager instance.
# The manager has TWO call slots (one per worker); workers have ONE (to manager).
#
# State machine per slot:
#
#   IDLE ──press──► CALLING ──CALL_ACCEPT──► IN_CALL ──press──► IDLE
#     ▲                │                         │
#     │           press/CALL_END             CALL_END
#     └────────────────┴─────────────────────────┘
#
#   IDLE ──CALL_REQUEST──► INCOMING ──press──► IN_CALL ──press──► IDLE
#                              │
#                         CALL_END/timeout
#                              │
#                            IDLE
#
# Signal messages (sent over TCP via network.py):
#   MSG_CALL_REQUEST  : caller → callee
#   MSG_CALL_ACCEPT   : callee → caller  (call answered)
#   MSG_CALL_END      : either → other   (hang up)

import enum
import threading
import time
import logging
from typing import Optional, Callable

import config
import led_handler
from translation import speak, play_alert_beep

log = logging.getLogger(__name__)

# ─── Incoming call ring timeout (seconds) ─────────────────────────────────────
RING_TIMEOUT = 30.0


class CallState(enum.Enum):
    IDLE     = "idle"
    CALLING  = "calling"    # we called, waiting for answer
    INCOMING = "incoming"   # partner called us, waiting for us to answer
    IN_CALL  = "in_call"   # audio streaming active


class CallSlot:
    """
    Manages the call state for one partner (one worker slot on manager,
    or the manager slot on a worker).
    """

    def __init__(self,
                 slot_id:          str,             # "w-01", "w-02", or "manager"
                 partner_label:    str,             # human label: "worker 1", "manager"
                 partner_ip:       str,             # IP for LiveCall UDP
                 send_request_fn:  Callable,        # fn(slot_id) → sends CALL_REQUEST
                 send_accept_fn:   Callable,        # fn(slot_id) → sends CALL_ACCEPT
                 send_end_fn:      Callable,        # fn(slot_id) → sends CALL_END
                 live_call_factory: Callable):      # fn(partner_ip) → LiveCall
        self.slot_id       = slot_id
        self.partner_label = partner_label
        self.partner_ip    = partner_ip

        self._send_request  = send_request_fn
        self._send_accept   = send_accept_fn
        self._send_end      = send_end_fn
        self._make_live_call = live_call_factory

        self._state      = CallState.IDLE
        self._live_call  = None
        self._lock       = threading.Lock()
        self._ring_timer: Optional[threading.Timer] = None

    # ─── State query ──────────────────────────────────────────────────────────

    @property
    def state(self) -> CallState:
        with self._lock:
            return self._state

    @property
    def is_in_call(self) -> bool:
        return self.state == CallState.IN_CALL

    def mute(self, muted: bool) -> None:
        """Mute / unmute mic during an active call."""
        if self._live_call:
            self._live_call.mute(muted)

    # ─── Button pressed for this slot ─────────────────────────────────────────

    def on_button_pressed(self) -> None:
        """Called when the user presses the call button for this slot."""
        with self._lock:
            state = self._state

        if state == CallState.IDLE:
            self._initiate()
        elif state == CallState.CALLING:
            self._cancel()
        elif state == CallState.INCOMING:
            self._answer()
        elif state == CallState.IN_CALL:
            self._hangup(local=True)

    # ─── Incoming signal handlers (called from network receive thread) ─────────

    def on_call_request_received(self) -> None:
        """Partner initiated a call to us."""
        with self._lock:
            if self._state != CallState.IDLE:
                log.info("[CALL] Busy – rejecting call from %s.", self.slot_id)
                self._send_end(self.slot_id)
                return
            self._state = CallState.INCOMING

        log.info("[CALL] Incoming call from %s.", self.slot_id)

        # Alert user
        threading.Thread(target=play_alert_beep, args=(3,),
                         daemon=True, name="call-beep").start()
        led_handler.blink_alert(times=10, interval=0.1)
        speak(f"Incoming call from {self.partner_label}. "
              f"Press call button to answer.",
              config.HELMET_LANGUAGE_CODE)

        # Start ring timeout
        self._ring_timer = threading.Timer(RING_TIMEOUT, self._ring_timeout)
        self._ring_timer.daemon = True
        self._ring_timer.start()

    def on_call_accepted(self) -> None:
        """Partner answered our outgoing call."""
        with self._lock:
            if self._state != CallState.CALLING:
                return
            self._state = CallState.IN_CALL

        self._cancel_ring_timer()
        log.info("[CALL] Call accepted by %s – starting audio.", self.slot_id)
        self._start_audio()
        speak("Call connected.", config.HELMET_LANGUAGE_CODE)

    def on_call_ended(self) -> None:
        """Partner hung up."""
        with self._lock:
            prev = self._state
            self._state = CallState.IDLE

        self._cancel_ring_timer()
        if prev == CallState.IN_CALL:
            self._stop_audio()
            speak("Call ended.", config.HELMET_LANGUAGE_CODE)
        elif prev == CallState.CALLING:
            speak("Call not answered.", config.HELMET_LANGUAGE_CODE)
        elif prev == CallState.INCOMING:
            speak("Missed call.", config.HELMET_LANGUAGE_CODE)
        log.info("[CALL] Slot %s returned to IDLE.", self.slot_id)

    # ─── Internal actions ─────────────────────────────────────────────────────

    def _initiate(self) -> None:
        with self._lock:
            self._state = CallState.CALLING
        log.info("[CALL] Calling %s…", self.slot_id)
        self._send_request(self.slot_id)
        speak(f"Calling {self.partner_label}.", config.HELMET_LANGUAGE_CODE)

    def _cancel(self) -> None:
        with self._lock:
            self._state = CallState.IDLE
        self._send_end(self.slot_id)
        speak("Call cancelled.", config.HELMET_LANGUAGE_CODE)
        log.info("[CALL] Outgoing call to %s cancelled.", self.slot_id)

    def _answer(self) -> None:
        with self._lock:
            self._state = CallState.IN_CALL
        self._cancel_ring_timer()
        self._send_accept(self.slot_id)
        log.info("[CALL] Answered call from %s.", self.slot_id)
        self._start_audio()
        speak("Call connected.", config.HELMET_LANGUAGE_CODE)

    def _hangup(self, local: bool = True) -> None:
        with self._lock:
            self._state = CallState.IDLE
        self._stop_audio()
        if local:
            self._send_end(self.slot_id)
        speak("Call ended.", config.HELMET_LANGUAGE_CODE)
        log.info("[CALL] Hung up on %s.", self.slot_id)

    def _ring_timeout(self) -> None:
        with self._lock:
            if self._state != CallState.INCOMING:
                return
            self._state = CallState.IDLE
        self._send_end(self.slot_id)
        speak("Missed call.", config.HELMET_LANGUAGE_CODE)
        log.info("[CALL] Ring timeout – %s not answered.", self.slot_id)

    def _cancel_ring_timer(self) -> None:
        if self._ring_timer:
            self._ring_timer.cancel()
            self._ring_timer = None

    def _start_audio(self) -> None:
        self._live_call = self._make_live_call(self.partner_ip)
        log.info("[CALL] Audio streaming started (%s).", self.slot_id)

    def _stop_audio(self) -> None:
        if self._live_call:
            self._live_call.stop()
            self._live_call = None
        log.info("[CALL] Audio streaming stopped (%s).", self.slot_id)
