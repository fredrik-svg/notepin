#!/bin/bash
# Testa att Bluetooth/BLE fungerar
# Kör: chmod +x scripts/test_ble.sh && ./scripts/test_ble.sh

set -e

echo "NotePin — BLE-test"
echo "=================="

echo ""
echo "[1] Bluetooth-status:"
bluetoothctl show | head -10

echo ""
echo "[2] Kontrollerar BLE-stöd..."
if hciconfig hci0 2>/dev/null | grep -q "UP RUNNING"; then
    echo "  BLE-adapter aktiv!"
else
    echo "  BLE-adapter INTE aktiv — startar..."
    sudo hciconfig hci0 up
fi

echo ""
echo "[3] Sätter enhetsnamn..."
SERIAL=$(grep Serial /proc/cpuinfo | awk '{print $3}' | tail -c 5)
NAME="NotePin-${SERIAL}"
bluetoothctl system-alias "$NAME"
echo "  BLE-namn: $NAME"

echo ""
echo "[4] Aktiverar annonsering..."
bluetoothctl discoverable on
bluetoothctl pairable on

echo ""
echo "Klart! Enheten bör nu synas som '$NAME' vid BLE-skanning."
echo "Tryck Ctrl+C för att avsluta."
echo ""

# Håll scriptet igång så enheten fortsätter annonsera
bluetoothctl advertise on
