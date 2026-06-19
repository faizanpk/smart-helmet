# Smart Helmet — User Guide

---

## What is this system?

Three helmets on one Wi-Fi network. Each person can send voice messages, make live calls, set reminders, and record shift handovers — all offline, no internet needed.

| Device | Who uses it |
|--------|------------|
| **Laptop 1** | Manager |
| **Raspberry Pi** | Worker A |
| **Laptop 2** | Worker B |

---

## Before First Use

### 1. Edit `config.py` on each device

Open `config.py` and change these three lines:

```python
HELMET_ROLE = "worker"          # "manager" on Laptop 1, "worker" on Pi and Laptop 2
HELMET_ID   = "w-01"           # "manager" / "w-01" / "w-02"
MANAGER_IP  = "192.168.1.100"  # IP address of Laptop 1 (the manager)
```

Also set `PEER_WORKER_IP` on each worker to the IP of the *other* worker.

> **Find your IP address:**
> - Windows: open Command Prompt → type `ipconfig`
> - Pi / Linux: open Terminal → type `hostname -I`

### 2. Install Python packages (run once)

```bash
pip install faster-whisper ctranslate2 sentencepiece huggingface-hub pyttsx3 pyaudio keyboard
```

On the Pi only, also run:
```bash
pip install RPi.GPIO
```

### 3. First run (downloads AI models — internet needed once)

```bash
python main.py
```

The system downloads ~300 MB of speech and translation models. After that, everything works fully offline.

### 4. Pi only — enable microphone and speaker

Add these two lines to `/boot/config.txt`, then reboot:

```
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard
```

---

## Wiring (Pi only)

### Buttons — wire each between the GPIO pin and any GND pin

| Button | GPIO | Physical Pin |
|--------|------|-------------|
| Send to manager (hold) | 17 | Pin 11 |
| Send to worker (hold) | 24 | Pin 18 |
| Call manager | 23 | Pin 16 |
| Play message | 25 | Pin 22 |
| Reminder (hold) | 27 | Pin 13 |
| Handover | 22 | Pin 15 |

### LED indicator

```
GPIO 5 (Pin 29)  →  220Ω resistor  →  LED (+)  →  LED (−)  →  GND (Pin 30)
```

---

## Starting the system

Always start the **manager first**, then the workers.

**Manager (Laptop 1):**
```bash
python main.py manager
```

**Worker Pi:**
```bash
python main.py worker
```

**Worker Laptop 2:**
```bash
python main.py worker
```

You will hear *"Smart helmet ready."* on each device. When a worker connects, the manager hears *"Worker 1 connected."*

---

## Controls

### Manager — keyboard

| Key | What it does |
|-----|-------------|
| **1** or **2** | Select which worker to send a message to |
| Hold **SPACE** | Record and send a voice message to the selected worker |
| **F1** | Call / answer / hang up — Worker A (Pi) |
| **F2** | Call / answer / hang up — Worker B (Laptop 2) |
| **P** | Play the next received message |
| Hold **P** for 3 sec | Set your language (say "English" or "German") |
| Hold **R** | Record a reminder |
| **H** | Record or play back a shift handover |
| Hold **SPACE** during a call | Mute yourself (release to unmute) |

### Worker Pi — buttons

| Button | What it does |
|--------|-------------|
| Hold **Send-to-manager** (GPIO 17) | Record and send a message to manager |
| Hold **Send-to-worker** (GPIO 24) | Record and send a message to the other worker |
| **Call** (GPIO 23) | Call / answer / hang up — manager |
| **Play** (GPIO 25, short press) | Play the next received message |
| Hold **Play** (GPIO 25, 3 sec) | Set your language |
| Hold **Reminder** (GPIO 27) | Record a reminder |
| **Handover** (GPIO 22) | Record or play back a shift handover |
| Hold **Send-to-manager** during a call | Mute yourself |

### Worker Laptop 2 — keyboard

| Key | What it does |
|-----|-------------|
| Hold **SPACE** | Record and send a message to manager |
| Hold **W** | Record and send a message to the other worker |
| **C** | Call / answer / hang up — manager |
| **P** | Play the next received message |
| Hold **P** for 3 sec | Set your language |
| Hold **R** | Record a reminder |
| **H** | Record or play back a shift handover |
| Hold **SPACE** during a call | Mute yourself |

---

## How to use

### Sending a voice message

1. Manager: press **1** or **2** to choose the worker. You hear *"Talking to worker 1."*
2. Hold the send button (SPACE or GPIO 17/24) and speak.
3. Release. You hear *"Message sent."*
4. The receiver's LED blinks and they hear 3 beeps + *"1 message received. Press P to play."*

### Playing a received message

1. Press **P** (or GPIO 25 on Pi).
2. You hear a short preview: *"From worker: Check the crane at..."*
3. The full message plays in your configured language. If the sender spoke a different language, it is automatically translated.
4. When all messages are played, the LED turns off.

### Making a call

| Step | Manager | Worker |
|------|---------|--------|
| Initiate | Press **F1** (Worker A) or **F2** (Worker B) | Press **C** (or GPIO 23) |
| You hear | *"Calling worker 1."* | *"Calling manager."* |
| Other side hears | 3 beeps + *"Incoming call. Press F1 to answer."* | 3 beeps + *"Incoming call. Press call button to answer."* |
| Answer | Press the same key (**F1** or **F2**) | Press **C** (or GPIO 23) |
| Both hear | *"Call connected."* — real-time audio starts | same |
| End call | Press the same key again | Press same button again |

> Workers cannot call each other — only manager ↔ worker calls.

### Setting your language

1. Hold **P** (or GPIO 25) for **3 full seconds**.
2. You hear *"Language setup. Say English or German."*
3. Keep holding and say **"English"** or **"German"**.
4. Release. You hear *"Configured for English."* or *"Konfiguriert für Deutsch."*

The setting is saved automatically and loaded on every boot.

### Reminders

1. Hold **R** and say your reminder (e.g. *"Check valve pressure in 20 minutes"*).
2. Release. The reminder plays automatically at the right time.

### Shift handover

**At end of shift:** Press **H** and speak your handover notes. Release to save.

**At start of shift:** Press **H** briefly (without speaking) to hear the previous handover.

---

## Testing before first use

```bash
python test_suite.py 1    # check all modules load correctly
python test_suite.py 3    # test microphone
python test_suite.py 4    # test speaker / text-to-speech
python test_suite.py 5    # test speech recognition
python test_suite.py 6    # test translation (EN ↔ DE)
python test_suite.py 7    # test network connection (run on both devices)
```

Run all tests at once: `python test_suite.py`
