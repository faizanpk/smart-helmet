# Smart Helmet – Industrial Coordination System

A lightweight communication system for managers and workers on industrial and construction sites. Each worker wears a Raspberry Pi-powered helmet equipped with an I2S microphone and speaker. They can configure language, send asynchronous voice messages, trigger emergency broadcasts, setup task reminders via voice, record handover notes, and establish live VoIP calls — all routed through a Manager (site supervisor) over local Wi-Fi. Incoming voice messages are automatically transcribed using offline Speech-to-Text (faster-whisper), translated via Google Translate, and played back using offline Text-to-Speech (piper-tts). The system is designed to run on a Raspberry Pi 4 at the edge with minimal cloud dependency, enabling basic coordination even in subterranean or remote environments

---

## Installation

Requires Python 3.10+. Install all dependencies:

```bash
pip install -r requirements.txt
```

Then download the offline voice models for TTS:

```bash
python -m piper.download_voices en_US-lessac-medium
python -m piper.download_voices de_DE-thorsten-medium
```

---

## How to Run

Each device runs the same `main.py` script with a role argument:

```bash
# On the Manager laptop (site supervisor):
python main.py manager

# On a Worker helmet (Raspberry Pi or laptop):
python main.py worker
```

---

## Configuration (`config.py`)

Before running, edit `config.py` to match your network setup and hardware:

| Setting | Description | Example |
| :--- | :--- | :--- |
| `HELMET_ROLE` | Role of this device | `"manager"` or `"worker"` |
| `HELMET_ID` | Unique ID for this device | `"w-01"`, `"w-02"` |
| `MANAGER_IP` | LAN IP address of the Manager laptop | `""` |
| `WORKER_IPS` | LAN IP addresses of each worker | `{w-01: "",}` |
| `HELMET_LANGUAGE` | Language this user speaks | `"en"` or `"de"` |
| `WHISPER_MODEL_SIZE` | STT model size | `"tiny"` (Pi) or `"base"` (laptop) |
| `ALSA_MIC_DEVICE` | ALSA device for I2S mic (Linux only) | `"plughw:2,0"` |
| `ALSA_SPK_DEVICE` | ALSA device for I2S amplifier (Linux only) | `"plughw:2,1"` |

---

## Hardware List (Raspberry Pi Helmet)

| Component | Model | Purpose |
| :--- | :--- | :--- |
| Single Board Computer | Raspberry Pi 4 (4 GB RAM) | Main compute & networking |
| I2S Microphone | SPH0645 | Voice capture |
| I2S Amplifier | MAX98357A | Audio playback |
| Speaker | 4Ω / 3W Mini Speaker | Voice output |
| Push Buttons | 6× Tactile Push Buttons | Controls (PTT, Play, Call, Reminder, etc.) |
| Power Button | 1× Momentary Push Button | GPIO 3 safe power ON/OFF |
| LED | 1× Standard LED + 330Ω resistor | Unread message indicator |
| Power Supply | 5V / 3A USB-C | Raspberry Pi power |

### GPIO Pinout
| Function | GPIO | Physical Pin |
| :--- | :--- | :--- |
| Power ON / Shutdown | GPIO 3 | Pin 5 |
| Speak to Manager | GPIO 17 | Pin 11 |
| Speak to Peer Worker | GPIO 24 | Pin 18 |
| Call Manager | GPIO 23 | Pin 16 |
| Play Message | GPIO 25 | Pin 22 |
| Handover / Emergency | GPIO 22 | Pin 15 |
| Reminder | GPIO 27 | Pin 13 |
| Message Alert LED | GPIO 5 | Pin 29 |

---

## Known Limitations

1. **Translation requires internet.** Speech-to-Text and Text-to-Speech run fully offline on the edge. However, the translation step (`deep-translator`) calls the Google Translate Cloud API and requires a working internet connection. A future improvement is replacing this with a locally hosted model (e.g., Argos Translate).

2. **Whisper latency on Raspberry Pi.** Running the `tiny` STT model on a Pi 4 CPU typically takes 1.5 – 2.5 seconds per message. This is acceptable for asynchronous messaging but is not suitable for real-time command processing. Using a Pi 5 or enabling a Coral USB Accelerator would significantly reduce this latency.

3. **Static IP configuration.** Worker and Manager IP addresses must be manually set in `config.py` before deployment. Automatic device discovery is not yet implemented.

4. **Live calls are Manager ↔ Worker only.** Workers cannot initiate live VoIP audio streams directly with each other. Peer communication is limited to asynchronous text/voice messages over TCP.

5. **Windows Firewall.** When testing on Windows laptops, incoming UDP packets (ports 5006–5009) for live calls may be blocked by Windows Defender Firewall. Ensure `python.exe` is allowed through the firewall for both Private and Public networks.
