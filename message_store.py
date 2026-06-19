# message_store.py – Thread-safe queue for received voice messages.
#
# Messages are stored here on arrival and played manually via BTN_PLAY_MSG.
# The manager and workers both use this to hold incoming messages until
# the user explicitly presses the play button.
#
# Each message dict:
#   sender_id   : str   – e.g. "w-01" or "manager"
#   sender_role : str   – "worker" or "manager"
#   text        : str   – transcribed text from sender
#   language    : str   – source language short code ("en" or "de")
#   channel     : str   – "manager" (via network.py) or "peer" (via peer_network.py)
#   timestamp   : float – time.time() when received

import threading
import time
import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_queue: List[Dict] = []
_lock  = threading.Lock()


def push(sender_id:   str,
         sender_role: str,
         text:        str,
         language:    str,
         channel:     str = "manager") -> None:
    """Add a received message to the end of the queue."""
    msg = {
        "sender_id":   sender_id,
        "sender_role": sender_role,
        "text":        text,
        "language":    language,
        "channel":     channel,
        "timestamp":   time.time(),
    }
    with _lock:
        _queue.append(msg)
    log.info("[MSG] Queued from '%s' (%s lang=%s). Queue: %d",
             sender_id, channel, language, count())


def peek() -> Optional[Dict]:
    """Return the oldest message without removing it. None if empty."""
    with _lock:
        return dict(_queue[0]) if _queue else None


def pop() -> Optional[Dict]:
    """Remove and return the oldest message. None if empty."""
    with _lock:
        return _queue.pop(0) if _queue else None


def count() -> int:
    """Return number of pending messages."""
    with _lock:
        return len(_queue)


def has_pending() -> bool:
    return count() > 0


def clear() -> None:
    """Discard all pending messages (e.g. on shutdown)."""
    with _lock:
        _queue.clear()
    log.info("[MSG] Queue cleared.")
