# Smart Helmet — Complete User Guide

---

## 1. System Overview

Three helmets communicate over a local Wi-Fi network. No internet is needed after initial setup (translation requires Wi-Fi but not the internet if you use an offline model).

| Device | Role | ID |
|--------|------|----|
| Laptop 1 | **Manager** | `manager` |
| Raspberry Pi 4 | **Worker A** | `w-01` |
| Laptop 2 | **Worker B** | `w-02` |

**What the system can do:**
- Send voice messages between any two helmets (manager ↔ worker, worker ↔ worker)
- Make live two-way calls (manager ↔ worker only)
- Automatically translate messages between English and German
- Set voice reminders that play at the right time
- Record and replay shift handover notes

---

## 2. Hardware List — Raspberry Pi 4

| Item | Model / Spec | Purpose |
|------|-------------|---------|
| Raspberry Pi 4 | Any RAM variant | Main computer |
| I2S Microphone | **Adafruit SPH0645LM4H** | Voice input |
| I2S Amplifier + Speaker | **Adafruit MAX98357A** + 4Ω/8Ω speaker | Voice output |
| Push buttons (momentary) | Tactile 12×12mm, normally open | Controls (×6) |
| LED | Any 3mm or 5mm | Message alert |
| 220Ω resistor | 1/4W | LED current limiter |
| Jumper wires | Male-to-female | Connecting breakouts to Pi |
| Breadboard | Half or full size | Mounting buttons and LED |
| USB power bank | 5V, 2A or more | Portable power for helmet |

> **Do not use a USB microphone with this setup.** The setup script configures I2S audio, which disables the onboard 3.5mm jack. The SPH0645LM4H mic and MAX98357A amp both use the I2S bus (GPIO pins), keeping the helmet compact and fully wirable inside a hard hat.

---

## 3. Raspberry Pi 4 GPIO Pin Map

```
Raspberry Pi 4 — 40-Pin GPIO Header
(Pins numbered left-to-right, top-to-bottom when USB ports face down)

       3.3V  [ 1] [ 2]  5V
      GPIO2  [ 3] [ 4]  5V
      GPIO3  [ 5] [ 6]  GND
      GPIO4  [ 7] [ 8]  GPIO14
        GND  [ 9] [10]  GPIO15
 ►  GPIO17  [11] [12]  GPIO18  ◄ I2S CLK (MAX98357A / SPH0645)
 ►  GPIO27  [13] [14]  GND
 ►  GPIO22  [15] [16]  GPIO23  ◄
      3.3V  [17] [18]  GPIO24  ◄
     GPIO10  [19] [20]  GND
      GPIO9  [21] [22]  GPIO25  ◄
     GPIO11  [23] [24]  GPIO8
        GND  [25] [26]  GPIO7
      GPIO0  [27] [28]  GPIO1
 ►   GPIO5  [29] [30]  GND
      GPIO6  [31] [32]  GPIO12
     GPIO13  [33] [34]  GND
     GPIO19  [35] [36]  GPIO16
     GPIO26  [37] [38]  GPIO20  ◄ I2S DATA IN (SPH0645 DOUT)
        GND  [39] [40]  GPIO21  ◄ I2S DATA OUT (MAX98357A DIN)

► = used by this project
```

---

## 4. Complete Wiring — All Components

---

### 4.1 I2S Microphone — Adafruit SPH0645LM4H

The SPH0645LM4H is a digital MEMS microphone. It communicates over the I2S bus — **no analog wiring needed**. It connects to 6 pins.

```
                 ┌─────────────────────────────────┐
                 │   Adafruit SPH0645LM4H Breakout  │
                 │                                  │
  Pi Pin 1  ─── │ 3V (VDD)                         │
  Pi Pin 6  ─── │ GND                               │
  Pi Pin 12 ─── │ BCLK   (Bit Clock)                │  ← GPIO18
  Pi Pin 35 ─── │ LRCL   (Left/Right Clock)         │  ← GPIO19
  Pi Pin 38 ─── │ DOUT   (Data Out → Pi receives)   │  ← GPIO20
  Pi Pin 17 ─── │ SEL    (Left/Right select)        │  ← 3.3V = Left channel
                 └─────────────────────────────────┘
```

