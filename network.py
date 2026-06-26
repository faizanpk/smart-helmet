# network.py – TCP communication hub.
#
# Protocol: length-prefixed framing (unchanged from v1).
#
#   ┌─────────────┬──────────────────────┬──────────────────┐
#   │ 4 bytes     │ N bytes              │ P bytes          │
#   │ header_len  │ JSON header          │ binary payload   │
#   └─────────────┴──────────────────────┴──────────────────┘
#
# Header JSON:
#   "type"        – message type (see MSG_* constants)
#   "payload_len" – byte length of binary payload
#   "meta"        – arbitrary dict
#
# Message types:
#   MSG_HELLO       – first message a worker sends after connecting
#                     meta: {"id": HELMET_ID, "language": HELMET_LANGUAGE_CODE}
#   MSG_TRANSLATION – translated audio to play on partner
#   MSG_CONTROL     – JSON-only status / control
#
# Roles:
#   manager → TCP server, accepts multiple worker connections simultaneously
#   worker  → TCP client, auto-reconnects, sends MSG_HELLO after each connect
#
# Manager API:
#   start_server(port, on_message)
#   send_audio_to(worker_id, audio, meta)   – targeted send to one worker
#   send_audio_to_all(audio, meta)          – broadcast to all workers
#   get_worker_ids()                        – list of currently connected IDs
#
# Worker API:
#   connect_to_server(manager_ip, port, on_message)
#   send_audio(audio, meta)                 – send to manager
#   is_connected()

import socket
import struct
import json
import threading
import logging
import time as _time
from typing import Callable, Dict, List, Optional, Tuple

import config

log = logging.getLogger(__name__)

# ─── Message type constants ───────────────────────────────────────────────────
MSG_HELLO         = "hello"
MSG_TRANSLATION   = "translation"    # legacy (kept for live_call.py compat)
MSG_CONTROL       = "control"
MSG_VOICE_MESSAGE = "voice_message"  # text message: sender sends text, receiver plays
MSG_CALL_REQUEST  = "call_request"   # caller → callee: initiate a call
MSG_CALL_ACCEPT   = "call_accept"    # callee → caller: call answered
MSG_CALL_END      = "call_end"       # either → other: hang up / cancel

# ─── Manager state (multi-worker) ────────────────────────────────────────────
_workers: Dict[str, socket.socket] = {}   # {worker_id: live socket}
_workers_lock = threading.Lock()
_server_sock: Optional[socket.socket] = None

# ─── Worker state (single manager connection) ─────────────────────────────────
_manager_sock: Optional[socket.socket] = None
_worker_send_lock = threading.Lock()

# ─── Worker connect callback (set by main.py to announce new workers) ─────────
_on_worker_connected:    Optional[Callable] = None   # (worker_id: str) -> None
_on_worker_disconnected: Optional[Callable] = None   # (worker_id: str) -> None

_send_locks: Dict[socket.socket, threading.Lock] = {}
_send_locks_guard = threading.Lock()


_on_manager_disconnected: Optional[Callable] = None  

def set_manager_event_callbacks(on_disconnect=None):
    """Register callback for manager disconnect (worker only)."""
    global _on_manager_disconnected
    _on_manager_disconnected = on_disconnect

def set_worker_event_callbacks(on_connect=None, on_disconnect=None):
    """Register callbacks for worker connection events (manager only)."""
    global _on_worker_connected, _on_worker_disconnected
    _on_worker_connected    = on_connect
    _on_worker_disconnected = on_disconnect


# ─── Internal framing helpers ─────────────────────────────────────────────────

def _get_send_lock(sock: socket.socket) -> threading.Lock:
    with _send_locks_guard:
        if sock not in _send_locks:
            _send_locks[sock] = threading.Lock()
        return _send_locks[sock]


def _send_frame(sock: socket.socket, msg_type: str,
                payload: bytes = b"", meta: Optional[dict] = None) -> bool:
    """Encode and send one framed message. Thread-safe per socket."""
    header = {
        "type":        msg_type,
        "payload_len": len(payload),
        "meta":        meta or {},
    }
    header_bytes = json.dumps(header).encode("utf-8")
    frame = struct.pack(">I", len(header_bytes)) + header_bytes + payload

    lock = _get_send_lock(sock)
    with lock:
        try:
            sock.sendall(frame)
            return True
        except OSError as exc:
            log.error("[NET] Send failed: %s", exc)
            return False


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
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


def _recv_frame(sock: socket.socket) -> Tuple[Optional[str], dict, bytes]:
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


# ─── Manager receive loop (one thread per connected worker) ───────────────────

