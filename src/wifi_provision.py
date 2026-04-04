"""WiFi AP Provisioning — Captive Portal för NotePin-setup.

Fungerar på ALLA enheter (iPhone, Android, desktop) utan BLE.

Flöde:
  1. Pi startar hotspot "NotePin-XXXX" (sista 4 i serienummer)
  2. Användaren ansluter till hotspottet
  3. Captive portal öppnas automatiskt (eller http://192.168.4.1)
  4. Användaren fyller i WiFi-credentials + appen skickar auth
  5. Pi ansluter till WiFi och registrerar sig i Supabase

Tekniskt:
  - hostapd för AP-läge (eller NetworkManager AP)
  - dnsmasq för DHCP + DNS-redirect (captive portal)
  - Enkel HTTP-server på port 80
"""

import asyncio
import json
import subprocess
import socket
import logging
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from src.utils.config_loader import get_device_serial
from src.utils.logger import setup_logger

logger = setup_logger("notepin.wifi_provision")

AP_IP = "192.168.4.1"
AP_SUBNET = "192.168.4.0/24"
AP_DHCP_RANGE = "192.168.4.10,192.168.4.50,24h"
AP_INTERFACE = "wlan0"


def _get_ap_ssid() -> str:
    """Generera AP-namn från serienummer."""
    serial = get_device_serial()
    return f"NotePin-{serial[-4:]}"


# ─── HTML för captive portal ───────────────────────────────────