| SPH0645 Pin | Pi Physical Pin | Pi GPIO | Notes |
|-------------|----------------|---------|-------|
| **3V** (VDD) | Pin 1 | 3.3V | Power |
| **GND** | Pin 6 | GND | Ground |
| **BCLK** | Pin 12 | GPIO 18 | I2S Bit Clock |
| **LRCL** | Pin 35 | GPIO 19 | I2S Word Select |
| **DOUT** | Pin 38 | GPIO 20 | Mic audio → Pi |
| **SEL** | Pin 17 | 3.3V | Tie to 3.3V for Left channel |

> **SEL pin:** Tie it to 3.3V for Left channel. This is important — if left floating the mic produces no output.

---

### 4.2 I2S Amplifier — Adafruit MAX98357A

The MAX98357A is a Class D amplifier. It also uses the I2S bus and can drive a small 4Ω or 8Ω speaker directly (up to 3W).

```
                 ┌──────────────────────────────────┐
                 │   Adafruit MAX98357A Breakout      │
                 │                                   │
  Pi Pin 2  ─── │ Vin (5V)                          │
  Pi Pin 9  ─── │ GND                               │
  Pi Pin 12 ─── │ BCLK   (Bit Clock — shared w/mic) │  ← GPIO18
  Pi Pin 35 ─── │ LRC    (Left/Right Clock)          │  ← GPIO19
  Pi Pin 40 ─── │ DIN    (Data In ← Pi sends)        │  ← GPIO21
  [not connected]│ SD     (Shutdown, active LOW)      │  leave floating = always ON
  [not connected]│ GAIN   (Gain select)               │  leave floating = 9dB gain
                 └──────────────────────────────────┘

  MAX98357A  +   ─────────────── Speaker + (red wire)
  MAX98357A  -   ─────────────── Speaker - (black wire)
```

| MAX98357A Pin | Pi Physical Pin | Pi GPIO | Notes |
|---------------|----------------|---------|-------|
| **Vin** | Pin 2 | 5V | Power |
| **GND** | Pin 9 | GND | Ground |
| **BCLK** | Pin 12 | GPIO 18 | Shared with SPH0645 |
| **LRC** | Pin 35 | GPIO 19 | Shared with SPH0645 |
| **DIN** | Pin 40 | GPIO 21 | Pi audio → Amp |
| **SD** | — | — | Leave unconnected (amp stays ON) |
| **GAIN** | — | — | Leave unconnected (9dB = good default) |
| **+** (Speaker Out) | — | — | To speaker positive terminal |
| **-** (Speaker Out) | — | — | To speaker negative terminal |

> BCLK and LRC are **shared** between the mic and amp — one wire from Pi Pin 12 goes to both breakouts, and one wire from Pi Pin 35 goes to both breakouts.

---

### 4.3 Push Buttons — All 6 Controls

Each button is **normally open, momentary**. One leg connects to the GPIO pin; the other to GND. The software enables internal pull-up resistors, so the GPIO reads HIGH normally and LOW when pressed.

```
GPIO pin  ────────┤ Button ├──────── GND
                   (press to close)
```

| Function | GPIO (BCM) | Physical Pin | GND Pin to use |
|----------|-----------|-------------|----------------|
| **Send to Manager** (hold to talk) | GPIO 17 | Pin 11 | Pin 14 |
| **Send to Worker** (hold to talk) | GPIO 24 | Pin 18 | Pin 20 |
| **Call Manager** (press once) | GPIO 23 | Pin 16 | Pin 14 |
| **Play Message** (press or hold) | GPIO 25 | Pin 22 | Pin 25 |
| **Reminder** (hold to record) | GPIO 27 | Pin 13 | Pin 14 |
| **Handover** (press) | GPIO 22 | Pin 15 | Pin 14 |
| **Loudspeaker Mode** (toggle switch) | GPIO 26 | Pin 37 | Pin 39 |

