#!/usr/bin/env python3
"""
Litmus Signage – Raspberry Pi Agent
====================================
Copy this file to the Raspberry Pi and run:

    python3 rpi_agent.py --dashboard http://<dashboard-ip>:5000

The agent will:
  1. Self-install any missing dependencies (apt + pip).
  2. Register this Pi with the dashboard (auto-onboard).
  3. Send a heartbeat + system stats every 30 s.
  4. Poll for the assigned playlist; reload Chromium if it changes.
  5. Execute queued CEC commands (TV on/off) and reboot requests.
"""

import subprocess
import sys
import os


# ---------------------------------------------------------------------------
# Bootstrap: install missing dependencies before anything else imports them
# ---------------------------------------------------------------------------

def _apt_install(*packages: str):
    """Install apt packages silently if not already present."""
    missing = []
    for pkg in packages:
        result = subprocess.run(
            ["dpkg", "-s", pkg],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            missing.append(pkg)
    if missing:
        print(f"[bootstrap] Installing via apt: {' '.join(missing)}")
        subprocess.run(
            ["sudo", "apt-get", "install", "-y", "--no-install-recommends"] + missing,
            check=True,
        )


def _pip_install(*packages: str):
    """Install pip packages using --break-system-packages for Bookworm compatibility."""
    missing = []
    for pkg in packages:
        try:
            __import__(pkg.split("[")[0].replace("-", "_"))
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[bootstrap] Installing via pip: {' '.join(missing)}")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--break-system-packages"] + missing,
            check=True,
        )


def bootstrap():
    # Chromium: try both package names (varies by OS version)
    chromium_pkg = "chromium-browser"
    check = subprocess.run(["which", "chromium-browser"], capture_output=True)
    if check.returncode != 0:
        check2 = subprocess.run(["which", "chromium"], capture_output=True)
        if check2.returncode != 0:
            # Try chromium-browser first, fall back to chromium
            try:
                _apt_install("chromium-browser")
            except subprocess.CalledProcessError:
                _apt_install("chromium")

    _apt_install("cec-utils")
    _pip_install("requests", "psutil")
    print("[bootstrap] All dependencies satisfied.")


bootstrap()

# ---------------------------------------------------------------------------
# Now safe to import everything
# ---------------------------------------------------------------------------

import argparse
import json
import logging
import platform
import socket
import time
import uuid
from pathlib import Path

import requests
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AGENT_VERSION = "1.0.0"
HEARTBEAT_INTERVAL = 30        # seconds between heartbeats / config polls
CONFIG_DIR = Path.home() / ".signage"
CONFIG_FILE = CONFIG_DIR / "agent.json"
LOG_FILE = CONFIG_DIR / "agent.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_mac() -> str:
    """Return the primary network interface MAC address."""
    mac = uuid.getnode()
    return ":".join(f"{(mac >> (8 * i)) & 0xff:02x}" for i in reversed(range(6)))


