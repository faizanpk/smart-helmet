# Smart Helmet — User Guide

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Hardware Components](#2-hardware-components)
3. [Wiring Guide](#3-wiring-guide)
4. [Software Setup](#4-software-setup)
5. [Configuration](#5-configuration)
6. [Controls Reference](#6-controls-reference)
7. [How to Use — Step by Step](#7-how-to-use--step-by-step)
8. [Testing Before First Use](#8-testing-before-first-use)

---

## 1. System Overview

Three helmet types, one Wi-Fi network:

```
 ┌──────────────────────────────────────────────────────────┐
 │              Wi-Fi / Local Network (LAN)                  │
 │                                                           │
 │   Manager (Laptop 1)  ──port 5005──  Worker 1 (Pi)       │
 │         │                                 │               │
 │         │             ─────────────port 5007──            │
 │         │                                 │               │
 │   Manager (Laptop 1)  ──port 5005──  Worker 2 (Laptop 2) │
 └──────────────────────────────────────────────────────────┘
```

| Device | Role |
|--------|------|
| **Laptop 1** | Manager — selects which worker to talk to |
| **Pi (Helmet 1)** | Worker — two PTT channels: manager + peer worker |
| **Laptop 2 (Helmet 2)** | Worker — same as Pi, keyboard-controlled |

### Language System
- Every helmet is configured with **one language** ("English" or "German").
- When a message arrives, it is stored silently.
- When you press the **PLAY button**, the system reads a preview and plays the message in your configured language — translating automatically if needed.
- You can change your language any time via voice command (see Section 7.5).

---

## 2. Hardware Components

### Manager (Laptop 1)
No extra hardware needed. Controlled entirely by keyboard.

### Worker Pi Helmet

| Component | Purpose |
|-----------|---------|
| Raspberry Pi 4 | Main compute unit |
| SPH0645LM4H I2S microphone | Voice capture |
| MAX98357A I2S amplifier + speaker | Voice playback |
| Push button × 5 | PTT (manager), PTT (worker), Play message, Reminder, Handover |
| ON-OFF toggle switch | Speaker mode / live call |
| LED + 220 Ω resistor | Message indicator (blinks when message arrives) |

### Worker Laptop 2
No extra hardware. Controlled by keyboard.

---

## 3. Wiring Guide (Pi)

### Microphone — SPH0645LM4H I2S

| Mic Pin | Pi Pin | GPIO |
|---------|--------|------|
| VDD | 3.3 V (Pin 1) | — |
| GND | GND (Pin 6) | — |
| BCLK | Pin 12 | GPIO 18 |
| LRCL | Pin 35 | GPIO 19 |
| DOUT | Pin 38 | GPIO 20 |
| SEL | GND | — |

### Amplifier — MAX98357A I2S

| Amp Pin | Pi Pin | GPIO |
|---------|--------|------|
| VIN | 5 V (Pin 2) | — |
| GND | GND (Pin 9) | — |
| BCLK | Pin 12 | GPIO 18 |
| LRC | Pin 35 | GPIO 19 |
| DIN | Pin 40 | GPIO 21 |

### Buttons (active LOW — wire between GPIO pin and GND)

| Button | GPIO | Pi Pin | Other Pin |
|--------|------|--------|-----------|
| BTN_SPEAK_MANAGER | 17 | Pin 11 | GND (Pin 14) |
| BTN_SPEAK_WORKER | 24 | Pin 18 | GND (Pin 20) |
| BTN_PLAY_MSG | 25 | Pin 22 | GND (Pin 25) |
| BTN_CALL_MANAGER | 23 | Pin 16 | GND (Pin 14) | ← press button (NOT toggle switch)
| BTN_REMINDER | 27 | Pin 13 | GND (Pin 14) |
| BTN_HANDOVER | 22 | Pin 15 | GND (Pin 14) |

### Message Indicator LED

```
GPIO 5 (Pin 29)  →  220 Ω resistor  →  LED (+)  →  LED (−)  →  GND (Pin 30)
```

---

## 4. Software Setup

### 4.1 All Devices — Python & Dependencies

```bash
# Python 3.9+ required
pip install faster-whisper ctranslate2 sentencepiece huggingface-hub
pip install pyttsx3 pyaudio keyboard RPi.GPIO   # RPi.GPIO on Pi only
```

### 4.2 Pi — Enable I2S Audio

Add these lines to `/boot/config.txt`, then reboot:

```
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard
```

Verify:
```bash
arecord -l    # should list your I2S mic
aplay -l      # should list your I2S amp
```

### 4.3 First Run — Download Models (internet required once)

```bash
python main.py   # downloads ~300 MB of models on first run
```

After the first run, the system works fully offline.

---

## 5. Configuration

Edit `config.py` on each device before first run:

```python
# Who is this device?
HELMET_ROLE = "worker"        # "manager" or "worker"
HELMET_ID   = "w-01"         # unique per device ("manager", "w-01", "w-02")

# What language does this person speak?
HELMET_LANGUAGE = "en"        # "en" = English,  "de" = German

# Network
MANAGER_IP     = "192.168.1.100"   # ← Laptop 1 (manager) LAN IP
PEER_WORKER_IP = "192.168.1.101"   # ← IP of the OTHER worker (Pi workers only)
```

> **Tip:** You can also set the language by voice after startup — no need to edit the file.
> Hold the PLAY button for 3 seconds and say "English" or "German".

### Find your IP address

```bash
# Windows
ipconfig

# Linux / Pi
hostname -I
```

---

## 6. Controls Reference

### Manager (Laptop 1 — keyboard)

| Key | Action |
|-----|--------|
| **1 / 2 / 3** | Select target worker for PTT message |
| Hold **SPACE** | Record & send PTT message to selected worker |
| **F1** | Call Worker A (Pi / w-01) — press to call, answer, or hang up |
| **F2** | Call Worker B (Laptop 2 / w-02) — press to call, answer, or hang up |
| **P** (short press) | Play next received message |
| Hold **P** (3 sec) | Voice language configuration |
| Hold **R** | Record a timed reminder |
| **H** | Play last handover / record new handover |
| Hold **SPACE** during call | Mute yourself (release = unmute) |

### Worker Pi (physical buttons)

| Button | Action |
|--------|--------|
| Hold **BTN_SPEAK_MANAGER** (GPIO 17) | Record & send PTT to manager |
| Hold **BTN_SPEAK_WORKER** (GPIO 24) | Record & send PTT to peer worker |
| **BTN_CALL_MANAGER** (GPIO 23) | Call manager — press to call, answer, or hang up |
| Short press **BTN_PLAY_MSG** (GPIO 25) | Play next received message |
| Hold **BTN_PLAY_MSG** (3 sec) | Voice language configuration |
| Hold **BTN_REMINDER** (GPIO 27) | Record timed reminder |
| **BTN_HANDOVER** (GPIO 22) | Play handover / record new handover |

### Worker Laptop 2 (keyboard)

| Key | Action |
|-----|--------|
| Hold **SPACE** | Record & send PTT to manager |
| Hold **W** | Record & send PTT to peer worker |
| **C** | Call manager — press to call, answer, or hang up |
| **P** (short press) | Play next received message |
| Hold **P** (3 sec) | Voice language configuration |
| Hold **R** | Record reminder |
| **H** | Handover |

> **Workers cannot call each other** — only manager ↔ worker calls.

---

## 7. How to Use — Step by Step

### 7.1 Starting the System

**Start in this order:**

1. **Manager first:**
   ```bash
   python main.py manager
   ```
   You hear: *"Smart helmet ready."*

2. **Worker Pi:**
   ```bash
   python main.py worker
   ```
   You hear: *"Smart helmet ready."*
   Manager hears: *"Worker 1 connected. Press 1 to talk to this worker."*

3. **Worker Laptop 2** (same as Pi):
   ```bash
   python main.py worker
   ```
   Manager hears: *"Worker 2 connected. Press 2 to talk to this worker."*

---

### 7.2 Sending a Message

**Manager → Worker:**
1. Press **1** to select Worker 1. TTS: *"Talking to worker 1."*
2. Hold **SPACE** and speak. TTS: *"Recording."*
3. Release **SPACE**. TTS: *"Processing."* then *"Message sent."*
4. Worker's LED blinks fast + 3 beeps + TTS: *"1 message received. Press P to play."*

**Worker → Manager:**
1. Hold **SPACE** (or BTN_SPEAK_MANAGER on Pi) and speak.
2. Release. TTS: *"Message sent."*
3. Manager's TTS: *"1 message received. Press P to play."*

**Worker → Peer Worker (private channel, manager does not hear):**
1. Hold **W** (or BTN_SPEAK_WORKER on Pi) and speak.
2. Release. TTS: *"Message sent."*
3. Peer worker's LED blinks + 3 beeps + *"1 message received. Press P to play."*

---

### 7.3 Playing a Received Message

When the LED blinks and you hear the beep:

1. Press **P** (or BTN_PLAY_MSG on Pi) briefly.
2. TTS reads a preview: *"From worker: Check the crane at..."*
3. Full message plays automatically in your configured language.
   - If sender spoke your language → plays directly.
   - If sender spoke the other language → translated automatically before playing.
4. If more messages remain: *"2 messages remaining."*
5. When all played: *"No more messages."* LED turns off.

---

### 7.4 Making and Receiving a Live Call

**Call States** — the same button does different things depending on state:

| Your state when pressing | What happens |
|--------------------------|-------------|
| Idle | Initiates a call to that partner |
| Calling (you called, waiting) | Cancels your outgoing call |
| Incoming (they called you) | Answers the call |
| In call | Hangs up |

**Manager calls Worker A:**
1. Press **F1**. TTS: *"Calling worker 1."*
2. Worker A's helmet: 3 beeps + LED blinks + TTS: *"Incoming call from manager. Press call button to answer."*
3. Worker A presses **BTN_CALL_MANAGER** / **C** key. TTS both sides: *"Call connected."*
4. Real-time audio flows — both sides hear each other instantly.
5. Either party presses their call button → TTS: *"Call ended."*

**Worker calls Manager:**
1. Worker presses **BTN_CALL_MANAGER** / **C** key. TTS: *"Calling manager."*
2. Manager's laptop: 3 beeps + TTS: *"Incoming call from worker 1. Press F1 to answer."*
3. Manager presses **F1**. TTS both sides: *"Call connected."*
4. Either party presses their call button → *"Call ended."*

**Muting during a call:** Hold **SPACE** (or BTN_SPEAK_MANAGER on Pi). Release to unmute.

**If manager is already in a call** and another worker calls → TTS: *"Already in a call."*

> Workers cannot call each other. Only manager ↔ worker calls.

---

### 7.5 Setting Your Language

You can configure your helmet language at any time without editing any file:

1. **Hold the PLAY button (P) for 3 full seconds.**
2. TTS: *"Language setup. Say English or German."*
3. Continue holding and say **"English"** or **"German"** (or "Englisch" / "Deutsch").
4. Release button.
5. TTS confirms: *"Configured for English."* / *"Konfiguriert für Deutsch."*

The setting is saved to `data/settings.json` and loaded automatically on next boot.

---

### 7.6 Reminders

1. Hold **R** and speak your reminder (e.g., *"Check valve in 30 minutes"*).
2. Release. TTS: *"Reminder saved."*
3. The reminder plays automatically at the scheduled time.

---

### 7.7 Shift Handover

**Record handover at end of shift:**
1. Press **H** and speak handover notes.
2. Release. TTS: *"Handover saved."*

**Play handover at start of shift:**
1. Press **H** briefly (without speaking).
2. TTS reads back the last recorded handover.

---

## 8. Testing Before First Use

Run each test in order:

```bash
python test_suite.py 1   # module imports — all must PASS
python test_suite.py 2   # database
python test_suite.py 3   # audio recording (speak into mic when prompted)
python test_suite.py 4   # text-to-speech playback
python test_suite.py 5   # offline STT — Whisper
python test_suite.py 6   # offline translation EN↔DE
python test_suite.py 7   # network (run on both manager and worker)
python test_suite.py 8   # full message flow simulation
```

> Run all at once: `python test_suite.py`

### Quick sanity checks

```bash
# On Pi — check mic
arecord -d 3 -r 16000 -f S16_LE test.wav && aplay test.wav

# On Pi — check speaker
speaker-test -t wav -c 1

# Both devices — check network
ping <manager_ip>
```

---

*Smart Helmet Communication System — Semester Project*