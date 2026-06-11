# config.py – Smart Helmet Communication Module
# Adjust HELMET_ROLE, HELMET_LANGUAGE_*, TARGET_LANGUAGE_*, and PARTNER_IP
# before deploying each helmet.

import os

# ─── Helmet Identity ──────────────────────────────────────────────────────────
# "manager" → runs on laptop, acts as TCP server, uses keyboard simulation
# "worker"  → runs on Raspberry Pi, acts as TCP client, uses GPIO buttons
HELMET_ROLE = "worker"
HELMET_ID   = "helmet-01"

# ─── Language Configuration ───────────────────────────────────────────────────
# HELMET_LANGUAGE_*  = language THIS helmet's user speaks
# TARGET_LANGUAGE_*  = language the PARTNER helmet's user speaks

# BCP-47 codes used by faster-whisper STT and pyttsx3 TTS
HELMET_LANGUAGE_CODE  = "en-US"   # STT / TTS language for this helmet
TARGET_LANGUAGE_CODE  = "de-DE"   # TTS language for audio sent to partner

# Short ISO-639-1 codes for argostranslate
HELMET_LANGUAGE_SHORT = "en"      # translate FROM
TARGET_LANGUAGE_SHORT = "de"      # translate TO

# ─── Offline STT Model ────────────────────────────────────────────────────────
# faster-whisper model size: "tiny" (75 MB, fast, good for Pi)
#                            "base" (145 MB, better accuracy, good for laptop)
#                            "small" (465 MB, best quality, needs >1 GB RAM)
WHISPER_MODEL_SIZE = "tiny"   # change to "base" on manager laptop if desired

# ─── Network ──────────────────────────────────────────────────────────────────
# Manager laptop IP – worker Pi must point to this address.
# Manager sets MY_IP via PARTNER_IP on the worker config.
PARTNER_IP = "192.168.1.102"      # ← set to manager laptop's LAN IP
COMM_PORT  = 5005                 # TCP port for all inter-helmet communication
LIVE_CALL_PORT = 5006             # UDP port for live call audio streaming

# ─── GPIO Pin Numbers (BCM numbering) ────────────────────────────────────────
BTN_SPEAK      = 17   # Push-to-talk translation
BTN_REMINDER   = 27   # Record / trigger reminder
BTN_HANDOVER   = 22   # Record or playback shift handover
SWITCH_SPEAKER = 23   # Toggle switch: speaker mode (ON = helmet removed)

# ─── Audio Recording ──────────────────────────────────────────────────────────
SAMPLE_RATE        = 16000   # Hz  (Google STT requires 8k or 16k)
CHANNELS           = 1       # Mono
CHUNK              = 1024    # Frames per buffer
RECORD_SECONDS_MAX = 60      # Hard cap on hold-to-talk duration (seconds)

# ALSA device identifiers on Raspberry Pi
# Find correct values with:  arecord -l   (mic)   aplay -l   (speaker)
# Typically the I2S mic/amp appear as card 1 when onboard audio is card 0.
ALSA_MIC_DEVICE = "plughw:1,0"   # SPH0645LM4H  – I2S microphone
ALSA_SPK_DEVICE = "plughw:1,0"   # MAX98357A     – I2S amplifier

# ─── Volume ───────────────────────────────────────────────────────────────────
VOLUME_NORMAL  = 80    # % – normal helmet-on volume
VOLUME_SPEAKER = 100   # % – speaker mode volume (helmet removed)

# ─── Data Storage ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH  = os.path.join(DATA_DIR, "helmet.db")

# ─── Laptop Keyboard Simulation (manager / development) ──────────────────────
# When not on a Pi, these keyboard keys simulate GPIO button presses:
#   SPACE  → BTN_SPEAK
#   r      → BTN_REMINDER
#   h      → BTN_HANDOVER
# Toggle-switch simulation is not available on laptop (always OFF).