def _manager_recv_loop(conn: socket.socket, addr: str, on_message) -> None:
    """
    Receive frames from one worker connection.
    Handles MSG_HELLO registration; passes everything else to on_message().
    """
    global _workers
    worker_id = None    # resolved after MSG_HELLO
    per_conn_lock = threading.Lock()

    while True:
        msg_type, meta, payload = _recv_frame(conn)
        if msg_type is None:
            break

        if msg_type == MSG_HELLO:
            worker_id = meta.get("id", addr)
            with _workers_lock:
                _workers[worker_id] = conn
            log.info("[NET] Worker '%s' registered (from %s).", worker_id, addr)
            if _on_worker_connected:
                try:
                    _on_worker_connected(worker_id)
                except Exception as exc:
                    log.error("[NET] on_worker_connected error: %s", exc)
        else:
            try:
                on_message(msg_type, meta, payload)
            except Exception as exc:
                log.error("[NET] Callback error: %s", exc)

    # Clean up on disconnect
    if worker_id:
        with _workers_lock:
            if _workers.get(worker_id) is conn:
                _workers.pop(worker_id, None)
        with _send_locks_guard:
            _send_locks.pop(conn, None)       
        log.warning("[NET] Worker '%s' disconnected.", worker_id)
        if _on_worker_disconnected:
            try:
                _on_worker_disconnected(worker_id)
            except Exception as exc:
                log.error("[NET] on_worker_disconnected error: %s", exc)
    else:
        log.warning("[NET] Unknown worker at %s disconnected before handshake.", addr)


# ─── Worker receive loop ──────────────────────────────────────────────────────

def _worker_recv_loop(sock: socket.socket, on_message) -> None:
    global _manager_sock
    while True:
        msg_type, meta, payload = _recv_frame(sock)
        if msg_type is None:
            log.warning("[NET] Lost manager connection.")
            _manager_sock = None
            # Notify main.py
            if _on_manager_disconnected:
                try:
                    _on_manager_disconnected()
                except Exception as exc:
                    log.error("[NET] on_manager_disconnected error: %s", exc)
            break
        try:
            on_message(msg_type, meta, payload)
        except Exception as exc:
            log.error("[NET] Callback error: %s", exc)


# ─── Public API – Manager ─────────────────────────────────────────────────────

def start_server(port: int, on_message) -> None:
    """
    (Manager) Start TCP server, accept unlimited worker connections.
    Each connection runs in its own daemon thread.
    """
    def _serve():
        global _server_sock
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(10)
        _server_sock = srv
        log.info("[NET] Manager server listening on port %d (multi-worker mode).", port)
        while True:
            try:
                conn, addr = srv.accept()
                addr_str = f"{addr[0]}:{addr[1]}"
                log.info("[NET] Incoming connection from %s", addr_str)
                t = threading.Thread(
                    target=_manager_recv_loop,
                    args=(conn, addr_str, on_message),
                    name=f"net-worker-{addr_str}",
                    daemon=True,
                )
                t.start()
            except OSError as exc:
                log.error("[NET] Server accept error: %s", exc)
                break

    threading.Thread(target=_serve, name="net-server", daemon=True).start()


def send_voice_message_to(worker_id: str, text: str, language: str) -> bool:
    """(Manager) Send a text voice message to a specific worker."""
    meta = {
        "sender_id":   config.HELMET_ID,
        "sender_role": "manager",
        "text":        text,
        "language":    language,
        "timestamp":   _time.time(),
    }
    with _workers_lock:
        sock = _workers.get(worker_id)
    if sock is None:
        log.warning("[NET] Worker '%s' not connected – message not sent.", worker_id)
        return False
    log.info("[NET] Voice msg to '%s': '%s' (%s)", worker_id, text[:40], language)
    return _send_frame(sock, MSG_VOICE_MESSAGE, b"", meta)


def send_voice_message_to_all(text: str, language: str) -> int:
    """(Manager) Broadcast a text voice message to ALL connected workers."""
    import time as _time
    meta = {
        "sender_id":   config.HELMET_ID,
        "sender_role": "manager",
        "text":        text,
        "language":    language,
        "timestamp":   _time.time(),
    }
    with _workers_lock:
        targets = list(_workers.items())
    sent = 0
    for wid, sock in targets:
        if _send_frame(sock, MSG_VOICE_MESSAGE, b"", meta):
            sent += 1
        else:
            with _workers_lock:
                _workers.pop(wid, None)
    return sent


def send_audio_to(worker_id: str, audio_bytes: bytes,
                  meta: Optional[dict] = None) -> bool:
    """(Manager) Send raw audio bytes to a specific worker (legacy / live-call)."""

    with _workers_lock:
        sock = _workers.get(worker_id)
    if sock is None:
        log.warning("[NET] Worker '%s' not connected – audio not sent.", worker_id)
        return False
    log.info("[NET] Sending %d bytes to worker '%s'.", len(audio_bytes), worker_id)
    ok = _send_frame(sock, MSG_TRANSLATION, audio_bytes, meta)
    if not ok:
        with _workers_lock:
            _workers.pop(worker_id, None)
    return ok


def send_audio_to_all(audio_bytes: bytes, meta: Optional[dict] = None) -> int:
    """(Manager) Broadcast audio to ALL connected workers. Returns count sent."""
    with _workers_lock:
        targets = list(_workers.items())
    sent = 0
    for wid, sock in targets:
        if _send_frame(sock, MSG_TRANSLATION, audio_bytes, meta):
            sent += 1
        else:
            with _workers_lock:
                _workers.pop(wid, None)
    return sent


