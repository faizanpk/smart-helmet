# Smart Helmet Communication System
## Complete User Guide

---

## What This System Does

| Feature | How to trigger |
|---------|---------------|
| **Push-to-Talk Translation** | Hold button, speak EN, release → partner hears DE (and vice versa) |
| **Task Reminders** | Hold button, say "Inspect crane at 14:30" → plays audio at that time |
| **Shift Handover** | Press button → records/plays handover message for next shift |
| **Live Call** | Flip toggle switch ON → full two-way intercom in real time |
| **Speaker Mode** | Flip toggle switch ON → volume jumps when helmet is removed |

---

## Part 1 — Hardware Wiring

### Components

| Component | Role |
|-----------|------|
| Raspberry Pi 4 (2 GB) | Worker helmet brain |
| SPH0645LM4H (I2S Mic) | Captures voice |
| MAX98357A (I2S Amp) | Drives speaker |
| Mini oval speaker (8 Ohm 1W) | Plays audio output |
| 3× Push buttons | Speak / Reminder / Handover |
| Toggle switch (3-pin ON-OFF-ON) | Speaker mode + Live Call |
| Li-Ion 3.7V 5000 mAh + TP4056 | Power supply |

---

### 1.1 — I2S Microphone (SPH0645LM4H)

```
SPH0645LM4H Pin     Raspberry Pi Pin
----------------------------------------------
3V              --> Pin 1  (3.3V)
GND             --> Pin 6  (GND)
BCLK            --> Pin 12 (GPIO 18 – I2S CLK)
DOUT            --> Pin 38 (GPIO 20 – I2S DIN)
LRCL            --> Pin 35 (GPIO 19 – I2S FS)
SEL             --> GND    (selects left channel)
```

> DOUT is "data out" from the mic — it goes INTO the Pi (GPIO 20).

---

### 1.2 — I2S Amplifier + Speaker (MAX98357A)

```
MAX98357A Pin       Raspberry Pi Pin
----------------------------------------------
VIN             --> Pin 2  (5V)
GND             --> Pin 9  (GND)
BCLK            --> Pin 12 (GPIO 18 – shared with mic)
LRC             --> Pin 35 (GPIO 19 – shared with mic)
DIN             --> Pin 40 (GPIO 21 – I2S DOUT)
GAIN            --> leave unconnected (9 dB default)
SD              --> leave unconnected (always on)
```

Connect your 8 Ohm speaker to the **+** and **−** screw terminals on the MAX98357A board.

---

### 1.3 — Push Buttons

Connect each button between its GPIO pin and any GND pin.
No external resistors needed — internal pull-ups are enabled in software.

```
Button           GPIO (BCM)   Pi Header Pin   GND Pin
-------------------------------------------------------
BTN_SPEAK        GPIO 17      Pin 11          Pin 14
BTN_REMINDER     GPIO 27      Pin 13          Pin 20
BTN_HANDOVER     GPIO 22      Pin 15          Pin 25
```

---

### 1.4 — Toggle Switch (3-pin ON-OFF-ON)

Use only the **centre pin** and **one outer pin**. Ignore the third pin.

```
Toggle pin       Raspberry Pi Pin
----------------------------------------------
Centre pin   --> Pin 16 (GPIO 23)
Outer pin    --> Pin 17 (3.3V)
```

When flipped ON: GPIO 23 reads HIGH → speaker mode + live call activates.

---

### 1.5 — Power (Battery + TP4056 + Boost Converter)

```
Battery B+  --> TP4056 B+
Battery B−  --> TP4056 B−
TP4056 OUT+ --> Boost converter IN+   (e.g. MT3608, set to 5V output)
TP4056 OUT− --> Boost converter IN−
Boost OUT+  --> Pi Pin 2 (5V)
Boost OUT−  --> Pi Pin 6 (GND)
```

> **Important:** The Pi needs exactly 5V. Your Li-Ion battery outputs 3.7V.
> A boost converter between the TP4056 and the Pi is **mandatory**.

---

## Part 2 — Manager Laptop Setup (Windows)

### Step 1 — Project location

The project is already at: `C:\Users\dell\smart-helmet\`

### Step 2 — Install Python dependencies

Open **PowerShell** and run:

```powershell
cd C:\Users\dell\smart-helmet
python -m pip install -r requirements.txt
```

### Step 3 — Download offline AI models (internet, one time only, ~300 MB)

```powershell
# Download OPUS-MT translation models (EN<->DE)
python main.py --setup-models

