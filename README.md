# Smart Helmet – Communication Module

A voice-driven communication system for construction site workers built on **Raspberry Pi 4** and a **Windows laptop**, with no screens or dashboards — everything is audio only.

## Features

| Feature | Description |
|---------|-------------|
| **Push-to-Talk Translation** | Hold button → speak → partner hears translated audio (EN ↔ DE, offline) |
| **Task Reminders** | Speak a reminder with a time ("at 14:30") → plays automatically at that time |
| **Shift Handover** | Record a message for the next shift; incoming shift plays it on arrival |
| **Live Call** | Full-duplex audio intercom (no translation, real-time, ~150 ms latency) |
| **Speaker Mode** | Toggle switch raises volume when the helmet is removed |

All AI processing is **fully offline** — no internet or cloud APIs required after initial model download.

---

## Hardware (Worker Helmet)

| Component | Purpose |
|-----------|---------|
| Raspberry Pi 4 (2 GB) | Main controller |
| Adafruit SPH0645LM4H | I2S MEMS microphone |
| Adafruit MAX98357A | I2S Class-D amplifier |
| Mini oval speaker (8 Ω, 1 W) | Audio output |
| 3× Push buttons | Speak / Reminder / Handover |
| 3-pin toggle switch (ON-OFF-ON) | Speaker mode + Live Call |
| Li-Ion 3.7 V 5000 mAh + TP4056 + MT3608 | Battery + charging + 5 V boost |

**Manager side:** Windows laptop (keyboard simulates buttons).

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Speech-to-Text | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper tiny / base) |
| Translation | [ctranslate2](https://github.com/OpenNMT/CTranslate2) + Helsinki-NLP OPUS-MT models |
| Text-to-Speech | pyttsx3 (Windows SAPI / Pi espeak-ng) |
| Audio I/O | PyAudio / ALSA (Linux) |
| Networking | TCP (translation messages) + UDP (live call audio) |
| Storage | SQLite via Python `sqlite3` |
| GPIO | RPi.GPIO (Pi) / `keyboard` library (laptop simulation) |

---

## Project Structure

```
smart-helmet/
├── main.py            # Entry point – start with: python main.py manager / worker
├── config.py          # All configuration (role, IP, language, GPIO pins)
├── translation.py     # STT + translation + TTS pipeline (fully offline)
├── network.py         # TCP manager/worker communication
├── live_call.py       # Full-duplex UDP audio streaming (live call mode)
├── gpio_handler.py    # GPIO buttons on Pi / keyboard fallback on laptop
├── speaker_mode.py    # Toggle switch: volume + live call trigger
├── reminders.py       # Timed voice reminders
├── handover.py        # Shift handover record/playback
├── db.py              # SQLite database init and helpers
├── test_suite.py      # Interactive test runner (11 tests)
├── setup_pi.sh        # One-time Pi setup script (run with sudo)
├── requirements.txt   # Python dependencies
└── data/              # Runtime data (DB, model cache) – git-ignored
```

---

## Setup

### Prerequisites

- Python 3.10+ on Windows (manager laptop)
- Raspberry Pi OS Lite 64-bit (worker Pi)
- Both devices on the same Wi-Fi network

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/smart-helmet.git
cd smart-helmet
```

### 2. Manager laptop (Windows)

```powershell
# Install Python packages
python -m pip install -r requirements.txt

# Download offline AI models (~300 MB, one-time, needs internet)
python -c "from translation import setup_offline_models; setup_offline_models()"
python -c "from faster_whisper import WhisperModel; WhisperModel('tiny', device='cpu', compute_type='int8')"
```

Edit `config.py`:
```python
HELMET_ROLE           = "manager"
HELMET_LANGUAGE_CODE  = "en-US"
TARGET_LANGUAGE_CODE  = "de-DE"
HELMET_LANGUAGE_SHORT = "en"
TARGET_LANGUAGE_SHORT = "de"
WHISPER_MODEL_SIZE    = "base"
```

### 3. Worker Raspberry Pi

```bash
# Copy repo to Pi (from laptop)
scp -r smart-helmet/ pi@smart-helmet.local:/home/pi/

# On the Pi
cd /home/pi/smart-helmet
chmod +x setup_pi.sh
sudo ./setup_pi.sh   # installs packages, I2S overlays, AI models, systemd service
```

Edit `config.py` on the Pi:
```python
HELMET_ROLE           = "worker"
HELMET_LANGUAGE_CODE  = "de-DE"
TARGET_LANGUAGE_CODE  = "en-US"
HELMET_LANGUAGE_SHORT = "de"
TARGET_LANGUAGE_SHORT = "en"
PARTNER_IP            = "192.168.x.x"   # <- your laptop's IP
WHISPER_MODEL_SIZE    = "tiny"
```

Reboot: `sudo reboot`

---

## Running

**Manager (PowerShell as Administrator):**
```powershell
python main.py manager
```

**Worker (Pi):**
```bash
sudo systemctl start smart-helmet
# or manually:
python main.py worker
```

Both sides announce **"Smart helmet ready."** when connected.

---

## Controls

### Manager Laptop (keyboard)

| Key | Normal mode | During live call |
|-----|------------|-----------------|
| Hold `SPACE` | Push-to-talk + translate | Mute |
| Hold `R` | Record reminder | — |
| `H` | Play / record handover | — |
| `L` | Start live call | Stop live call |

### Worker Pi (physical buttons)

| Button | GPIO | Function |
|--------|------|---------|
| BTN_SPEAK | GPIO 17 | Push-to-talk / Mute during call |
| BTN_REMINDER | GPIO 27 | Record reminder |
| BTN_HANDOVER | GPIO 22 | Play or record handover |
| Toggle ON | GPIO 23 | Live call + loud speaker |
| Toggle OFF | GPIO 23 | PTT mode + normal volume |

---

## Testing

Run individual tests or the full suite:

```bash
python test_suite.py        # all 11 tests in order
python test_suite.py 1      # imports only
python test_suite.py 4      # translation only
python test_suite.py 11     # live call UDP loopback
```

---

## Hardware Wiring Summary

| Component | Pi Pin | GPIO |
|-----------|--------|------|
| SPH0645LM4H BCLK | Pin 12 | GPIO 18 |
| SPH0645LM4H DOUT | Pin 38 | GPIO 20 |
| SPH0645LM4H LRCL | Pin 35 | GPIO 19 |
| MAX98357A BCLK | Pin 12 | GPIO 18 |
| MAX98357A LRC | Pin 35 | GPIO 19 |
| MAX98357A DIN | Pin 40 | GPIO 21 |
| BTN_SPEAK | Pin 11 | GPIO 17 |
| BTN_REMINDER | Pin 13 | GPIO 27 |
| BTN_HANDOVER | Pin 15 | GPIO 22 |
| Toggle switch | Pin 16 | GPIO 23 |

See `USERGUIDE.md` for full wiring diagrams and step-by-step instructions.

---

## License

MIT License – see [LICENSE](LICENSE) for details.
