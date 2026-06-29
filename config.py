# config.py – Smart Helmet Communication Module
#
# Edit the values in each section before deploying a helmet.
# Three device types:
#   "manager" → Laptop 1 – TCP server, selects target worker with 1/2/3 keys
#   "worker"  → Pi or Laptop 2 – TCP client, two PTT channels (manager + peer)

import os

# ─── Data Storage ─────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR      = os.path.join(BASE_DIR, "data")
DB_PATH       = os.path.join(DATA_DIR, "helmet.db")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

# ─── Helmet Identity ──────────────────────────────────────────────────────────
# HELMET_ID must be unique across all helmets in the same session.
#   manager   → "manager"
#   worker 1  → "w-01"   (Pi)
#   worker 2  → "w-02"   (Laptop 2)
HELMET_ROLE = "manager"
HELMET_ID   = "manager"

# ─── Language Configuration ───────────────────────────────────────────────────
# HELMET_LANGUAGE = the language THIS user speaks and wants to hear.
#   "en"  →  English speaker / listener
#   "de"  →  German speaker / listener
#
# This is the PRIMARY setting. All BCP-47 codes below are derived from it.
# You can also change this at runtime via voice command (long-hold PLAY button).
# The setting is saved to data/settings.json and loaded on next boot.
#
HELMET_LANGUAGE = "en"   # "en" or "de"
HELMET_LANGUAGE_SHORT = "en"

# BCP-47 codes (derived in main.py from HELMET_LANGUAGE via apply_language_setting)
# Set manually here only if you are NOT using the voice configuration feature.
HELMET_LANGUAGE_CODE  = "en-US"   # used for STT language hint and local TTS

TARGET_LANGUAGE_CODE  = "de-DE"   # opposite language BCP-47
TARGET_LANGUAGE_SHORT = "de"      # opposite language ISO-639-1

USE_GOOGLE_TRANSLATE = True       # Translation — Google Translate via deep-translator

# ─── Offline STT Model ────────────────────────────────────────────────────────
# "tiny"  – 75 MB,  fast, recommended for Pi
# "base"  – 145 MB, better accuracy, recommended for laptop
WHISPER_MODEL_SIZE = "tiny"

# Piper TTS voice models
PIPER_VOICES_DIR = os.path.join(DATA_DIR, "piper_voices")
PIPER_VOICE_EN = os.path.join(PIPER_VOICES_DIR, "en_US-lessac-medium.onnx")
PIPER_VOICE_DE = os.path.join(PIPER_VOICES_DIR, "de_DE-thorsten-medium.onnx")

# ─── Network – Manager ────────────────────────────────────────────────────────
MANAGER_IP = "192.168.43.210"      # ← Laptop 1 (manager) LAN IP
COMM_PORT  = 5005                 # TCP port: worker ↔ manager

# Legacy alias kept for live_call.py
PARTNER_IP = MANAGER_IP

# ─── Network – Workers (manager needs each worker's IP to call/message them) ──
WORKER_IPS = {
    "worker 1": "192.168.43.138",      # Raspberry Pi worker
    "worker 2": "192.168.43.145",      # Laptop worker
}
PEER_PORT      = 5007             # TCP port: worker ↔ worker

_FIXED_WORKER_ORDER = ["worker 1", "worker 2"]

# ─── Live Call ────────────────────────────────────────────────────────────────
LIVE_CALL_PORT = 5006             # UDP port for full-duplex intercom

# ─── GPIO Pin Numbers (BCM numbering, Raspberry Pi) ─────────────────────────────────────
BTN_SPEAK_MANAGER = 17   # Hold → PTT to manager           (Pin 11)
BTN_SPEAK_WORKER  = 24   # Hold → PTT to peer worker       (Pin 18)
BTN_REMINDER      = 27   # Hold → record reminder          (Pin 13)
BTN_HANDOVER      = 22   # Press → play/record handover    (Pin 15)
BTN_PLAY_MSG      = 25   # Short → play message            (Pin 22)
BTN_CALL_MANAGER  = 23   # Press → call/answer/end manager (Pin 16)  ← push button
LED_MSG_PIN       = 5    # Output → message alert LED      (Pin 29)

# Manager-only: one call button per worker (keyboard keys F1/F2)
# Workers do not need these – they only call the manager.
BTN_CALL_W01 = None   # F1 key on manager laptop  (w-01 = Pi)
BTN_CALL_W02 = None   # F2 key on manager laptop  (w-02 = Laptop 2)

# Legacy alias
BTN_SPEAK = BTN_SPEAK_MANAGER

# Port offset for LiveCall UDP: 0 = w-01, 2 = w-02
# Keeps manager↔w-01 on ports 5006/5007 and manager↔w-02 on 5008/5009
CALL_PORT_OFFSETS = {
    "worker 1": 0,
    "worker 2": 2,
}

# ─── Laptop Keyboard Simulation ───────────────────────────────────────────────────────────
# Manager (Laptop 1):
#   SPACE    → BTN_SPEAK_MANAGER   (send PTT to selected worker)
#   1/2/3    → select target worker for PTT
#   p        → BTN_PLAY_MSG
#   F1       → BTN_CALL_W01        (call/answer/end Worker A)
#   F2       → BTN_CALL_W02        (call/answer/end Worker B)
#   r        → BTN_REMINDER
#   h        → BTN_HANDOVER
#   Hold SPACE during call → mute
#
# Worker (Laptop 2):
#   SPACE    → BTN_SPEAK_MANAGER   (send PTT to manager)
#   w        → BTN_SPEAK_WORKER    (send PTT to peer worker)
#   p        → BTN_PLAY_MSG
#   c        → BTN_CALL_MANAGER    (call/answer/end manager)
#   r        → BTN_REMINDER
#   h        → BTN_HANDOVER

# ─── Audio Recording ──────────────────────────────────────────────────────────
SAMPLE_RATE        = 16000
CHANNELS           = 1
CHUNK              = 1024
RECORD_SECONDS_MAX = 60

# ALSA device identifiers on Raspberry Pi
ALSA_MIC_DEVICE = "plughw:1,0"
ALSA_SPK_DEVICE = "plughw:1,0"

# ─── Volume ──────────────────────────────────────────────────────────────────────────────
VOLUME_NORMAL  = 80
VOLUME_SPEAKER = 100

# Loudspeaker mode toggle switch
SWITCH_LOUDSPEAKER = 26   # BCM pin — pick any free GPIO pin, confirm against your wiring

# Two separate physical speaker outputs
ALSA_SPK_DEVICE_INTERNAL = "plughw:0,0"     # normal/internal speaker — confirm via aplay -l
ALSA_SPK_DEVICE_EXTERNAL = "plughw:1,0"     # external loudspeaker — confirm via aplay -l
