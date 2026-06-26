# live_call.py – Full-duplex live audio call (intercom mode).

import socket
import struct
import threading
import queue
import logging
import speaker_mode

import pyaudio

import config

log = logging.getLogger(__name__)

_FRAMES_PER_PACKET = int(config.SAMPLE_RATE * 0.020)
_PACKET_BYTES      = _FRAMES_PER_PACKET * 2
_JITTER_QUEUE_SIZE = 6


class LiveCall:
    def __init__(self):
        self._partner_ip = ""
        self._send_port  = 0
        self._recv_port  = 0
        self._active     = False
        self._muted      = False
        self._seq        = 0
        self._recv_queue: queue.Queue = queue.Queue(maxsize=_JITTER_QUEUE_SIZE * 2)
        self._pa = None   # single shared PyAudio instance for this call

    def start(self, partner_ip: str, port_offset: int = 0) -> None:
        if self._active:
            return
        self._partner_ip = partner_ip
        self._active = True
        self._seq = 0

        base = config.LIVE_CALL_PORT + port_offset
        if config.HELMET_ROLE == "manager":
            self._send_port, self._recv_port = base, base + 1
        else:
            self._send_port, self._recv_port = base + 1, base

        try:
            self._pa = pyaudio.PyAudio()   # ← created ONCE, before any thread starts
        except Exception:
            log.exception("[CALL] Failed to initialise PyAudio.")
            self._active = False
            return

        log.info("[CALL] Starting -> %s  send=%d recv=%d",
                 partner_ip, self._send_port, self._recv_port)

        threading.Thread(target=self._sender_thread,   daemon=True, name="call-send").start()
        threading.Thread(target=self._receiver_thread, daemon=True, name="call-recv").start()
        threading.Thread(target=self._player_thread,   daemon=True, name="call-play").start()

    def _cleanup_pa(self) -> None:
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
    
    def stop(self) -> None:
        self._active = False
        log.info("[CALL] Stopping...")
        threading.Timer(0.5, self._cleanup_pa).start()

    def mute(self, muted: bool) -> None:
        self._muted = muted

    @property
    def is_active(self) -> bool:
        return self._active

    def _sender_thread(self) -> None:
        stream = None
        sock = None
        try:
            stream = self._pa.open(
                format=pyaudio.paInt16, channels=config.CHANNELS,
                rate=config.SAMPLE_RATE, input=True,
                frames_per_buffer=_FRAMES_PER_PACKET,
            )
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            log.info("[CALL-SEND] Active -> %s:%d", self._partner_ip, self._send_port)

            while self._active:
                pcm = stream.read(_FRAMES_PER_PACKET, exception_on_overflow=False)
                if self._muted:
                    pcm = b"\x00" * _PACKET_BYTES
                header = struct.pack(">I", self._seq)
                sock.sendto(header + pcm, (self._partner_ip, self._send_port))
                self._seq = (self._seq + 1) % 0xFFFFFFFF
        except Exception:
            log.exception("[CALL-SEND] Crashed.")
        finally:
            if stream:
                try: stream.stop_stream(); stream.close()
                except Exception: pass
            if sock:
                sock.close()
            log.info("[CALL-SEND] Stopped.")

    def _receiver_thread(self) -> None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self._recv_port))
            sock.settimeout(0.2)
            log.info("[CALL-RECV] Listening on %d", self._recv_port)

            while self._active:
                try:
                    data, _ = sock.recvfrom(4 + _PACKET_BYTES + 64)
                except socket.timeout:
                    continue
                if len(data) < 4 + _PACKET_BYTES:
                    continue
                pcm = data[4:]
                try:
                    self._recv_queue.put_nowait(pcm)
                except queue.Full:
                    try: self._recv_queue.get_nowait()
                    except queue.Empty: pass
                    self._recv_queue.put_nowait(pcm)
        except Exception:
            log.exception("[CALL-RECV] Crashed.")
        finally:
            if sock:
                sock.close()
            log.info("[CALL-RECV] Stopped.")

    def _find_device(self, p, alsa_name: str, is_input: bool):
        if not alsa_name:
            return None
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if alsa_name in info.get("name", ""):
                if is_input and info["maxInputChannels"] > 0:
                    return i
                if not is_input and info["maxOutputChannels"] > 0:
                    return i
        return None

    def _player_thread(self) -> None:
        stream = None
        try:
            output_idx = self._find_device(self._pa, speaker_mode.get_speaker_device(), is_input=False)
            stream = self._pa.open(
                format=pyaudio.paInt16, channels=config.CHANNELS,
                rate=config.SAMPLE_RATE, output=True,
                output_device_index=output_idx,
                frames_per_buffer=_FRAMES_PER_PACKET,
            )
            silence = b"\x00" * _PACKET_BYTES
            log.info("[CALL-PLAY] Active.")

            while self._active:
                try:
                    pcm = self._recv_queue.get(timeout=0.2)
                except queue.Empty:
                    pcm = silence
                stream.write(pcm)
        except Exception:
            log.exception("[CALL-PLAY] Crashed.")
        finally:
            if stream:
                try: stream.stop_stream(); stream.close()
                except Exception: pass
            log.info("[CALL-PLAY] Stopped.")