# OR run directly:
python -c "from translation import setup_offline_models; setup_offline_models()"
```

Then pre-cache Whisper:

```powershell
python -c "from faster_whisper import WhisperModel; WhisperModel('tiny', device='cpu', compute_type='int8')"
```

After this the laptop runs **fully offline** — no internet needed to use it.

### Step 4 — Find your laptop IP address

```powershell
ipconfig
```

Look for **IPv4 Address** under your Wi-Fi adapter. Example: `192.168.1.100`
Write it down — you will enter it in the Pi config.

### Step 5 — Set the laptop role in config.py

Open `C:\Users\dell\smart-helmet\config.py` and change:

```python
HELMET_ROLE           = "manager"
HELMET_LANGUAGE_CODE  = "en-US"    # manager speaks English
TARGET_LANGUAGE_CODE  = "de-DE"    # worker hears German
HELMET_LANGUAGE_SHORT = "en"
TARGET_LANGUAGE_SHORT = "de"
WHISPER_MODEL_SIZE    = "base"     # more accurate on laptop
```

Leave `PARTNER_IP` unchanged — the manager is the server.

---

## Part 3 — Raspberry Pi Setup

### Step 1 — Flash the OS

1. Download **Raspberry Pi Imager**: https://www.raspberrypi.com/software/
2. Select: **Raspberry Pi OS Lite (64-bit)** — no desktop required
3. Click the **gear icon** before writing and configure:
   - Hostname: `smart-helmet`
   - Enable SSH: yes
   - Username: `pi` with a password of your choice
   - Wi-Fi: your network name and password
4. Write to SD card, insert into Pi, and power on

### Step 2 — Connect via SSH from your laptop

```powershell
ssh pi@smart-helmet.local
```

If that does not work, find the Pi IP from your router admin page and use:
```powershell
ssh pi@192.168.1.xxx
```

### Step 3 — Copy the project to the Pi

On your **laptop** in PowerShell:

```powershell
scp -r C:\Users\dell\smart-helmet pi@smart-helmet.local:/home/pi/smart-helmet
```

### Step 4 — Run the setup script

On the **Pi** (via SSH):

```bash
cd /home/pi/smart-helmet
chmod +x setup_pi.sh
sudo ./setup_pi.sh
```

The script runs 7 steps automatically:
1. Installs system packages (portaudio, espeak-ng, alsa-utils)
2. Enables I2S mic overlay (SPH0645LM4H)
3. Enables I2S amp overlay (MAX98357A)
4. Disables onboard audio (I2S devices become card 0)
5. Installs Python packages from requirements.txt
6. Downloads offline AI models (Whisper + OPUS-MT, ~300 MB)
7. Installs systemd service (auto-starts on boot)

> Takes 5–10 minutes. Keep the Pi connected to the internet.

### Step 5 — Configure the Pi role

```bash
nano /home/pi/smart-helmet/config.py
```

Change these lines:

```python
HELMET_ROLE           = "worker"
HELMET_LANGUAGE_CODE  = "de-DE"          # worker speaks German
TARGET_LANGUAGE_CODE  = "en-US"          # manager hears English
HELMET_LANGUAGE_SHORT = "de"
TARGET_LANGUAGE_SHORT = "en"
PARTNER_IP            = "192.168.1.100"  # <- YOUR LAPTOP IP HERE
WHISPER_MODEL_SIZE    = "tiny"           # keep tiny for Pi
```

Save: **Ctrl+O** then **Ctrl+X**.

### Step 6 — Reboot and verify audio

```bash
sudo reboot
```

Wait 30 seconds, then SSH back in:

```bash
ssh pi@smart-helmet.local

arecord -l    # should show I2S mic
aplay -l      # should show I2S amp
```

Expected output:
```
**** List of CAPTURE Hardware Devices ****
card 0: sndrpisimplecar [snd_rpi_simple_card], device 0: simple-card_codec_link snd-soc-dummy-dai-0 []
```

If the card number shown is **not 0**, update config.py:
```python
ALSA_MIC_DEVICE = "plughw:1,0"   # replace 1 with your card number
ALSA_SPK_DEVICE = "plughw:1,0"
```

### Step 7 — Quick audio test

```bash
# Record 3 seconds from mic, then play back through speaker
arecord -D plughw:0,0 -f S16_LE -r 16000 -d 3 /tmp/test.wav
aplay  -D plughw:0,0 /tmp/test.wav

