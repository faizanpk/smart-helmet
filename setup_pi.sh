#!/usr/bin/env bash
# setup_pi.sh – One-time hardware and software setup for the Worker Raspberry Pi 4.
#
# Run as root (or with sudo):
#   chmod +x setup_pi.sh
#   sudo ./setup_pi.sh
#
# What this script does:
#   1.  Install system packages (PortAudio, ALSA utils, espeak-ng, Python 3)
#   2.  Enable the I2S overlay for the SPH0645LM4H microphone
#   3.  Enable the I2S overlay for the MAX98357A amplifier
#   4.  Disable onboard audio so I2S devices become card 0
#   5.  Install Python dependencies from requirements.txt
#   6.  Pre-download offline AI models (Whisper + OPUS-MT) – needs internet once
#   7.  Install and enable a systemd service for auto-start on boot

set -euo pipefail

HELMET_DIR="/home/pi/smart-helmet"
VENV_DIR="$HELMET_DIR/.venv"
BOOT_CONFIG="/boot/firmware/config.txt"   # Bookworm path

# Fallback for older Bullseye
if [ ! -f "$BOOT_CONFIG" ]; then
    BOOT_CONFIG="/boot/config.txt"
fi

echo "=== Smart Helmet Pi Setup ==="
echo "Project dir : $HELMET_DIR"
echo "Boot config : $BOOT_CONFIG"
echo ""

# 1. System packages
echo "[1/7] Installing system packages..."
apt-get update -q
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    portaudio19-dev python3-pyaudio \
    alsa-utils \
    espeak-ng espeak-ng-data \
    git wget curl
echo "    Done."

# 2. I2S microphone overlay (SPH0645LM4H)
echo "[2/7] Enabling I2S microphone overlay (SPH0645LM4H)..."
if ! grep -q "i2s-mems-mic" "$BOOT_CONFIG"; then
    cat >> "$BOOT_CONFIG" <<'EOF'

# Smart Helmet - I2S microphone (Adafruit SPH0645LM4H)
dtparam=i2s=on
dtoverlay=i2s-mems-mic
EOF
    echo "    I2S mic overlay added."
else
    echo "    Already present - skipping."
fi

# 3. I2S amplifier overlay (MAX98357A)
echo "[3/7] Enabling I2S amplifier overlay (MAX98357A)..."
if ! grep -q "max98357a" "$BOOT_CONFIG"; then
    cat >> "$BOOT_CONFIG" <<'EOF'

# Smart Helmet - I2S amplifier (Adafruit MAX98357A)
dtoverlay=max98357a
EOF
    echo "    I2S amp overlay added."
else
    echo "    Already present - skipping."
fi

# 4. Disable onboard audio
echo "[4/7] Disabling onboard audio..."
if ! grep -q "^dtparam=audio=off" "$BOOT_CONFIG"; then
    echo "dtparam=audio=off" >> "$BOOT_CONFIG"
    echo "    Onboard audio disabled (I2S devices will be card 0)."
else
    echo "    Already disabled - skipping."
fi

# 5. Python virtual environment and dependencies
echo "[5/7] Installing Python dependencies..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$HELMET_DIR/requirements.txt" -q
echo "    Python dependencies installed in $VENV_DIR"

# 6. Pre-download offline AI models
echo "[6/7] Downloading offline AI models (Whisper + OPUS-MT, ~300 MB, once only)..."
"$VENV_DIR/bin/python" -c "
import sys
sys.path.insert(0, '$HELMET_DIR')
from translation import setup_offline_models
setup_offline_models()
print('Translation models ready.')
"
"$VENV_DIR/bin/python" -c "
from faster_whisper import WhisperModel
print('Downloading Whisper tiny model...')
WhisperModel('tiny', device='cpu', compute_type='int8')
print('Whisper ready.')
"
echo "    All offline models cached."

# 7. systemd service
echo "[7/7] Installing systemd auto-start service..."
SERVICE_FILE="/etc/systemd/system/smart-helmet.service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Smart Helmet Communication System
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=$HELMET_DIR
ExecStart=$VENV_DIR/bin/python $HELMET_DIR/main.py worker
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable smart-helmet.service
echo "    systemd service installed and enabled."

echo ""
echo "============================================================"
echo "  Setup complete!"
echo "============================================================"
echo ""
echo "  NEXT STEPS:"
echo "  1. Set PARTNER_IP to your laptop IP in config.py:"
echo "       nano $HELMET_DIR/config.py"
echo ""
echo "  2. Reboot for I2S overlays to take effect:"
echo "       sudo reboot"
echo ""
echo "  3. After reboot, verify audio devices:"
echo "       arecord -l   <- should show I2S mic"
echo "       aplay -l     <- should show I2S amp"
echo ""
echo "  4. Start the helmet:"
echo "       sudo systemctl start smart-helmet"
echo "       sudo journalctl -fu smart-helmet"
echo ""
