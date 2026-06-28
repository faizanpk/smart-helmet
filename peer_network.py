# peer_network.py – Direct worker-to-worker TCP audio channel.
#
# Completely separate from network.py (which handles worker ↔ manager).
# Manager never sees this traffic.
#
# Each worker does two things:
#   1. Runs a small TCP SERVER so the peer can reach it.
#   2. Acts as a TCP CLIENT when sending audio to the peer.
#
# The same framing protocol as network.py is used:
#   4-byte header_len | JSON header | binary payload
#
# Usage (in main.py on worker side):
#   peer_network.start_peer_server(config.PEER_PORT, _on_peer_message)
#   peer_network.send_to_peer(config.PEER_WORKER_IP, config.PEER_PORT, mp3, meta)

import socket
import struct
import json
import threading
import logging

log = logging.getLogger(__name__)

# ─── Module state ─────────────────────────────────────────────────────────────
_peer_sock:  socket.socket | None = None   # cached outgoing peer connection
_peer_lock = threading.Lock()
_server_sock: socket.socket | None = None


# ─── Framing (identical to network.py) ───────────────────────────────────────

def _send_frame(sock: socket.socket, msg_type: str,
                payload: bytes = b"", meta: dict | None = None) -> bool:
    header = {
        "type":        msg_type,
        "payload_len": len(payload),
        "meta":        meta or {},
    }
    header_bytes = json.dumps(header).encode("utf-8")
    frame = struct.pack(">I", len(header_bytes)) + header_bytes + payload
    try:
        sock.sendall(frame)
        return True
    except OSError as exc:
        log.error("[PEER] Send failed: %s", exc)
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


def _recv_frame(sock: socket.socket) -> tuple[str | None, dict, bytes]:
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


# ─── Peer server (incoming messages from the other worker) ───────────────────

def _peer_recv_loop(conn: socket.socket, addr: str, on_message) -> None:
    """Receive audio frames from a connected peer worker."""
    log.info("[PEER] Peer connected from %s.", addr)
    while True:
        msg_type, meta, payload = _recv_frame(conn)
        if msg_type is None:
            log.warning("[PEER] Peer %s disconnected.", addr)
            break
        try:
            on_message(msg_type, meta, payload)
        except Exception as exc:
            log.error("[PEER] Callback error: %s", exc)
    conn.close()


def start_peer_server(port: int, on_message) -> None:
    """
    Start a TCP server that listens for the peer worker to connect.
    Runs in a background daemon thread.
    Each accepted connection spawns its own receive thread.
    """
    def _serve():
        global _server_sock
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(5)
        _server_sock = srv
        log.info("[PEER] Peer server listening on port %d.", port)
        while True:
            try:
                conn, addr = srv.accept()
                addr_str = f"{addr[0]}:{addr[1]}"
                threading.Thread(
                    target=_peer_recv_loop,
                    args=(conn, addr_str, on_message),
                    name="peer-recv",
                    daemon=True,
                ).start()
            except OSError as exc:
                log.error("[PEER] Server accept error: %s", exc)
                break

    threading.Thread(target=_serve, name="peer-server", daemon=True).start()


# ─── Peer client (outgoing messages to the other worker) ─────────────────────

def send_voice_message_to_peer(peer_ip: str, port: int,
                               text: str, language: str,
                               sender_id: str, is_emergency: bool = False) -> bool:
    """
    Send a text voice message directly to the peer worker.
    The message is stored on the peer and played when they press BTN_PLAY_MSG.
    Returns True on success.
    """
    import time as _time
    meta = {
        "sender_id":   sender_id,
        "sender_role": "worker",
        "text":        text,
        "language":    language,
        "channel":     "peer",
        "is_emergency": is_emergency,
        "timestamp":   _time.time(),
    }
    return _send_to_peer_internal(peer_ip, port, "voice_message", b"", meta)


def send_to_peer(peer_ip: str, port: int,
                 audio_bytes: bytes, meta: dict | None = None) -> bool:
    """
    Send raw audio bytes directly to the peer worker (legacy / live-call).
    Returns True on success.
    """
    return _send_to_peer_internal(peer_ip, port, "translation", audio_bytes, meta)


def _send_to_peer_internal(peer_ip: str, port: int,
                            msg_type: str, payload: bytes,
                            meta: dict | None = None) -> bool:
    global _peer_sock

    with _peer_lock:
        # Try to reuse existing connection
        if _peer_sock is not None:
            ok = _send_frame(_peer_sock, msg_type, payload, meta)
            if ok:
                return True
            # Connection was dead – fall through to reconnect
            _peer_sock.close()
            _peer_sock = None

        # (Re)connect to peer
        try:
            log.info("[PEER] Connecting to peer at %s:%d...", peer_ip, port)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((peer_ip, port))
            sock.settimeout(None)
            _peer_sock = sock
            log.info("[PEER] Connected to peer at %s:%d.", peer_ip, port)
            ok = _send_frame(sock, msg_type, payload, meta)
            if not ok:
                _peer_sock = None
            return ok
        except (OSError, ConnectionRefusedError) as exc:
            log.error("[PEER] Cannot reach peer at %s:%d – %s", peer_ip, port, exc)
            _peer_sock = None
            return False


def is_peer_reachable() -> bool:
    """Return True if the cached peer socket is open."""
    return _peer_sock is not None