PORTAL_HTML = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NotePin Setup</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    min-height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 20px;
  }
  .card {
    background: #1e293b;
    border-radius: 16px;
    padding: 32px 24px;
    max-width: 400px;
    width: 100%;
    box-shadow: 0 25px 50px rgba(0,0,0,0.5);
  }
  .logo {
    text-align: center;
    margin-bottom: 24px;
  }
  .logo svg { width: 48px; height: 48px; }
  h1 {
    font-size: 24px;
    font-weight: 700;
    text-align: center;
    margin-bottom: 8px;
  }
  .subtitle {
    text-align: center;
    color: #94a3b8;
    font-size: 14px;
    margin-bottom: 24px;
  }
  .device-id {
    text-align: center;
    font-family: monospace;
    font-size: 12px;
    color: #64748b;
    margin-bottom: 24px;
    padding: 8px;
    background: #0f172a;
    border-radius: 8px;
  }
  label {
    display: block;
    font-size: 14px;
    font-weight: 500;
    margin-bottom: 6px;
    color: #cbd5e1;
  }
  input, select {
    width: 100%;
    padding: 12px 16px;
    border: 1px solid #334155;
    border-radius: 10px;
    background: #0f172a;
    color: #e2e8f0;
    font-size: 16px;
    margin-bottom: 16px;
    outline: none;
    transition: border-color 0.2s;
    -webkit-appearance: none;
  }
  input:focus, select:focus {
    border-color: #6366f1;
  }
  .btn {
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 10px;
    background: #6366f1;
    color: white;
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
  }
  .btn:hover { background: #4f46e5; }
  .btn:disabled {
    background: #334155;
    color: #64748b;
    cursor: not-allowed;
  }
  .spinner {
    display: none;
    text-align: center;
    padding: 20px;
  }
  .spinner.active { display: block; }
  .spinner svg {
    animation: spin 1s linear infinite;
    width: 32px;
    height: 32px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .status {
    text-align: center;
    padding: 16px;
    border-radius: 10px;
    margin-top: 16px;
    display: none;
    font-size: 14px;
  }
  .status.success {
    display: block;
    background: #064e3b;
    color: #6ee7b7;
  }
  .status.error {
    display: block;
    background: #7f1d1d;
    color: #fca5a5;
  }
  .divider {
    border-top: 1px solid #334155;
    margin: 20px 0;
  }
  .section-title {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #64748b;
    margin-bottom: 12px;
  }
  .scan-btn {
    font-size: 13px;
    color: #6366f1;
    background: none;
    border: none;
    cursor: pointer;
    float: right;
    margin-top: -22px;
  }
  #wifi-list {
    margin-bottom: 16px;
  }
  .wifi-item {
    padding: 10px 12px;
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 8px;
    margin-bottom: 6px;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    transition: border-color 0.2s;
  }
  .wifi-item:hover { border-color: #6366f1; }
  .wifi-item.selected {
    border-color: #6366f1;
    background: #1e1b4b;
  }
  .wifi-name { font-size: 14px; }
  .wifi-signal { font-size: 12px; color: #64748b; }
  .hidden { display: none; }
  .app-link {
    text-align: center;
    margin-top: 16px;
    font-size: 13px;
    color: #64748b;
  }
  .app-link a { color: #6366f1; text-decoration: none; }
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="24" cy="24" r="22" stroke="#6366f1" stroke-width="3"/>
      <circle cx="24" cy="18" r="6" fill="#6366f1"/>
      <rect x="21" y="26" width="6" height="12" rx="3" fill="#6366f1"/>
    </svg>
  </div>

  <h1>NotePin Setup</h1>
  <p class="subtitle">Anslut din NotePin till WiFi</p>
  <div class="device-id">Enhet: DEVICE_SERIAL</div>

  <form id="setup-form">
    <!-- WiFi-sektion -->
    <div class="section-title">WiFi-nätverk</div>

    <div id="wifi-list"></div>
    <button type="button" class="scan-btn" onclick="scanWifi()">Sök nätverk</button>

    <label for="ssid">Nätverksnamn (SSID)</label>
    <input type="text" id="ssid" name="ssid" required
           placeholder="Välj från listan eller skriv manuellt">

    <label for="password">Lösenord</label>
    <input type="password" id="password" name="password"
           placeholder="WiFi-lösenord">

    <div class="divider"></div>

    <!-- Auth-sektion (dold — fylls i automatiskt av appen) -->
    <div id="auth-section" class="hidden">
      <div class="section-title">App-koppling</div>
      <input type="hidden" id="user_id" name="user_id">
      <input type="hidden" id="refresh_token" name="refresh_token">
      <input type="hidden" id="supabase_url" name="supabase_url">
      <input type="hidden" id="anon_key" name="anon_key">
      <p style="font-size:13px;color:#6ee7b7;margin-bottom:16px;">
        ✓ App-credentials mottagna
      </p>
    </div>

    <!-- Manuell auth-sektion (visas om appen inte skickat automatiskt) -->
    <div id="manual-auth" class="hidden">
      <div class="section-title">Parkopplingskod</div>
      <label for="pairing_code">Kod från appen</label>
      <input type="text" id="pairing_code" name="pairing_code"
             placeholder="Klistra in koden från appen"
             style="font-family:monospace;">
      <p style="font-size:12px;color:#64748b;margin-bottom:16px;">
        Öppna NotePin-appen → Enheter → "Visa parkopplingskod"
      </p>
    </div>

    <button type="submit" class="btn" id="submit-btn">Anslut</button>
  </form>

  <div class="spinner" id="spinner">
    <svg viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2">
      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
    </svg>
    <p style="margin-top:12px;color:#94a3b8;">Ansluter till WiFi...</p>
  </div>

  <div class="status" id="status"></div>

  <div class="app-link">
    Har du inte appen? <a href="APP_URL" target="_blank">Ladda ner här</a>
  </div>
</div>

<script>
  // Kolla URL-parametrar (appen kan skicka auth via URL)
  const params = new URLSearchParams(window.location.search);
  if (params.get('user_id')) {
    document.getElementById('user_id').value = params.get('user_id');
    document.getElementById('refresh_token').value = params.get('refresh_token') || '';
    document.getElementById('supabase_url').value = params.get('supabase_url') || '';
    document.getElementById('anon_key').value = params.get('anon_key') || '';
    document.getElementById('auth-section').classList.remove('hidden');
  } else {
    document.getElementById('manual-auth').classList.remove('hidden');
  }

  // Sök WiFi-nätverk
  async function scanWifi() {
    try {
      const res = await fetch('/api/scan');
      const networks = await res.json();
      const list = document.getElementById('wifi-list');
      list.innerHTML = '';
      networks.forEach(n => {
        const div = document.createElement('div');
        div.className = 'wifi-item';
        div.innerHTML = '<span class="wifi-name">' + n.ssid + '</span>' +
                        '<span class="wifi-signal">' + n.signal + '%</span>';
        div.onclick = () => {
          document.getElementById('ssid').value = n.ssid;
          document.querySelectorAll('.wifi-item').forEach(i => i.classList.remove('selected'));
          div.classList.add('selected');
          document.getElementById('password').focus();
        };
        list.appendChild(div);
      });
    } catch (e) {
      console.error('Scan failed:', e);
    }
  }

  // Skicka formuläret
  document.getElementById('setup-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const btn = document.getElementById('submit-btn');
    const spinner = document.getElementById('spinner');
    const status = document.getElementById('status');
    const form = document.getElementById('setup-form');

    btn.disabled = true;
    form.style.display = 'none';
    spinner.classList.add('active');
    status.style.display = 'none';

    const data = {
      ssid: document.getElementById('ssid').value,
      password: document.getElementById('password').value,
      user_id: document.getElementById('user_id').value,
      refresh_token: document.getElementById('refresh_token').value,
      supabase_url: document.getElementById('supabase_url').value,
      anon_key: document.getElementById('anon_key').value,
      pairing_code: document.getElementById('pairing_code').value,
    };

    try {
      const res = await fetch('/api/provision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const result = await res.json();

      spinner.classList.remove('active');

      if (result.success) {
        status.className = 'status success';
        status.textContent = '✓ ' + result.message;
        status.style.display = 'block';
      } else {
        status.className = 'status error';
        status.textContent = '✗ ' + result.message;
        status.style.display = 'block';
        form.style.display = 'block';
        btn.disabled = false;
      }
    } catch (err) {
      spinner.classList.remove('active');
      status.className = 'status error';
      status.textContent = '✗ Anslutningsfel — försök igen';
      status.style.display = 'block';
      form.style.display = 'block';
      btn.disabled = false;
    }
  });

  // Auto-scan vid sidladdning
  scanWifi();
</script>
</body>
</html>"""


# ─── HTTP-server ───────────────────────────────────────────────

class ProvisionHandler(BaseHTTPRequestHandler):
    """HTTP-handler för captive portal."""

    # Referens till WiFiProvisionServer-instansen (sätts av servern)
    provision_server = None

    def log_message(self, format, *args):
        """Logga via vår logger istället för stderr."""
        logger.debug(f"HTTP: {format % args}")

    def do_GET(self):
        """Serva captive portal-sidan."""
        parsed = urlparse(self.path)

        # WiFi-scan endpoint
        if parsed.path == "/api/scan":
            self._handle_scan()
            return

        # Status endpoint (för appen att polla)
        if parsed.path == "/api/status":
            self._handle_status()
            return

        # Captive portal detection (Apple, Google, Microsoft)
        captive_paths = [
            "/hotspot-detect.html",      # Apple
            "/library/test/success.html", # Apple
            "/generate_204",             # Google
            "/gen_204",                  # Google
            "/ncsi.txt",                 # Microsoft
            "/connecttest.txt",          # Microsoft
            "/redirect",                 # Android
            "/success.txt",              # Firefox
            "/canonical.html",           # Firefox
        ]

        if parsed.path in captive_paths:
            # Redirect till portal
            self.send_response(302)
            self.send_header("Location", f"http://{AP_IP}/")
            self.end_headers()
            return

        # Huvudsidan
        serial = get_device_serial()
        html = PORTAL_HTML.replace("DEVICE_SERIAL", serial)
        html = html.replace("APP_URL", "#")  # TODO: riktig app-URL

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_POST(self):
        """Hantera provisioning-data."""
        if self.path == "/api/provision":
            self._handle_provision()
        else:
            self.send_error(404)

    def _handle_scan(self):
        """Skanna efter WiFi-nätverk."""
        networks = []
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
                 "--rescan", "yes"],
                capture_output=True, text=True, timeout=15,
            )
            seen = set()
            for line in result.stdout.strip().split("\n"):
                parts = line.split(":")
                if len(parts) >= 2 and parts[0] and parts[0] not in seen:
                    seen.add(parts[0])
                    networks.append({
                        "ssid": parts[0],
                        "signal": int(parts[1]) if parts[1].isdigit() else 0,
                        "security": parts[2] if len(parts) > 2 else "",
                    })
            # Sortera efter signalstyrka
            networks.sort(key=lambda n: n["signal"], reverse=True)
        except Exception as e:
            logger.warning(f"WiFi-scan misslyckades: {e}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(networks).encode())

    def _handle_status(self):
        """Returnera enhetens status."""
        status = {"paired": False, "wifi_connected": False}
        if self.provision_server:
            status["paired"] = self.provision_server.is_provisioned
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(status).encode())

    def _handle_provision(self):
        """Ta emot och applicera provisioning-data."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"success": False, "message": "Ogiltig data"})
            return

        ssid = data.get("ssid", "").strip()
        password = data.get("password", "")

        if not ssid:
            self._send_json(400, {"success": False, "message": "SSID saknas"})
            return

        logger.info(f"Provisioning-begäran: WiFi={ssid}")

        # Notifiera provision_server om data
        if self.provision_server:
            self.provision_server._received_data = data
            success, message = self.provision_server._apply_provision(data)
            self._send_json(
                200, {"success": success, "message": message}
            )
        else:
            self._send_json(
                500, {"success": False, "message": "Server ej redo"}
            )

    def _send_json(self, code: int, data: dict):
        """Skicka JSON-svar."""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


# ─── WiFi AP + Provisioning-server ────────────────────────────

class WiFiProvisionServer:
    """Startar WiFi AP-hotspot och captive portal för provisioning."""

    def __init__(self, config: dict):
        self.config = config
        self.ap_ssid = _get_ap_ssid()
        self._http_server: HTTPServer | None = None
        self._http_thread: Thread | None = None
        self._running = False
        self._received_data: dict | None = None
        self.is_provisioned = False

        # Callbacks (samma signatur som BLE-server)
        self._on_wifi_configured: Optional[Callable] = None
        self._on_auth_configured: Optional[Callable] = None

    def on_wifi_configured(self, callback: Callable):
        self._on_wifi_configured = callback

    def on_auth_configured(self, callback: Callable):
        self._on_auth_configured = callback

    async def start(self):
        """Starta AP-hotspot + HTTP-server."""
        self._running = True
        logger.info(f"Startar WiFi-provisioning: hotspot '{self.ap_ssid}'")

        # 1. Starta AP-hotspot via NetworkManager
        success = self._start_hotspot()
        if not success:
            logger.error("Kunde inte starta hotspot")
            return False

        # 2. Starta dnsmasq för captive portal redirect
        self._start_dns_redirect()

        # 3. Starta HTTP-server i separat tråd
        self._start_http_server()

        logger.info(f"Captive portal aktiv på http://{AP_IP}/")
        logger.info(f"Anslut till WiFi '{self.ap_ssid}' för att konfigurera")
        return True

    async def stop(self):
        """Stäng ner hotspot och HTTP-server."""
        self._running = False

        # Stoppa HTTP-server
        if self._http_server:
            self._http_server.shutdown()
            logger.info("HTTP-server stoppad")

        # Stoppa hotspot
        self._stop_hotspot()

        # Stoppa dnsmasq
        self._stop_dns_redirect()

        logger.info("WiFi-provisioning stoppad")

    def _start_hotspot(self) -> bool:
        """Starta WiFi AP via NetworkManager."""
        try:
            # Ta bort gammal hotspot-connection om den finns
            subprocess.run(
                ["nmcli", "connection", "delete", "NotePin-Hotspot"],
                capture_output=True, timeout=10,
            )

            # Skapa ny hotspot
            result = subprocess.run(
                [
                    "nmcli", "device", "wifi", "hotspot",
                    "ifname", AP_INTERFACE,
                    "con-name", "NotePin-Hotspot",
                    "ssid", self.ap_ssid,
                    "band", "bg",
                    "channel", "6",
                ],
                capture_output=True, text=True, timeout=15,
            )

            if result.returncode != 0:
                logger.error(f"Hotspot-startfel: {result.stderr}")
                return False

            # Sätt statisk IP
            subprocess.run(
                [
                    "nmcli", "connection", "modify", "NotePin-Hotspot",
                    "ipv4.addresses", f"{AP_IP}/24",
                    "ipv4.method", "shared",
                ],
                capture_output=True, timeout=10,
            )

            # Aktivera hotspot
            subprocess.run(
                ["nmcli", "connection", "up", "NotePin-Hotspot"],
                capture_output=True, timeout=15,
            )

            logger.info(f"Hotspot '{self.ap_ssid}' startad på {AP_IP}")
            return True

        except subprocess.TimeoutExpired:
            logger.error("Hotspot-start timeout")
            return False
        except Exception as e:
            logger.error(f"Hotspot-fel: {e}")
            return False

    def _stop_hotspot(self):
        """Stäng ner hotspot."""
        try:
            subprocess.run(
                ["nmcli", "connection", "down", "NotePin-Hotspot"],
                capture_output=True, timeout=10,
            )
            subprocess.run(
                ["nmcli", "connection", "delete", "NotePin-Hotspot"],
                capture_output=True, timeout=10,
            )
            logger.info("Hotspot nedstängd")
        except Exception as e:
            logger.warning(f"Hotspot-nedstängning: {e}")

    def _start_dns_redirect(self):
        """Starta DNS-redirect så alla domäner pekar till captive portal."""
        try:
            # Skriv dnsmasq-config för captive portal
            dnsmasq_conf = Path("/tmp/notepin-dnsmasq.conf")
            dnsmasq_conf.write_text(
                f"interface={AP_INTERFACE}\n"
                f"bind-interfaces\n"
                f"address=/#/{AP_IP}\n"
                f"no-resolv\n"
                f"log-queries\n"
            )

            # Stoppa eventuell existerande instans
            subprocess.run(
                ["pkill", "-f", "notepin-dnsmasq"],
                capture_output=True,
            )

            # Starta dnsmasq
            subprocess.Popen(
                [
                    "dnsmasq",
                    f"--conf-file={dnsmasq_conf}",
                    "--pid-file=/tmp/notepin-dnsmasq.pid",
                    "--no-daemon",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("DNS-redirect startad (captive portal)")

        except FileNotFoundError:
            logger.warning(
                "dnsmasq ej installerat — captive portal auto-detect "
                "fungerar inte, men http://192.168.4.1 fungerar ändå"
            )
        except Exception as e:
            logger.warning(f"DNS-redirect: {e}")

    def _stop_dns_redirect(self):
        """Stoppa DNS-redirect."""
        try:
            subprocess.run(
                ["pkill", "-f", "notepin-dnsmasq"],
                capture_output=True,
            )
        except Exception:
            pass

    def _start_http_server(self):
        """Starta HTTP-server i separat tråd."""
        ProvisionHandler.provision_server = self

        self._http_server = HTTPServer((AP_IP, 80), ProvisionHandler)
        self._http_thread = Thread(
            target=self._http_server.serve_forever,
            daemon=True,
        )
        self._http_thread.start()
        logger.info(f"HTTP-server startad på http://{AP_IP}:80")

    def _apply_provision(self, data: dict) -> tuple[bool, str]:
        """Applicera provisioning-data (WiFi + auth).

        Returns:
            (success: bool, message: str)
        """
        ssid = data.get("ssid", "").strip()
        password = data.get("password", "")

        # --- Steg 1: Spara auth-credentials ---
        user_id = data.get("user_id", "").strip()
        refresh_token = data.get("refresh_token", "").strip()
        supabase_url = data.get("supabase_url", "").strip()
        anon_key = data.get("anon_key", "").strip()
        pairing_code = data.get("pairing_code", "").strip()

        # Om pairing_code finns, dekoda den (base64-kodad JSON)
        if pairing_code and not user_id:
            try:
                import base64
                decoded = json.loads(base64.b64decode(pairing_code))
                user_id = decoded.get("user_id", "")
                refresh_token = decoded.get("refresh_token", "")
                supabase_url = decoded.get("supabase_url", "")
                anon_key = decoded.get("anon_key", "")
            except Exception as e:
                logger.warning(f"Ogiltig parkopplingskod: {e}")

        if user_id and refresh_token:
            creds = self._load_credentials()
            creds.update({
                "user_id": user_id,
                "refresh_token": refresh_token,
            })
            if supabase_url:
                creds["supabase_url"] = supabase_url
            if anon_key:
                creds["anon_key"] = anon_key

            # Generera device_id
            serial = get_device_serial()
            creds["device_id"] = serial

            self._save_credentials(creds)
            logger.info(f"Auth-credentials sparade för user: {user_id[:8]}...")

            if self._on_auth_configured:
                self._on_auth_configured(user_id)

        # --- Steg 2: Stäng hotspot och anslut till WiFi ---
        logger.info(f"Stänger hotspot och ansluter till '{ssid}'...")

        # Stoppa DNS-redirect
        self._stop_dns_redirect()

        # Stoppa hotspot
        self._stop_hotspot()

        # Kort paus så nätverksgränssnittet hinner återställas
        import time
        time.sleep(2)

        # Anslut till WiFi
        try:
            # Ta bort eventuell gammal anslutning
            subprocess.run(
                ["nmcli", "connection", "delete", ssid],
                capture_output=True, timeout=10,
            )

            result = subprocess.run(
                [
                    "nmcli", "device", "wifi", "connect", ssid,
                    "password", password,
                    "ifname", AP_INTERFACE,
                ],
                capture_output=True, text=True, timeout=30,
            )

            if result.returncode == 0:
                logger.info(f"WiFi anslutet: {ssid}")

                # Spara WiFi-info
                creds = self._load_credentials()
                creds["wifi_ssid"] = ssid
                self._save_credentials(creds)

                self.is_provisioned = True

                if self._on_wifi_configured:
                    self._on_wifi_configured(ssid)

                return True, f"Ansluten till {ssid}! NotePin är redo."

            else:
                error = result.stderr.strip()
                logger.error(f"WiFi-anslutning misslyckades: {error}")

                # Starta om hotspot så användaren kan försöka igen
                self._start_hotspot()
                self._start_dns_redirect()

                return False, f"Kunde inte ansluta till {ssid}. Kontrollera lösenordet."

        except subprocess.TimeoutExpired:
            logger.error("WiFi-anslutning timeout")
            self._start_hotspot()
            self._start_dns_redirect()
            return False, "Timeout vid WiFi-anslutning. Försök igen."

        except Exception as e:
            logger.error(f"WiFi-anslutningsfel: {e}")
            self._start_hotspot()
            self._start_dns_redirect()
            return False, f"Anslutningsfel: {e}"

    def _load_credentials(self) -> dict:
        """Ladda sparade credentials."""
        creds_file = Path.home() / ".notepin_credentials.json"
        if creds_file.exists():
            try:
                with open(creds_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def _save_credentials(self, creds: dict):
        """Spara credentials."""
        creds_file = Path.home() / ".notepin_credentials.json"
        with open(creds_file, "w") as f:
            json.dump(creds, f, indent=2)
        creds_file.chmod(0o600)
