# live_call.py – Full-duplex live audio call (intercom mode).
#
# Activated by CallManager when a call is accepted.
# Streams raw PCM audio between manager and one worker over UDP.
#
# Design:
#   - Two threads per side: one sender (mic → UDP), one receiver (UDP → speaker)
#   - No STT / translation / TTS – raw audio only
#   - Packet format: 4-byte sequence number + raw PCM frames (~20 ms per packet)
#   - BTN_SPEAK_MANAGER held during a call = MUTE (stops sending mic audio)
#   - Jitter buffer: receiver keeps a small queue to smooth out network variation
#
# Port assignment (avoids collision when manager calls two workers):
#   manager ↔ w-01 : manager sends on 5006, receives on 5007
#                     worker  sends on 5007, receives on 5006
#   manager ↔ w-02 : manager sends on 5008, receives on 5009
#                     worker  sends on 5009, receives on 5008
#
# LIVE_CALL_PORT is the BASE port; call_manager passes the correct offset.

import socket
import struct
import threading
import queue
import time
import platform
import logging

import pyaudio

import config

log = logging.getLogger(__name__)

# ─── Audio packet settings ────────────────────────────────────────────────────
# 20 ms of audio per packet – good balance of latency vs overhead
_FRAMES_PER_PACKET = int(config.SAMPLE_RATE * 0.020)   # 320 frames @ 16 kHz
_PACKET_BYTES      = _FRAMES_PER_PACKET * 2             # 640 bytes (16-bit mono)
_JITTER_QUEUE_SIZE = 4                                  # ~80 ms jitter buffer