def get_worker_ids() -> List[str]:
    """(Manager) Return sorted list of currently connected worker IDs."""
    with _workers_lock:
        return sorted(_workers.keys())


def get_worker_count() -> int:
    with _workers_lock:
        return len(_workers)


# ─── Public API – Worker ──────────────────────────────────────────────────────

def connect_to_server(manager_ip: str, port: int, on_message) -> None:
    """
    (Worker) Connect to manager with auto-reconnect.
    Sends MSG_HELLO immediately after each successful connection.
    """
    def _connect():
        global _manager_sock
        import time
        while True:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect((manager_ip, port))
                sock.settimeout(None)
                _manager_sock = sock
                log.info("[NET] Connected to manager at %s:%d.", manager_ip, port)
                # Handshake: identify ourselves
                _send_frame(sock, MSG_HELLO, b"", {
                    "id":       config.HELMET_ID,
                    "language": config.HELMET_LANGUAGE_CODE,
                })
                _worker_recv_loop(sock, on_message)
                log.warning("[NET] Lost manager – retrying in 5 s...")
            except (OSError, ConnectionRefusedError) as exc:
                log.warning("[NET] Cannot reach manager at %s:%d (%s) – retry in 5 s.",
                            manager_ip, port, exc)
            time.sleep(5)

    threading.Thread(target=_connect, name="net-client", daemon=True).start()


def send_audio(audio_bytes: bytes, meta: Optional[dict] = None) -> bool:
    """(Worker) Send raw audio bytes to the manager (legacy / live-call)."""
    if _manager_sock is None:
        log.warning("[NET] No manager connection – audio not sent.")
        return False
    log.info("[NET] Sending %d bytes to manager.", len(audio_bytes))
    return _send_frame(_manager_sock, MSG_TRANSLATION, audio_bytes, meta)


def send_voice_message(text: str, language: str) -> bool:
    """(Worker) Send a text voice message to the manager."""
    import time as _time
    if _manager_sock is None:
        log.warning("[NET] No manager connection – message not sent.")
        return False
    meta = {
        "sender_id":   config.HELMET_ID,
        "sender_role": "worker",
        "text":        text,
        "language":    language,
        "timestamp":   _time.time(),
    }
    log.info("[NET] Voice msg to manager: '%s' (%s)", text[:40], language)
    return _send_frame(_manager_sock, MSG_VOICE_MESSAGE, b"", meta)


def send_control(msg_dict: dict) -> bool:
    """(Worker) Send a JSON control message to the manager."""
    if _manager_sock is None:
        return False
    return _send_frame(_manager_sock, MSG_CONTROL, b"", msg_dict)


def is_connected() -> bool:
    """Worker: True if connected to manager. Manager: True if >=1 worker connected."""
    if config.HELMET_ROLE == "manager":
        return get_worker_count() > 0
    return _manager_sock is not None


# ─── Call signalling helpers ──────────────────────────────────────────────────
# These wrap _send_frame for the three call control messages.

def _call_meta(target_id: str, msg_type: str) -> dict:
    import time as _t
    return {
        "sender_id":   config.HELMET_ID,
        "sender_role": config.HELMET_ROLE,
        "target_id":   target_id,
        "msg_type":    msg_type,
        "timestamp":   _t.time(),
    }


def send_call_request(target_id: str) -> bool:
    """Send a call request to target_id (worker id or 'manager')."""
    meta = _call_meta(target_id, MSG_CALL_REQUEST)
    if config.HELMET_ROLE == "manager":
        with _workers_lock:
            sock = _workers.get(target_id)
        if sock is None:
            log.warning("[NET] Call: worker '%s' not connected.", target_id)
            return False
        return _send_frame(sock, MSG_CALL_REQUEST, b"", meta)
    else:
        if _manager_sock is None:
            log.warning("[NET] Call: not connected to manager.")
            return False
        return _send_frame(_manager_sock, MSG_CALL_REQUEST, b"", meta)


def send_call_accept(target_id: str) -> bool:
    """Accept an incoming call from target_id."""
    meta = _call_meta(target_id, MSG_CALL_ACCEPT)
    if config.HELMET_ROLE == "manager":
        with _workers_lock:
            sock = _workers.get(target_id)
        if sock is None:
            return False
        return _send_frame(sock, MSG_CALL_ACCEPT, b"", meta)
    else:
        if _manager_sock is None:
            return False
        return _send_frame(_manager_sock, MSG_CALL_ACCEPT, b"", meta)


def send_call_end(target_id: str) -> bool:
    """End or cancel a call with target_id."""
    meta = _call_meta(target_id, MSG_CALL_END)
    if config.HELMET_ROLE == "manager":
        with _workers_lock:
            sock = _workers.get(target_id)
        if sock is None:
            return False
        return _send_frame(sock, MSG_CALL_END, b"", meta)
    else:
        if _manager_sock is None:
            return False
        return _send_frame(_manager_sock, MSG_CALL_END, b"", meta)