def get_ip() -> str:
    """Best-effort local IP address detection."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_stats() -> dict:
    """Collect system stats; gracefully degrade if psutil unavailable."""
    stats: dict = {}
    if not HAS_PSUTIL:
        return stats
    try:
        stats["cpu_percent"] = psutil.cpu_percent(interval=0.5)
    except Exception:
        pass
    try:
        vm = psutil.virtual_memory()
        stats["ram_percent"] = vm.percent
        stats["ram_used_gb"] = round(vm.used / (1024 ** 3), 2)
        stats["ram_total_gb"] = round(vm.total / (1024 ** 3), 2)
    except Exception:
        pass
    try:
        disk = psutil.disk_usage("/")
        stats["disk_percent"] = disk.percent
    except Exception:
        pass
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for name, entries in temps.items():
                if name.lower() in ("cpu_thermal", "cpu-thermal", "cpu", "coretemp", "bcm2835"):
                    if entries:
                        stats["temp_c"] = round(entries[0].current, 1)
                        break
            if "temp_c" not in stats:
                first = next(iter(temps.values()), [])
                if first:
                    stats["temp_c"] = round(first[0].current, 1)
    except Exception:
        pass
    try:
        stats["uptime_hours"] = round(
            (time.time() - psutil.boot_time()) / 3600, 1
        )
    except Exception:
        pass
    return stats


def get_hdmi_status() -> str:
    """Check if an HDMI display is physically connected.
    Returns 'connected', 'disconnected', or 'unknown'.
    """
    # Method 1: /sys/class/drm — works on all Bookworm / kernel 6.x RPi OS
    try:
        for p in Path("/sys/class/drm").glob("card*-HDMI-*/status"):
            status = p.read_text().strip().lower()
            if status == "connected":
                return "connected"
            if status == "disconnected":
                return "disconnected"
    except Exception:
        pass
    # Method 2: tvservice (older RPi OS / legacy kernel)
    try:
        r = subprocess.run(["tvservice", "-s"], capture_output=True, text=True, timeout=3)
        out = r.stdout.lower()
        if "0x120002" in out or "0x12001a" in out or ("hdmi" in out and "state" in out):
            return "disconnected" if ("off" in out or "no signal" in out) else "connected"
    except Exception:
        pass
    return "unknown"


def get_tv_power() -> str:
    """Query TV power state via CEC.
    Returns 'on', 'standby', 'not_present', or 'unknown'.
    Note: TV must have CEC enabled (Anynet+/EasyLink/SimpLink/BRAVIA Sync).
    """
    try:
        result = subprocess.run(
            ["cec-client", "-s"],
            input="pow 0\n",
            capture_output=True,
            text=True,
            timeout=8,
        )
        output = result.stdout.lower()
        if "power status: on" in output:
            return "on"
        if "power status: standby" in output or "power status: in standby" in output:
            return "standby"
        if "not present" in output or "marked as not present" in output:
            return "not_present"  # TV present on HDMI but CEC disabled/not responding
    except Exception:
        pass
    return "unknown"


def cec_tv_on():
    try:
        subprocess.run(
            ["cec-client", "-s"],
            input="on 0\n",
            capture_output=True,
            text=True,
            timeout=8,
        )
        logger.info("CEC: TV on command sent")
    except Exception as e:
        logger.warning(f"CEC tv_on failed: {e}")


def cec_tv_off():
    try:
        subprocess.run(
            ["cec-client", "-s"],
            input="standby 0\n",
            capture_output=True,
            text=True,
            timeout=8,
        )
        logger.info("CEC: TV standby command sent")
    except Exception as e:
        logger.warning(f"CEC tv_off failed: {e}")


def save_local_config(data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data))


def load_local_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Chromium kiosk management
# ---------------------------------------------------------------------------

_chromium_proc: "subprocess.Popen | None" = None
_chromium_url: str = ""


def launch_chromium(url: str):
    global _chromium_proc, _chromium_url
    if _chromium_proc and _chromium_proc.poll() is None:
        if _chromium_url == url:
            return  # already showing correct content
        logger.info(f"Playlist changed, restarting Chromium → {url}")
        _chromium_proc.terminate()
        time.sleep(1)

    _chromium_url = url

    # Detect the correct binary name (varies by RPi OS version)
    browser_bin = None
    for candidate in ("chromium-browser", "chromium"):
        r = subprocess.run(["which", candidate], capture_output=True)
        if r.returncode == 0:
            browser_bin = candidate
            break

    if not browser_bin:
        logger.warning("Chromium not found. Install with: sudo apt install -y chromium")
        return

    cmd = [
        browser_bin,
        "--kiosk",
        "--noerrdialogs",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--disable-features=TranslateUI",
        "--check-for-update-interval=31536000",
        url,
    ]
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    try:
        _chromium_proc = subprocess.Popen(cmd, env=env)
        logger.info(f"Chromium ({browser_bin}) launched (pid {_chromium_proc.pid}) \u2192 {url}")
    except Exception as e:
        logger.warning(f"Chromium launch failed: {e}")


# ---------------------------------------------------------------------------
# Dashboard API calls
# ---------------------------------------------------------------------------

class DashboardClient:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.timeout = 10

    def register(self, name: str, hostname: str, mac: str, ip: str) -> str | None:
        """Register this RPi. Returns client_id or None on failure."""
        try:
            r = self.session.post(
                f"{self.base}/api/clients/register",
                json={
                    "name": name,
                    "hostname": hostname,
                    "mac": mac,
                    "ip": ip,
                    "agent_version": AGENT_VERSION,
                },
            )
            data = r.json()
            if data.get("ok"):
                return data["client_id"]
            logger.error(f"Register failed: {data.get('error')}")
        except Exception as e:
            logger.warning(f"Register error: {e}")
        return None

    def heartbeat(self, client_id: str, stats: dict) -> dict:
        """Send heartbeat + stats. Returns response dict (may contain pending_command)."""
        try:
            r = self.session.post(
                f"{self.base}/api/clients/{client_id}/heartbeat",
                json={"ip": get_ip(), "stats": stats},
            )
            return r.json()
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")
        return {}

    def get_config(self, client_id: str) -> dict:
        """Fetch assigned playlist config from dashboard."""
        try:
            r = self.session.get(f"{self.base}/api/clients/{client_id}/config")
            return r.json()
        except Exception as e:
            logger.warning(f"Config fetch error: {e}")
        return {}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def build_player_url(base_url: str, playlist_id: str | None) -> str:
    """Build the web player URL for the assigned playlist."""
    url = base_url.rstrip("/")
    # Replace dashboard port 5000 with web-player port 8080
    url = url.replace(":5000", ":8080")
    if playlist_id:
        return f"{url}/player?playlist_id={playlist_id}"
    return f"{url}/player"


def run(args):
    dashboard_url: str = args.dashboard
    client_name: str = args.name or socket.gethostname()

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    local = load_local_config()

    mac = get_mac()
    hostname = socket.gethostname()
    ip = get_ip()

    api = DashboardClient(dashboard_url)

    # ---- Register ----
    client_id = local.get("client_id")
    if not client_id:
        logger.info(f"Registering with dashboard at {dashboard_url} …")
        client_id = api.register(client_name, hostname, mac, ip)
        if client_id:
            local["client_id"] = client_id
            local["dashboard_url"] = dashboard_url
            local["name"] = client_name
            save_local_config(local)
            logger.info(f"Registered! client_id = {client_id}")
        else:
            logger.error("Could not register with dashboard. Will retry.")
    else:
        logger.info(f"Using stored client_id = {client_id}")
        # Re-register to refresh IP / hostname
        api.register(client_name, hostname, mac, ip)

    last_playlist_id: str | None = None
    last_cec_check: float = 0
    CEC_CHECK_INTERVAL = 120  # only poll CEC every 2 minutes — it can block /dev/cec0

    while True:
        try:
            logger.info("Heartbeat cycle starting…")

            # ---- 1. Fast stats (never blocks) ----
            stats = get_stats()
            try:
                stats["hdmi_connected"] = get_hdmi_status()
            except Exception:
                pass

            # ---- 2. Heartbeat FIRST — so commands are received even if CEC is slow ----
            if client_id:
                hb = api.heartbeat(client_id, stats)
                pending = hb.get("pending_command")
                if pending:
                    logger.info(f"Executing command: {pending}")
                    if pending == "tv_on":
                        cec_tv_on()
                    elif pending == "tv_off":
                        cec_tv_off()
                    elif pending == "reboot":
                        logger.info("Rebooting in 3 s…")
                        time.sleep(3)
                        os.system("sudo reboot")
                else:
                    logger.info("Heartbeat sent OK, no pending command")
            else:
                # Retry registration
                client_id = api.register(client_name, hostname, mac, ip)
                if client_id:
                    local["client_id"] = client_id
                    save_local_config(local)

            # ---- 3. Config / playlist poll ----
            if client_id:
                cfg = api.get_config(client_id)
                assigned_pid = cfg.get("assigned_playlist_id")
                if assigned_pid != last_playlist_id:
                    logger.info(
                        f"Playlist changed: {last_playlist_id!r} → {assigned_pid!r}"
                    )
                    last_playlist_id = assigned_pid
                    player_url = build_player_url(dashboard_url, assigned_pid)
                    launch_chromium(player_url)

            # ---- 4. CEC TV power — only every 2 min, AFTER heartbeat ----
            now = time.time()
            if now - last_cec_check >= CEC_CHECK_INTERVAL:
                last_cec_check = now
                try:
                    tv_power = get_tv_power()
                    stats["tv_power"] = tv_power
                    logger.info(f"CEC TV power status: {tv_power}")
                    # Send updated stats with TV power via another heartbeat
                    if client_id:
                        api.heartbeat(client_id, stats)
                except Exception as e:
                    logger.warning(f"CEC check failed: {e}")

        except Exception:
            logger.exception("Unexpected error in main loop")

        time.sleep(HEARTBEAT_INTERVAL)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Litmus Signage RPi Agent")
    parser.add_argument(
        "--dashboard",
        required=True,
        help="Dashboard base URL, e.g. http://192.168.1.100:5000",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Human-readable name for this display (default: hostname)",
    )
    args = parser.parse_args()
    # Auto-fix missing scheme so bare IPs/hostnames still work
    if not args.dashboard.startswith(("http://", "https://")):
        args.dashboard = "http://" + args.dashboard
    logger.info(f"Litmus RPi Agent v{AGENT_VERSION} starting")
    logger.info(f"Dashboard: {args.dashboard}")
    run(args)