> **Tip:** Run one jumper wire from any GND pin (e.g. Pin 14) to a breadboard ground rail. Then connect all the button GND legs to that shared rail — saves wiring.

---

### 4.4 Message Alert LED

The LED lights up when a new message arrives and blinks while unread messages are waiting.

```
Pi Pin 29 (GPIO 5)
       │
    [220Ω resistor]
       │
    LED Anode (+) ← long leg
    LED Cathode (−) ← short leg
       │
Pi Pin 30 (GND)
```

| Component | From | To |
|-----------|------|----|
| Wire | Pin 29 (GPIO 5) | 220Ω resistor leg 1 |
| 220Ω resistor | leg 1 | leg 2 → LED anode (long leg) |
| LED | anode (long) | cathode (short) |
| Wire | LED cathode (short) | Pin 30 (GND) |

> ⚠️ **Always use the 220Ω resistor.** Without it you will burn out the LED and may damage GPIO 5.

---

### 4.5 Power Supply

For **desktop testing:** use the official Raspberry Pi 4 USB-C power adapter (5V, 3A).

For **helmet use (portable):** use a USB power bank that outputs 5V, 2A or more via USB-C.

```
USB Power Bank (5V 2A+)
        │
   USB-C cable
        │
Pi USB-C port (power input)
```

> A 10,000 mAh power bank gives approximately 8–10 hours of continuous runtime on a Pi 4.

---

## 5. Complete Wiring Summary

