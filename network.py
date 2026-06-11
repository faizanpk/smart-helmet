# network.py – TCP communication between manager laptop and worker Pi.
#
# Protocol:  all messages use a simple length-prefixed framing scheme.
#
#   ┌─────────────┬──────────────────────┬──────────────────┐
#   │ 4 bytes     │ N bytes              │ P bytes          │
#   │ header_len  │ JSON header          │ binary payload   │
#   └─────────────┴──────────────────────┴──────────────────┘
#
# Header JSON fields:
#   "type"        – message type string (see MSG_* constants)
#   "payload_len" – byte length of the binary payload that follows
#   "meta"        – arbitrary dict for per-message metadata
#
# Message types:
#   MSG_TRANSLATION – translated MP3 audio to play on the partner helmet
#   MSG_CONTROL     – JSON-only control / status message
#
# Roles:
#   manager  → starts TCP server, waits for worker to connect
#   worker   → connects to manager, auto-reconnects on drop

import socket
import struct
import json
import threading
import logging

import config

log = logging.getLogger(__name__)

# ─── Message type constants ───────────────────────────────────────────────────
MSG_TRANSLATION = "translation"
MSG_CONTROL     = "control"

# ─── Module-level socket state ────────────────────────────────────────────────
_server_sock:  socket.socket | None = None   # server listening socket
_active_sock:  socket.socket | None = None   # the live connected socket
_send_lock = threading.Lock()


# ─── Internal framing helpers ─────────────────────────────────────────────────

def _send_frame(sock: socket.socket, msg_type: str,
                payload: bytes = b"", meta: dict | None = None) -> bool:
    """Encode and send one framed message. Thread-safe."""
    header = {
        "type":        msg_type,
        "payload_len": len(payload),
        "meta":        meta or {},
    }
    header_bytes = json.dumps(header).encode("utf-8")
    frame = struct.pack(">I", len(header_bytes)) + header_bytes + payload
    with _send_lock:
        try:
            sock.sendall(frame)
            return True
        except OSError as exc:
            log.error("[NET] Send failed: %s", exc)
            return False


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    """Receive exactly n bytes from socket, or return None on disconnect."""
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def _recv_frame(sock: socket.socket) -> tuple[str | None, dict, bytes]:
    """Receive one framed message. Returns (msg_type, meta, payload)."""
    raw = _recv_exact(sock, 4)
    if raw is None:
        return None, {}, b""
    header_len = struct.unpack(">I", raw)[0]
    header_raw = _recv_exact(sock, header_len)
    if header_raw is None:
        return None, {}, b""
    try:
        header = json.loads(header_raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None, {}, b""
    payload_len = header.get("payload_len", 0)
    payload = _recv_exact(sock, payload_len) if payload_len > 0 else b""
    return header.get("type"), header.get("meta", {}), payload or b""


# ─── Receive loop (runs in background thread) ─────────────────────────────────

def _receive_loop(sock: socket.socket, callback) -> None:
    """Read frames from sock and call callback(msg_type, meta, payload)."""
    global _active_sock
    while True:
        msg_type, meta, payload = _recv_frame(sock)
        if msg_type is None:
            log.warning("[NET] Connection closed or error – stopping receive loop.")
            _active_sock = None
            break
        try:
            callback(msg_type, meta, payload)
        except Exception as exc:
            log.error("[NET] Callback error: %s", exc)


# ─── Public API ───────────────────────────────────────────────────────────────

def start_server(port: int, on_message) -> None:
    """
    (Manager) Start a TCP server and wait for exactly one worker to connect.
    Runs in a background daemon thread; reconnects if the worker drops.
    """
    def _serve():
        global _server_sock, _active_sock
        _server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _server_sock.bind(("0.0.0.0", port))
        _server_sock.listen(1)
        log.info("[NET] Manager server listening on port %d", port)
        while True:
            try:
                conn, addr = _server_sock.accept()
                _active_sock = conn
                log.info("[NET] Worker connected from %s", addr)
                _receive_loop(conn, on_message)
                log.info("[NET] Worker disconnected – waiting for reconnect...")
            except OSError as exc:
                log.error("[NET] Server error: %s", exc)
                break

    t = threading.Thread(target=_serve, name="net-server", daemon=True)
    t.start()


def connect_to_server(partner_ip: str, port: int, on_message) -> None:
    """
    (Worker) Connect to the manager server with automatic reconnection.
    Runs in a background daemon thread.
    """
    def _connect():
        global _active_sock
        import time
        while True:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect((partner_ip, port))
                sock.settimeout(None)
                _active_sock = sock
                log.info("[NET] Connected to manager at %s:%d", partner_ip, port)
                _receive_loop(sock, on_message)
                log.warning("[NET] Lost connection – retrying in 5 s...")
            except (OSError, ConnectionRefusedError) as exc:
                log.warning("[NET] Cannot connect to %s:%d (%s) – retry in 5 s",
                            partner_ip, port, exc)
            time.sleep(5)

    t = threading.Thread(target=_connect, name="net-client", daemon=True)
    t.start()


def send_audio(audio_bytes: bytes, meta: dict | None = None) -> bool:
    """Send translated MP3 audio to the partner helmet."""
    if _active_sock is None:
        log.warning("[NET] No active connection – audio not sent.")
        return False
    log.info("[NET] Sending audio (%d bytes)", len(audio_bytes))
    return _send_frame(_active_sock, MSG_TRANSLATION, audio_bytes, meta)


def send_control(msg_dict: dict) -> bool:
    """Send a JSON-only control message to the partner helmet."""
    if _active_sock is None:
        log.warning("[NET] No active connection – control msg not sent.")
        return False
    return _send_frame(_active_sock, MSG_CONTROL, b"", msg_dict)


def is_connected() -> bool:
    """Return True if a live partner connection exists."""
    return _active_sock is not None
