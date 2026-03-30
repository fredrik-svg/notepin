#!/bin/bash
# Testa att WM8960 Audio HAT fungerar
# Kör: chmod +x scripts/test_audio.sh && ./scripts/test_audio.sh

set -e

echo "NotePin — Ljudtest"
echo "=================="

# Kontrollera att ljudenhet finns
echo ""
echo "[1] Söker ljudenheter..."
arecord -l

echo ""
echo "[2] Spelar in 5 sekunder..."
arecord -D plughw:0,0 -f S32_LE -r 44100 -c 2 -d 5 /tmp/notepin_test.wav
echo "  Inspelning klar!"

echo ""
echo "[3] Filinformation:"
file /tmp/notepin_test.wav
ls -lh /tmp/notepin_test.wav

echo ""
echo "[4] Spelar upp (om hörlursutgång är ansluten)..."
aplay -D plughw:0,0 /tmp/notepin_test.wav 2>/dev/null || echo "  Ingen uppspelningsenhet — hoppar över"

echo ""
echo "Klart! Om du hörde inspelningen fungerar mikrofonen."
echo "Testfil: /tmp/notepin_test.wav"
