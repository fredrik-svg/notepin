# NotePin Pi — Raspberry Pi Voice Recorder

AI-driven röstinspelare byggd på Raspberry Pi Zero 2W med BLE-provisioning och Supabase-backend.

## Repostruktur

```
notepin-pi/
├── README.md
├── requirements.txt           # Python-beroenden
├── setup.sh                   # Installationsskript (körs en gång på ny Pi)
├── config/
│   ├── config.example.yaml    # Exempelkonfiguration
│   ├── asound.conf            # ALSA-konfiguration för WM8960
│   └── notepin.service        # Systemd service-fil
├── src/
│   ├── main.py                # Huvudprocess — startar alla subsystem
│   ├── recorder.py            # Ljudinspelning via ALSA/PyAudio
│   ├── ble_server.py          # BLE GATT-server (pairing + WiFi-provisioning)
│   ├── uploader.py            # WiFi-upload till Supabase Storage
│   ├── led_controller.py      # RGB LED-status (inspelning, sync, batteri)
│   ├── button_handler.py      # GPIO-knapp (start/stopp, highlight)
│   ├── battery_monitor.py     # LiPo-batteriövervakning via I2C
│   ├── wifi_manager.py        # NetworkManager-styrning (anslut/koppla)
│   ├── updater.py             # OTA-uppdatering via git pull
│   └── utils/
│       ├── audio_filters.py   # Högpass, AGC, noise gate
│       ├── config_loader.py   # Läs YAML-konfiguration
│       └── logger.py          # Loggning till fil + systemd journal
├── scripts/
│   ├── install_wm8960.sh      # Installera WM8960 Audio HAT-drivrutiner
│   ├── install_ble.sh         # Installera BlueZ + BLE-beroenden
│   ├── provision.sh           # Första konfiguration av ny Pi
│   └── test_audio.sh          # Testa mikrofon + inspelning
└── tests/
    ├── test_recorder.py
    ├── test_ble.py
    └── test_uploader.py
```

## Snabbstart

### 1. Flasha Raspberry Pi OS Lite (64-bit) på SD-kort

Använd Raspberry Pi Imager. Aktivera SSH och ange WiFi-credentials för initial setup.

### 2. Klona repot på Pi:n

```bash
ssh pi@notepin.local
git clone https://github.com/DITT-KONTO/notepin-pi.git
cd notepin-pi
```

### 3. Kör installationsskriptet

```bash
chmod +x setup.sh
sudo ./setup.sh
```

### 4. Konfigurera

```bash
cp config/config.example.yaml config/config.yaml
nano config/config.yaml  # Fyll i Supabase URL + anon key
```

### 5. Starta

```bash
sudo systemctl start notepin
sudo systemctl enable notepin  # Autostart vid boot
```

## OTA-uppdateringar

Vid varje boot kontrollerar Pi:n om det finns ny kod:

```python
# updater.py (förenklat)
result = subprocess.run(["git", "pull"], capture_output=True)
if "Already up to date" not in result.stdout:
    subprocess.run(["sudo", "systemctl", "restart", "notepin"])
```

## Hårdvara

| Komponent | Modell |
|-----------|--------|
| SBC | Raspberry Pi Zero 2W |
| Audio HAT | WM8960 (dual MEMS mic, I2S) |
| Batteri | UPS HAT + 3.7V 2500mAh LiPo |
| Knapp | Taktil momentan (GPIO17) |
| LED | WS2812B RGB eller enkel RGB LED (GPIO18) |
| Lagring | 64GB MicroSD (A2) |

## Konfiguration (config.yaml)

```yaml
supabase:
  url: "https://xxxxx.supabase.co"
  anon_key: "eyJ..."
  storage_bucket: "recordings"

audio:
  sample_rate: 44100
  bit_depth: 24
  channels: 2
  format: "flac"            # wav eller flac
  highpass_hz: 80
  agc_enabled: true
  noise_gate_db: -45

ble:
  device_name: "NotePin"    # BLE-annonseringsnamn
  service_uuid: "12345678-1234-1234-1234-123456789abc"

gpio:
  button_pin: 17            # BCM-numrering
  led_pin: 18
  led_type: "ws2812b"       # ws2812b eller rgb

device:
  check_updates_on_boot: true
  upload_on_wifi: true
  max_recording_hours: 4
```

## Licens

MIT