```
┌──────────────────────────────────────────────────────────────────────┐
│                      Raspberry Pi 4                                   │
│                                                                        │
│  Pin  1 (3.3V) ──────────────────────────── SPH0645 VDD              │
│  Pin  2 (5V)   ──────────────────────────── MAX98357A Vin            │
│  Pin  6 (GND)  ──────────────────────────── SPH0645 GND              │
│  Pin  9 (GND)  ──────────────────────────── MAX98357A GND            │
│  Pin 11 (GPIO17) ──[BTN: Send to Manager]── GND rail                 │
│  Pin 12 (GPIO18) ──────────────────────────┬ SPH0645 BCLK            │
│                                             └ MAX98357A BCLK          │
│  Pin 13 (GPIO27) ──[BTN: Reminder]───────── GND rail                 │
│  Pin 14 (GND)  ──────────────────────────── GND rail (buttons)       │
│  Pin 15 (GPIO22) ──[BTN: Handover]────────── GND rail                │
│  Pin 16 (GPIO23) ──[BTN: Call Manager]────── GND rail                │
│  Pin 17 (3.3V) ──────────────────────────── SPH0645 SEL             │
│  Pin 18 (GPIO24) ──[BTN: Send to Worker]──── GND rail                │
│  Pin 20 (GND)  ──────────────────────────── [optional extra GND]     │
│  Pin 22 (GPIO25) ──[BTN: Play Message]────── GND rail                │
│  Pin 25 (GND)  ──────────────────────────── [optional extra GND]     │
│  Pin 29 (GPIO5)  ──[220Ω]──[LED+]──[LED−]── GND rail                │
│  Pin 30 (GND)  ──────────────────────────── [LED cathode / GND rail] │
│  Pin 35 (GPIO19) ──────────────────────────┬ SPH0645 LRCL            │
│                                             └ MAX98357A LRC           │
│  Pin 37 (GPIO26) ──[SW: Loudspeaker]──────── GND rail                 │
│  Pin 38 (GPIO20) ──────────────────────────── SPH0645 DOUT           │
│  Pin 40 (GPIO21) ──────────────────────────── MAX98357A DIN          │
│                                                                        │
│  MAX98357A Speaker+ ─────────────────────── Speaker red wire          │
│  MAX98357A Speaker− ─────────────────────── Speaker black wire        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 6. Software Setup

### 6.1 One-time Pi setup (run once, needs internet)

```bash
cd /home/pi
git clone https://github.com/faizanpk/smart-helmet.git
cd smart-helmet
chmod +x setup_pi.sh
sudo ./setup_pi.sh
```

This script automatically:
- Installs all Python packages
- Enables the I2S overlays for the mic and amp
- Disables onboard audio
- Downloads AI models (~300 MB)
- Installs a systemd auto-start service

After the script finishes:
```bash
sudo reboot
```

### 6.2 Configure each device

Open `config.py` on each device and set the correct values:

**Laptop 1 (Manager):**
```python
HELMET_ROLE = "manager"
HELMET_ID   = "manager"
MANAGER_IP  = "192.168.x.x"   # this laptop's own IP
```

**Raspberry Pi (Worker A):**
```python
HELMET_ROLE = "worker"
HELMET_ID   = "w-01"
MANAGER_IP  = "192.168.x.x"   # Laptop 1's IP
```

**Laptop 2 (Worker B):**
```python
HELMET_ROLE = "worker"
HELMET_ID   = "w-02"
MANAGER_IP  = "192.168.x.x"   # Laptop 1's IP
```

Also set `WORKER_IPS` on the manager so it knows each worker's IP for live calls:
```python
WORKER_IPS = {
    "w-01": "192.168.x.x",   # Pi's IP
    "w-02": "192.168.x.x",   # Laptop 2's IP
}
```

**How to find your IP:**
- Windows: open Command Prompt → `ipconfig` → look for "IPv4 Address"
- Pi / Linux: open Terminal → `hostname -I`

### 6.3 Verify audio after reboot (Pi only)

```bash
arecord -l    # should show: I2S mic (SPH0645)
aplay -l      # should show: I2S amp (MAX98357A)
```

The ALSA device name in `config.py` should match what you see:
```python
ALSA_MIC_DEVICE = "plughw:1,0"   # change if your device number differs
ALSA_SPK_DEVICE = "plughw:1,0"
```

### 6.4 Start the system

Start the **manager first**, then the workers.

```bash
# Laptop 1 (Manager)
python main.py manager

# Pi (Worker A) — or it auto-starts after reboot if systemd service is installed
python main.py worker

