#!/bin/bash
# NotePin Pi — Installationsskript
# Kör på en ny Raspberry Pi OS Lite-installation:
#   chmod +x setup.sh && sudo ./setup.sh
#
# Installerar: NetworkManager, BlueZ, Python-beroenden,
# WM8960-drivrutiner, systemd-service

set -e

echo "=========================================="
echo "  NotePin Pi — Installation"
echo "=========================================="

# Kontrollera att vi kör som root
if [ "$EUID" -ne 0 ]; then
    echo "Kör med sudo: sudo ./setup.sh"
    exit 1
fi

NOTEPIN_DIR="$(cd "$(dirname "$0")" && pwd)"
PI_USER="${SUDO_USER:-pi}"

echo ""
echo "[1/6] Uppdaterar systemet..."
apt-get update -qq
apt-get upgrade -y -qq

echo ""
echo "[2/6] Installerar systemberoenden..."
apt-get install -y -qq \
    python3-pip \
    python3-dev \
    python3-venv \
    git \
    network-manager \
    bluez \
    bluez-tools \
    libasound2-dev \
    portaudio19-dev \
    libsndfile1-dev \
    flac

# Säkerställ att NetworkManager körs (standard i Trixie, behövs i Bookworm)
echo ""
echo "[3/6] Konfigurerar NetworkManager + Bluetooth..."
if systemctl is-active --quiet dhcpcd 2>/dev/null; then
    echo "  Byter från dhcpcd till NetworkManager (Bookworm-kompatibilitet)..."
    systemctl disable --now dhcpcd
fi
systemctl enable --now NetworkManager
systemctl enable --now bluetooth

echo ""
echo "[4/6] Installerar Python-beroenden..."
pip3 install --break-system-packages -r "$NOTEPIN_DIR/requirements.txt"

echo ""
echo "[5/6] Konfigurerar I2C och ljud..."
# Aktivera I2C (för batteriövervakning)
raspi-config nonint do_i2c 0

# Aktivera I2S (för WM8960 Audio HAT)
# Sökväg: /boot/firmware/config.txt (Bookworm+/Trixie)
BOOT_CONFIG="/boot/firmware/config.txt"
if [ ! -f "$BOOT_CONFIG" ]; then
    BOOT_CONFIG="/boot/config.txt"  # Fallback för äldre versioner
fi

if ! grep -q "dtoverlay=wm8960-soundcard" "$BOOT_CONFIG" 2>/dev/null; then
    echo "dtoverlay=wm8960-soundcard" >> "$BOOT_CONFIG"
    echo "  WM8960 overlay tillagd i $BOOT_CONFIG"
fi

# Skapa recordings-mapp
mkdir -p "/home/$PI_USER/recordings"
chown "$PI_USER:$PI_USER" "/home/$PI_USER/recordings"

echo ""
echo "[6/6] Installerar systemd-service..."
cp "$NOTEPIN_DIR/config/notepin.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable notepin

echo ""
echo "=========================================="
echo "  Installation klar!"
echo "=========================================="
echo ""
echo "Nästa steg:"
echo "  1. Kopiera config:"
echo "     cp config/config.example.yaml config/config.yaml"
echo ""
echo "  2. Fyll i Supabase-credentials i config.yaml"
echo ""
echo "  3. Starta om Pi:n:"
echo "     sudo reboot"
echo ""
echo "  4. Para ihop med appen via BLE"
echo ""
echo "Loggar: journalctl -u notepin -f"
echo "=========================================="