class LiveCall:
    """
    Full-duplex UDP audio streaming between two helmets.

    Usage (via CallManager):
        call = LiveCall()          # create once
        call.start(partner_ip, port_offset)   # start for this call session
        call.mute(True)            # mute mic
        call.mute(False)           # unmute
        call.stop()                # end call

    port_offset: 0 for w-01, 2 for w-02 (so ports don't overlap)
    """

    def __init__(self):
        self._partner_ip: str = ""
        self._send_port:  int = 0
        self._recv_port:  int = 0
        self._active     = False
        self._muted      = False
        self._lock       = threading.Lock()
        self._recv_queue: queue.Queue = queue.Queue(maxsize=_JITTER_QUEUE_SIZE * 2)
        self._seq = 0

    # ─── Public interface ───────────────────────────────────────────────────────────────────────

    def start(self, partner_ip: str, port_offset: int = 0) -> None:
        """
        Begin the live call: configure ports and start audio threads.

        partner_ip  : IP address of the remote helmet
        port_offset : 0 for w-01 / manager↔w-01,
                      2 for w-02 / manager↔w-02
                      (keeps port pairs from overlapping)
        """
        with self._lock:
            if self._active:
                return
            self._partner_ip = partner_ip
            self._active     = True
            self._seq        = 0

        # Port assignment based on role:
        #   manager sends on BASE+offset, receives on BASE+offset+1
        #   worker  sends on BASE+offset+1, receives on BASE+offset
        base = config.LIVE_CALL_PORT + port_offset
        if config.HELMET_ROLE == "manager":
            self._send_port = base
            self._recv_port = base + 1
        else:
            self._send_port = base + 1
            self._recv_port = base

        # Reset jitter queue for new call
        while not self._recv_queue.empty():
            try:
                self._recv_queue.get_nowait()
            except Exception:
                break

        log.info("[CALL] Starting: %s  send=%d  recv=%d",
                 partner_ip, self._send_port, self._recv_port)

        threading.Thread(target=self._sender_thread,   name="call-send", daemon=True).start()
        threading.Thread(target=self._receiver_thread, name="call-recv", daemon=True).start()
        threading.Thread(target=self._player_thread,   name="call-play", daemon=True).start()

    def stop(self) -> None:
        """End the live call: stop all threads."""
        with self._lock:
            self._active = False
        log.info("[CALL] Live call ended.")

    def mute(self, muted: bool) -> None:
        """Mute or unmute the microphone (BTN_SPEAK controls this during a call)."""
        self._muted = muted
        log.info("[CALL] %s.", "Muted" if muted else "Unmuted")

    @property
    def is_active(self) -> bool:
        return self._active

    # ─── Sender thread (mic → UDP) ─────────────────────────────────────────────

    def _sender_thread(self) -> None:
        """Read mic audio and send UDP packets to partner."""
        p = pyaudio.PyAudio()
        input_idx = self._find_device(p, config.ALSA_MIC_DEVICE, is_input=True)

        stream = p.open(
            format=pyaudio.paInt16,
            channels=config.CHANNELS,
            rate=config.SAMPLE_RATE,
            input=True,
            input_device_index=input_idx,
            frames_per_buffer=_FRAMES_PER_PACKET,
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        log.info("[CALL-SEND] Sender active -> %s:%d", self._partner_ip, self._send_port)
        try:
            while self._active:
                pcm = stream.read(_FRAMES_PER_PACKET, exception_on_overflow=False)

                if self._muted:
                    # Send silence so receiver doesn't stall / buffer empties
                    pcm = b"\x00" * _PACKET_BYTES

                header = struct.pack(">I", self._seq)
                sock.sendto(header + pcm, (self._partner_ip, self._send_port))
                self._seq = (self._seq + 1) % 0xFFFFFFFF
        except Exception as exc:
            log.error("[CALL-SEND] Error: %s", exc)
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
            sock.close()
            log.info("[CALL-SEND] Sender stopped.")

    # ─── Receiver thread (UDP → queue) ────────────────────────────────────────

    def _receiver_thread(self) -> None:
        """Receive UDP packets from partner and push into jitter queue."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self._recv_port))
        sock.settimeout(0.1)   # allow checking _active flag

        log.info("[CALL-RECV] Listening on port %d", self._recv_port)
        try:
            while self._active:
                try:
                    data, _ = sock.recvfrom(4 + _PACKET_BYTES + 64)
                except socket.timeout:
                    continue
                if len(data) < 4 + _PACKET_BYTES:
                    continue
                # Strip sequence header, push PCM to queue
                pcm = data[4:]
                try:
                    self._recv_queue.put_nowait(pcm)
                except queue.Full:
                    # Drop oldest packet to avoid growing buffer
                    try:
                        self._recv_queue.get_nowait()
                    except queue.Empty:
                        pass
                    self._recv_queue.put_nowait(pcm)
        except Exception as exc:
            log.error("[CALL-RECV] Error: %s", exc)
        finally:
            sock.close()
            log.info("[CALL-RECV] Receiver stopped.")

    # ─── Player thread (queue → speaker) ──────────────────────────────────────

    def _player_thread(self) -> None:
        """Dequeue PCM packets and play them through the speaker."""
        p = pyaudio.PyAudio()
        output_idx = self._find_device(p, config.ALSA_SPK_DEVICE, is_input=False)

        stream = p.open(
            format=pyaudio.paInt16,
            channels=config.CHANNELS,
            rate=config.SAMPLE_RATE,
            output=True,
            output_device_index=output_idx,
            frames_per_buffer=_FRAMES_PER_PACKET,
        )

        # Pre-fill jitter buffer with silence before playing
        silence = b"\x00" * _PACKET_BYTES
        for _ in range(_JITTER_QUEUE_SIZE):
            while self._active:
                try:
                    self._recv_queue.put_nowait(silence)
                    break
                except queue.Full:
                    break

        log.info("[CALL-PLAY] Player active.")
        try:
            while self._active:
                try:
                    pcm = self._recv_queue.get(timeout=0.2)
                except queue.Empty:
                    pcm = silence   # play silence if no packet arrived
                stream.write(pcm)
        except Exception as exc:
            log.error("[CALL-PLAY] Error: %s", exc)
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
            log.info("[CALL-PLAY] Player stopped.")

    # ─── ALSA device helper ───────────────────────────────────────────────────

    @staticmethod
    def _find_device(p: pyaudio.PyAudio, alsa_name: str, is_input: bool):
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if alsa_name in info.get("name", ""):
                if is_input  and info["maxInputChannels"]  > 0:
                    return i
                if not is_input and info["maxOutputChannels"] > 0:
                    return i
        return None   # use system default
