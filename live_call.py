# live_call.py – Full-duplex live audio call (intercom mode).

import os
import socket
import struct
import threading
import queue
import logging
import speaker_mode
import platform
import tempfile
import subprocess
import random

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
        sock = None
        stream = None
        
        # RTP Standard Variables
        ssrc = random.randint(0, 0xFFFFFFFF)  # Unique source identifier
        timestamp = 0
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            log.info("[CALL-SEND] Active -> %s:%d", self._partner_ip, self._send_port)

            if platform.system() == "Linux":
                # --- RASPBERRY PI I2S MIC FIX ---
                cmd = [
                    "arecord", "-D", config.ALSA_MIC_DEVICE, "-f", "S32_LE",
                    "-r", "48000", "-c", "1", "-t", "raw", "-q"
                ]
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE)
                
                while self._active:
                    raw_data = process.stdout.read(_FRAMES_PER_PACKET * 12) 
                    import numpy as np
                    audio_np = np.frombuffer(raw_data, dtype=np.int32)
                    audio_16 = (audio_np >> 16).astype(np.int16)
                    pcm = audio_16[::3].tobytes()
                    
                    if self._muted:
                        pcm = b"\x00" * _PACKET_BYTES
                        
                    # Build standard 12-byte RTP Header
                    # V=2, P=0, X=0, CC=0 (0x80) | M=0, PT=10 for L16 Audio (0x0A)
                    rtp_header = struct.pack(">BBHII", 0x80, 0x0A, self._seq & 0xFFFF, timestamp, ssrc)
                    sock.sendto(rtp_header + pcm, (self._partner_ip, self._send_port))
                    
                    self._seq = (self._seq + 1) & 0xFFFF
                    timestamp = (timestamp + _FRAMES_PER_PACKET) & 0xFFFFFFFF
                    
            else:
                # --- WINDOWS/MAC (PyAudio) ---
                stream = self._pa.open(
                    format=pyaudio.paInt16, channels=config.CHANNELS,
                    rate=config.SAMPLE_RATE, input=True,
                    frames_per_buffer=_FRAMES_PER_PACKET,
                )

                while self._active:
                    pcm = stream.read(_FRAMES_PER_PACKET, exception_on_overflow=False)
                    if self._muted:
                        pcm = b"\x00" * _PACKET_BYTES
                        
                    # Build standard 12-byte RTP Header
                    rtp_header = struct.pack(">BBHII", 0x80, 0x0A, self._seq & 0xFFFF, timestamp, ssrc)
                    sock.sendto(rtp_header + pcm, (self._partner_ip, self._send_port))
                    
                    self._seq = (self._seq + 1) & 0xFFFF
                    timestamp = (timestamp + _FRAMES_PER_PACKET) & 0xFFFFFFFF
                    
        except Exception:
            log.exception("[CALL-SEND] RTP crashed.")
        finally:
            if stream:
                try: stream.stop_stream(); stream.close()
                except Exception: pass
            if sock:
                sock.close()
            log.info("[CALL-SEND] Stopped.")

    def _receiver_thread(self) -> None:
        sock = None
        jitter_buffer = {}  # Dictionary to hold out-of-order packets
        next_play_seq = -1  
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self._recv_port))
            sock.settimeout(0.2)
            log.info("[CALL-RECV] RTP Listener on %d", self._recv_port)

            while self._active:
                try:
                    data, _ = sock.recvfrom(12 + _PACKET_BYTES + 64)
                except socket.timeout:
                    continue
                    
                if len(data) < 12 + _PACKET_BYTES:
                    continue
                    
                # Parse the 12-byte RTP Header
                rtp_header = data[:12]
                b1, b2, seq, timestamp, ssrc = struct.unpack(">BBHII", rtp_header)
                pcm = data[12:]
                
                # --- JITTER BUFFER LOGIC ---
                if next_play_seq == -1:
                    next_play_seq = seq  # Lock onto the very first sequence number
                    
                jitter_buffer[seq] = pcm
                
                # Play the packet if it's the exact one we are expecting next
                if next_play_seq in jitter_buffer:
                    play_pcm = jitter_buffer.pop(next_play_seq)
                    next_play_seq = (next_play_seq + 1) & 0xFFFF
                # Or skip ahead if a packet was permanently lost over Wi-Fi
                elif any(s >= next_play_seq + 3 for s in jitter_buffer.keys()):
                    next_play_seq = max(jitter_buffer.keys())
                    play_pcm = jitter_buffer.pop(next_play_seq)
                    next_play_seq = (next_play_seq + 1) & 0xFFFF
                else:
                    continue # Wait for the correct packet to arrive
                        
                # Send the correctly ordered packet to the speaker
                try:
                    self._recv_queue.put_nowait(play_pcm)
                except queue.Full:
                    try: self._recv_queue.get_nowait()
                    except queue.Empty: pass
                    self._recv_queue.put_nowait(play_pcm)
                        
                # Prevent memory leaks if sequence numbers jump wildly
                if len(jitter_buffer) > 10:
                    jitter_buffer.clear()

        except Exception:
            log.exception("[CALL-RECV] RTP crashed.")
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