# Play a 440 Hz test tone through the speaker
speaker-test -D plughw:0,0 -t sine -f 440 -l 1
```

---

## Part 4 — Running the System

Both devices **must be on the same Wi-Fi network or hotspot**.

### Start the Manager Laptop

Open **PowerShell as Administrator** (right-click → Run as administrator):

```powershell
cd C:\Users\dell\smart-helmet
python main.py manager
```

Wait until you see and hear:
```
[INFO]  Role     : manager
[INFO]  [NET] Manager: TCP server started on port 5005
[INFO]  Keyboard controls: SPACE=speak/mute  r=reminder  h=handover  L=live-call
        "Smart helmet ready."   <- spoken aloud
```

### Start the Worker Pi

```bash
# Option A: systemd service (auto-starts on boot — recommended)
sudo systemctl start smart-helmet
sudo journalctl -fu smart-helmet      # watch the live log

# Option B: manual
cd /home/pi/smart-helmet
source .venv/bin/activate
python main.py worker
```

Wait until you hear on the Pi speaker:
```
"Smart helmet ready."
```

And on the Pi log:
```
[INFO]  [NET] Connected to manager at 192.168.1.100:5005
```

> When **both** sides say "Smart helmet ready." — the system is fully connected and ready to use.

---

## Part 5 — Using Each Feature

### Feature 1 — Push-to-Talk Translation

**Manager (laptop) → Worker (Pi):**

| Step | What you do | What you hear |
|------|------------|--------------|
| 1 | Hold **SPACE** | "Recording." |
| 2 | Speak English | (mic is active while key held) |
| 3 | Release **SPACE** | "Sending." → "Message sent." |
| 4 | Worker Pi speaker plays | German translation |

**Worker (Pi) → Manager (laptop):**

| Step | What you do | What you hear |
|------|------------|--------------|
| 1 | Hold **BTN_SPEAK** (GPIO 17, Pin 11) | "Aufnahme." |
| 2 | Speak German | (mic is active while button held) |
| 3 | Release button | "Senden." → "Nachricht gesendet." |
| 4 | Manager laptop speaker plays | English translation |

**Tips for best recognition:**
- Speak at normal pace, clearly
- Wait a moment after pressing before speaking
- Stay 20–30 cm from the mic
- Minimise background noise

---

### Feature 2 — Task Reminders

**Set a reminder:**

| Step | What you do | What you hear |
|------|------------|--------------|
| 1 | Hold **R** key (or BTN_REMINDER, GPIO 27) | "Hold the button and record your reminder." |
| 2 | Speak reminder + time | (recording) |
| 3 | Release | "Reminder saved for 14:30." |

**Supported time phrases:**

| What you say | Parsed as |
|-------------|-----------|
| "at 14:30" | 14:30 |
| "at 2 pm" | 14:00 |
| "at noon" | 12:00 |
| "morning inspection" | 09:00 |
| "um 14 Uhr 30" | 14:30 |
| "um 9 Uhr" | 09:00 |

**At the set time, system plays automatically:**
```
"Reminder:"
[your recorded message spoken aloud]
```

If no time is detected: "No time found. Please include a time, for example: at 14 30."

---

### Feature 3 — Shift Handover

**Record a handover (outgoing shift):**

| Step | What you do | What you hear |
|------|------------|--------------|
| 1 | Press **H** key (or BTN_HANDOVER, GPIO 22) | "Hold the button and record your handover. Say your name, zone, and message." |
| 2 | Hold and speak | (recording) |
| 3 | Release | "Handover message saved." |

Example message:
> "This is Ahmed, Zone B. Scaffolding on level 3 needs safety inspection before the morning shift."

**Play a handover (incoming shift):**

| Step | What you do | What you hear |
|------|------------|--------------|
| 1 | Press **H** (unread message exists) | Full message plays |
| 2 | Press **H** again | In record mode for next handover |

The system always plays unread messages first.

---

### Feature 4 — Live Call (Full Duplex Intercom)

**Start a call:**

| Device | Action |
|--------|--------|
| Manager laptop | Press **L** key |
| Worker Pi | Flip toggle switch **ON** |

**During the call:**
- Both sides can speak and hear simultaneously
- No translation — raw audio only
- Hold **SPACE** (laptop) or **BTN_SPEAK** (Pi) to mute yourself
- Release to unmute

**End the call:**
- Manager: press **L** again
- Worker: flip toggle switch **OFF**

Expected latency: 100–300 ms (network dependent, normal for conversation).

---

### Feature 5 — Speaker Mode (Volume)

| Toggle | What happens |
|--------|-------------|
| Flip **ON** (helmet removed) | Volume → 100%, live call starts |
| Flip **OFF** (helmet on) | Volume → 80%, live call stops |

---

## Part 6 — Troubleshooting

| Problem | Likely cause | Fix |
|---------|-------------|-----|
| No sound from speaker | Wiring or amp issue | Check MAX98357A connections; run speaker-test |
| Mic not detected | I2S overlay inactive | Run `arecord -l`; check card number in config.py |
| Worker cannot connect | Wrong PARTNER_IP or firewall | Verify with `ipconfig`; allow TCP port 5005 in Windows Firewall |
| "Could not understand" | Audio too quiet or wrong language | Speak louder; check HELMET_LANGUAGE_CODE in config.py |
| Reminder does not play | Wrong system clock | Run `date` on Pi; check DB with test_suite |
| Echo during live call | Mic picks up speaker | Lower volume or add physical distance between mic and speaker |
| Pi not on Wi-Fi | Wrong credentials | Re-flash SD card with correct Wi-Fi settings |
| Keys not detected on laptop | Not running as Administrator | Right-click PowerShell → Run as administrator |
| ALSA error: no such device | Card number mismatch | Run `arecord -l`; update ALSA_MIC_DEVICE in config.py |
| ModuleNotFoundError | Package not installed | `python -m pip install -r requirements.txt` |
| "Smart helmet ready" not heard | TTS or speaker problem | Run `python test_suite.py 3` to test TTS in isolation |

---

## Part 7 — Quick Reference

### Manager Laptop Keys (must run as Administrator)

| Key | Normal mode | During live call |
|-----|-------------|-----------------|
| Hold **SPACE** | Push-to-talk + translate | Mute yourself |
| Hold **R** | Record reminder | — |
| Press **H** | Play or record handover | — |
| Press **L** | Start live call | Stop live call |

### Worker Pi Physical Interface

| Button/Switch | GPIO (BCM) | Pi Header Pin | Action |
|--------------|-----------|--------------|--------|
| BTN_SPEAK | GPIO 17 | Pin 11 | PTT translate / Mute during call |
| BTN_REMINDER | GPIO 27 | Pin 13 | Record reminder |
| BTN_HANDOVER | GPIO 22 | Pin 15 | Play or record handover |
| Toggle ON | GPIO 23 | Pin 16 | Live call + loud volume |
| Toggle OFF | GPIO 23 | Pin 16 | PTT mode + normal volume |

### Key Files

| File | Purpose |
|------|---------|
| `config.py` | All settings — edit HELMET_ROLE, PARTNER_IP, language codes |
| `main.py` | Entry point — `python main.py manager` or `python main.py worker` |
| `setup_pi.sh` | One-time Pi setup — run once with `sudo ./setup_pi.sh` |
| `test_suite.py` | Test each feature — `python test_suite.py 3` |
| `data/helmet.db` | SQLite database (reminders + handovers) |
| `data/translation_models/` | Cached OPUS-MT translation models |

### Useful Pi Commands

```bash
# Check service status
sudo systemctl status smart-helmet

# Watch live log
sudo journalctl -fu smart-helmet

# Restart the service
sudo systemctl restart smart-helmet

# List audio input devices (microphones)
arecord -l

# List audio output devices (speakers)
aplay -l

# Record 3 s from mic and play back (audio hardware test)
arecord -D plughw:0,0 -f S16_LE -r 16000 -d 3 /tmp/t.wav && aplay -D plughw:0,0 /tmp/t.wav

# View saved reminders
cd /home/pi/smart-helmet && source .venv/bin/activate
python -c "import db; c=db.get_conn(); [print(dict(r)) for r in c.execute('SELECT * FROM reminders').fetchall()]"

# View saved handover messages
python -c "import db; c=db.get_conn(); [print(dict(r)) for r in c.execute('SELECT * FROM handover').fetchall()]"
```