# Laptop 2 (Worker B)
python main.py worker
```

You hear *"Smart helmet ready."* on each device.
When a worker connects, the manager hears *"Worker 1 connected."*

---

## 7. Controls Reference

### Manager — Laptop 1 (keyboard)

| Key | Action |
|-----|--------|
| **1** | Select Worker A (Pi) as PTT target |
| **2** | Select Worker B (Laptop 2) as PTT target |
| Hold **SPACE** | Record and send voice message to selected worker |
| **F1** | Call / answer / hang up — Worker A (Pi) |
| **F2** | Call / answer / hang up — Worker B (Laptop 2) |
| **P** (1 tap) | Play next received message |
| **P** (2 taps) | Set language by voice |
| Hold **P** | Record and trigger emergency broadcast to all |
| **R** (1 tap) | Replay reminder |
| Hold **R** | Record a reminder |
| **H** (1 tap) | Play/replay shift handover |
| Hold **H** | Record shift handover |
| Hold **SPACE** during a call | Mute yourself (release to unmute) |

### Worker A — Raspberry Pi (physical buttons)

| Button (GPIO) | Action |
|--------------|--------|
| Hold **GPIO 17** (Pin 11) | Record and send voice message to manager |
| Hold **GPIO 24** (Pin 18) | Record and send voice message to the other worker |
| Press **GPIO 23** (Pin 16) | Call / answer / hang up — manager |
| Press **GPIO 25** (Pin 22) 1 time | Play next received message |
| Press **GPIO 25** (Pin 22) 2 times | Set language by voice |
| Hold **GPIO 25** (Pin 22) | Record and trigger emergency broadcast to all |
| Press **GPIO 27** (Pin 13) 1 time | Replay reminder |
| Hold **GPIO 27** (Pin 13) | Record a reminder |
| Press **GPIO 22** (Pin 15) 1 time | Play/replay shift handover |
| Hold **GPIO 22** (Pin 15) | Record shift handover |
| Hold **GPIO 17** during a call | Mute yourself (release to unmute) |

### Worker B — Laptop 2 (keyboard)

| Key | Action |
|-----|--------|
| Hold **SPACE** | Record and send voice message to manager |
| Hold **W** | Record and send voice message to the other worker |
| **C** | Call / answer / hang up — manager |
| **P** (1 tap) | Play next received message |
| **P** (2 taps) | Set language by voice |
| Hold **P** | Record and trigger emergency broadcast to all |
| **R** (1 tap) | Replay reminder |
| Hold **R** | Record a reminder |
| **H** (1 tap) | Play/replay shift handover |
| Hold **H** | Record shift handover |
| Hold **SPACE** during a call | Mute yourself (release to unmute) |

---

## 8. How to Use Each Feature

### 8.1 Sending a Voice Message

**Manager → Worker:**
1. Press **1** or **2** to pick the target. You hear *"Talking to worker 1."*
2. Hold **SPACE** and speak.
3. Release. You hear *"Message sent."*

**Worker → Manager:**
1. Hold **SPACE** (Laptop 2) or **GPIO 17** (Pi) and speak.
2. Release. You hear *"Message sent."*

**Worker → Worker:**
1. Hold **W** (Laptop 2) or **GPIO 24** (Pi) and speak.
2. Release. You hear *"Message sent."*

---

### 8.2 Receiving and Playing a Message

When a message arrives the receiver hears 3 beeps + TTS: *"1 message received. Press P to play."*
On the Pi, the LED also blinks rapidly.

**To play:**
1. Tap **P** (or GPIO 25 on Pi) once.
2. You hear a short preview: *"From manager: Please check the..."*
3. The full message plays in your configured language. If the sender spoke a different language it is automatically translated.
4. LED turns off when all messages are played.

---

### 8.3 Live Calls

Only manager ↔ worker. Workers cannot call each other.

**The call button has 4 states — same button, different action each time:**

| State when you press | Result |
|---------------------|--------|
| Idle | Calls the partner |
| You called — waiting for answer | Cancels the call |
| Partner is calling you — ringing | Answers the call |
| In a call | Hangs up |

**Manager calls Worker A:**
1. Press **F1** → *"Calling worker 1."*
2. Worker A: 3 beeps + LED blinks + *"Incoming call from manager. Press call button to answer."*
3. Worker A presses **GPIO 23** → both hear *"Call connected."*
4. Either side presses their call button → *"Call ended."*

**Worker calls Manager:**
1. Worker presses **C** or **GPIO 23** → *"Calling manager."*
2. Manager: 3 beeps + *"Incoming call from worker 1. Press F1 to answer."*
3. Manager presses **F1** → both hear *"Call connected."*
4. Either side presses their call button → *"Call ended."*

**Mute:** Hold **SPACE** or **GPIO 17** during a call. Release to unmute.

**Unanswered:** Call auto-cancels after 30 seconds.

---

### 8.4 Setting the Language

1. Tap **P** (or GPIO 25) **twice**.
2. *"Language setup. Say English or German."*
3. Say **"English"** or **"German"**.
4. Release. *"Configured for English."* or *"Konfiguriert für Deutsch."*

Saved automatically and reloaded on every boot.

---

### 8.5 Reminders

**Record a reminder:**
1. Hold **R** and speak: *"Check pressure valve in 20 minutes."*
2. Release. The system saves it and plays it automatically at the right time.

**Replay a reminder:**
1. Tap **R** once.

---

### 8.6 Shift Handover

**End of shift (recording):** Hold **H** (or GPIO 22) and speak your notes. Release to save.

**Start of shift (playback):** Tap **H** (or GPIO 22) once — the previous shift's notes or the last recorded notes play back.

---

### 8.7 Emergency Broadcast

Hold **P** (or GPIO 25) to record and broadcast a priority emergency alert to all helmets. Release to send.

---

### 8.8 Loudspeaker Mode (External Speaker)

When you take the helmet off, you can route all audio to a loud external USB speaker. 

**Hardware Setup:**
1. Plug an external USB speaker into one of the Pi's USB ports. 
2. Wire a physical **Toggle Switch** between **GPIO 26 (Pin 37)** and **GND (Pin 39)**.

**How to use:**
*   **Switch ON (Closed):** Loudspeaker Mode. All incoming messages, calls, and TTS announcements will play through the loud external USB speaker. 
*   **Switch OFF (Open):** Normal Mode. Audio plays securely through the helmet's internal I2S speaker.

## 9. LED Indicator (Pi only)

| LED Behaviour | Meaning |
|--------------|---------|
| Rapid blink (10 times fast) | New message just arrived |
| Slow blink (continuous) | Unread messages waiting in queue |
| OFF | No unread messages |

---

## 10. Network Ports

All ports must be reachable between devices on your Wi-Fi network.

| Port | Protocol | Used for |
|------|----------|---------|
| 5005 | TCP | Manager ↔ Worker messages and call signalling |
| 5006 | UDP | Live call audio — Manager ↔ Worker A (send) |
| 5007 | TCP | Worker A ↔ Worker B messages |
| 5007 | UDP | Live call audio — Manager ↔ Worker A (receive) |
| 5008 | UDP | Live call audio — Manager ↔ Worker B (send) |
| 5009 | UDP | Live call audio — Manager ↔ Worker B (receive) |

---

## 11. Pre-Deployment Tests

```bash
python test_suite.py 1    # all modules import correctly
python test_suite.py 3    # microphone recording works
python test_suite.py 4    # speaker and text-to-speech work
python test_suite.py 5    # speech recognition works
python test_suite.py 6    # translation EN ↔ DE works
python test_suite.py 7    # network connects manager to worker
```

Run all tests at once:
```bash
python test_suite.py
```

---

## 12. Troubleshooting

| Problem | What to check |
|---------|--------------|
| No sound from speaker | Run `aplay -l` — does MAX98357A appear? Check DIN wire (Pi Pin 40 → MAX98357A DIN) |
| Microphone not recording | Run `arecord -l` — does SPH0645 appear? Check DOUT wire (SPH0645 DOUT → Pi Pin 38). Check SEL is tied to 3.3V |
| Button does nothing | Open a terminal: `python -c "import RPi.GPIO as G; G.setmode(G.BCM); G.setup(17, G.IN, pull_up_down=G.PUD_UP); print(G.input(17))"` — should print `1` (unpressed) and `0` (pressed) |
| LED does not blink | Check: 220Ω resistor present? Long leg of LED to resistor, short leg to GND. Test: `python -c "import RPi.GPIO as G; G.setmode(G.BCM); G.setup(5, G.OUT); G.output(5, 1)"` — LED should light |
| Worker cannot connect | Both devices on same Wi-Fi? Is `MANAGER_IP` correct in config.py? |
| Message in wrong language | Long-hold P for 3 seconds and say "English" or "German" |
| Speech not recognised | Check `WHISPER_MODEL_SIZE = "tiny"` in config.py for Pi (faster). Speak clearly in a quiet environment |
| F1/F2 keys do nothing | Run terminal as administrator on Windows. Check `pip install keyboard` is done |
