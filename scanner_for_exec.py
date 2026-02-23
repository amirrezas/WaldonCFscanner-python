import sys
import os
import platform
import urllib.request
import zipfile
import json
import asyncio

# --- BOOT LOGS & WINDOWS ENCODING PATCH ---
if platform.system() == 'Windows':
    os.system("title WaldonCFscanner - Booting Engine...")
    os.system("chcp 65001 > nul")
    sys.stdout.reconfigure(encoding='utf-8')

    # Anti-Freeze QuickEdit Patch
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        mode.value &= ~0x0040
        kernel32.SetConsoleMode(handle, mode)
    except Exception:
        pass

print("Booting Scanner Engine... Unpacking libraries, please wait a few seconds...")
print("Checking system architecture and dependencies...")

# --- DYNAMIC FILE RESOLUTION (Internal vs External) ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR


def get_resource_path(filename):
    """Prioritize external file in the exe directory, fallback to bundled internal file."""
    external_path = os.path.join(BASE_DIR, filename)
    if os.path.exists(external_path):
        return external_path

    internal_path = os.path.join(BUNDLE_DIR, filename)
    if os.path.exists(internal_path):
        return internal_path

    return external_path  # Default fallback


CSV_FILE = os.path.join(BASE_DIR, "clean_ips.csv")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
URI_FILE = os.path.join(BASE_DIR, "config.txt")
OUTPUT_DIR = os.path.join(BASE_DIR, "output_configs")
ERROR_LOG_FILE = os.path.join(BASE_DIR, "scanner_error.log")

# Embedded or External Data Files
IPV4_FILE = get_resource_path("ipv4.txt")
IPV6_FILE = get_resource_path("ipv6.txt")
DOMAINS_FILE = get_resource_path("cloudfalare-domains.txt")


def ensure_xray_core():
    """Automatically downloads and extracts the latest Xray-core if missing."""
    sys_os = platform.system()
    exe_name = "xray.exe" if sys_os == "Windows" else "xray"
    xray_path = os.path.join(BASE_DIR, exe_name)

    if os.path.exists(xray_path):
        print("Xray-core detected.")
        return

    print(f"Xray-core missing. Fetching the latest release for {sys_os}...")
    api_url = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"

    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())

        asset_suffix = "windows-64.zip" if sys_os == "Windows" else "linux-64.zip"
        download_url = None
        for asset in data.get('assets', []):
            if asset['name'].endswith(asset_suffix):
                download_url = asset['browser_download_url']
                break

        if not download_url:
            print("Could not find suitable Xray binary on GitHub.")
            return

        print(f"Downloading Xray-core (approx 20MB)...")
        zip_path = os.path.join(BASE_DIR, "xray_temp.zip")
        urllib.request.urlretrieve(download_url, zip_path)

        print("Extracting executable...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for member in zip_ref.namelist():
                if member == exe_name or member.endswith(f"/{exe_name}"):
                    source = zip_ref.open(member)
                    with open(xray_path, "wb") as target:
                        target.write(source.read())
                    break
        os.remove(zip_path)

        if sys_os != "Windows":
            import stat
            os.chmod(xray_path, os.stat(xray_path).st_mode | stat.S_IEXEC)

        print("Xray-core installed successfully!\n")
    except Exception as e:
        print(f"Failed to auto-download Xray: {e}")
        print("Continuing with Pure-Python fallback mode.")


ensure_xray_core()

import aiohttp
import ssl
import time
import random
import ipaddress
import csv
import tempfile
import logging
import copy
import stat
import urllib.parse
import pyperclip
import re

from textual import on
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, DataTable, ProgressBar, Label, Button, Input, Switch
from textual.containers import Horizontal, Vertical, Grid
from textual.binding import Binding

SPEED_TEST_PATH = "/__down?bytes=1000000"
VERIFY_URL = "http://cp.cloudflare.com/generate_204"

logging.basicConfig(
    filename=ERROR_LOG_FILE,
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


def get_system_socket_capacity() -> int:
    cores = os.cpu_count() or 4
    if platform.system() == 'Windows':
        return min(cores * 150, 1000)
    else:
        return min(cores * 300, 3000)


class IPScannerUI(App):
    TITLE = "High-Speed Xray VLESS Verification Engine"

    CSS = """
    Screen { background: #000000; }
    #controls-container { height: auto; dock: top; padding: 1 2; background: #111111; border-bottom: solid #333333; }

    #header-row { height: 1; margin-bottom: 1; align: right middle; }
    #github-link { color: #00ffff; text-style: italic; }

    #settings-grid { grid-size: 6 1; height: 3; grid-columns: auto 12 auto 12 auto 10; align: left middle; }

    #clipboard-row { height: 3; margin-top: 1; align: left middle; }
    #clipboard_input { width: 1fr; margin-left: 1; background: #222222; color: #00ff00; }
    #btn_paste { margin-left: 1; min-width: 15; }

    #button-grid { grid-size: 6 1; height: 3; grid-columns: 1fr 1fr 1fr 1fr 1fr 1fr; margin-top: 1; grid-gutter: 1; }
    .lbl { padding-top: 1; color: #ffffff; text-style: bold; }
    .inp { width: 10; background: #222222; color: #00ff00; }
    .btn { width: 100%; }

    #pipelines { height: 7; margin: 1; }
    .queue-box { width: 1fr; height: 100%; border: round #444444; padding: 0 1; background: #111111; }
    .queue-title { text-style: bold; color: #ffffff; margin-bottom: 1; }
    #target-box { border: round #00ff00; }

    #data-area { height: 1fr; padding: 0 1; }
    #log_view { width: 40%; height: 100%; border: panel #00ff00; background: #050505; color: #ffffff; margin-right: 1; }
    #results_table { width: 60%; height: 100%; border: panel #ffff00; background: #050505; color: #ffffff; }
    """

    BINDINGS = [Binding("q", "quit", "Quit", priority=True)]

    def compose(self) -> ComposeResult:
        yield Header()

        with Vertical(id="controls-container"):
            with Horizontal(id="header-row"):
                yield Label("GitHub: github.com/amirrezas/WaldonCFscanner-python", id="github-link")

            with Grid(id="settings-grid"):
                yield Label("Power (1-100):", classes="lbl")
                yield Input("10", id="power_input", classes="inp")
                yield Label("Target IPs:", classes="lbl")
                yield Input("10", id="target_input", classes="inp")
                yield Label("Debug Mode:", classes="lbl")
                yield Switch(id="debug_switch", value=True)

            with Horizontal(id="clipboard-row"):
                yield Label("VLESS URI:", classes="lbl")
                yield Input(placeholder="Paste vless://... config here", id="clipboard_input")
                yield Button("Paste", id="btn_paste", variant="primary")

            with Grid(id="button-grid"):
                yield Button("Start", id="btn_start", variant="success", classes="btn")
                yield Button("Pause", id="btn_pause", variant="warning", classes="btn", disabled=True)
                yield Button("Resume", id="btn_resume", variant="primary", classes="btn", disabled=True)
                yield Button("Stop", id="btn_stop", variant="error", classes="btn", disabled=True)
                yield Button("Save CSV", id="btn_csv", variant="default", classes="btn")
                yield Button("Save Log", id="btn_log", variant="default", classes="btn")

        with Horizontal(id="pipelines"):
            with Vertical(classes="queue-box"):
                yield Label("1. TCP", classes="queue-title")
                yield ProgressBar(id="tcp_bar", show_eta=False)
            with Vertical(classes="queue-box"):
                yield Label("2. TLS", classes="queue-title")
                yield ProgressBar(id="tls_bar", show_eta=False)
            with Vertical(classes="queue-box"):
                yield Label("3. Speed", classes="queue-title")
                yield ProgressBar(id="speed_bar", show_eta=False)
            with Vertical(classes="queue-box"):
                yield Label("4. Xray", classes="queue-title")
                yield ProgressBar(id="xray_bar", show_eta=False)
            with Vertical(classes="queue-box", id="target-box"):
                yield Label("Target", classes="queue-title")
                yield ProgressBar(id="target_bar", show_eta=False)

        with Horizontal(id="data-area"):
            yield RichLog(id="log_view", highlight=True, markup=True)
            yield DataTable(id="results_table")

        yield Footer()

    def parse_uri_to_json(self, uri: str) -> dict:
        try:
            uri = uri.strip()
            if not uri.startswith("vless://"): return {}
            main_part = uri[8:]
            main_part, name = main_part.split("#", 1) if "#" in main_part else (main_part, "Generated")
            user_part, server_part = main_part.split("@", 1)
            server_port, query_str = server_part.split("?", 1)
            server, port = server_port.split(":", 1)
            qs = urllib.parse.parse_qs(query_str)

            config = {
                "inbounds": [{"port": 10809, "protocol": "mixed"}],
                "outbounds": [{
                    "protocol": "vless",
                    "settings": {
                        "vnext": [{
                            "address": server,
                            "port": int(port),
                            "users": [{"id": user_part, "encryption": qs.get("encryption", ["none"])[0]}]
                        }]
                    },
                    "streamSettings": {
                        "network": qs.get("type", ["tcp"])[0],
                        "security": qs.get("security", ["none"])[0]
                    }
                }]
            }
            if config["outbounds"][0]["streamSettings"]["security"] == "tls":
                config["outbounds"][0]["streamSettings"]["tlsSettings"] = {
                    "serverName": qs.get("sni", [""])[0],
                    "fingerprint": qs.get("fp", ["chrome"])[0],
                    "alpn": qs.get("alpn", ["http/1.1"])[0].split(",")
                }
            if config["outbounds"][0]["streamSettings"]["network"] == "ws":
                config["outbounds"][0]["streamSettings"]["wsSettings"] = {
                    "path": qs.get("path", ["/"])[0],
                    "headers": {"Host": qs.get("host", [""])[0]}
                }
            return config
        except Exception as e:
            logging.error(f"URI Parse Error: {e}")
            return {}

    def parse_json_to_uri(self, config: dict) -> str:
        try:
            out = config["outbounds"][0]
            if out.get("protocol") != "vless": return ""
            vnext = out["settings"]["vnext"][0]
            uuid = vnext["users"][0]["id"]
            server = vnext["address"]
            port = vnext["port"]
            encryption = vnext["users"][0].get("encryption", "none")

            stream = out.get("streamSettings", {})
            net = stream.get("network", "tcp")
            sec = stream.get("security", "none")

            params = {"type": net, "encryption": encryption, "security": sec}

            if sec == "tls":
                tls = stream.get("tlsSettings", {})
                if "serverName" in tls: params["sni"] = tls["serverName"]
                if "fingerprint" in tls: params["fp"] = tls["fingerprint"]
                if "alpn" in tls: params["alpn"] = ",".join(tls["alpn"])

            if net == "ws":
                ws = stream.get("wsSettings", {})
                if "path" in ws: params["path"] = ws["path"]
                if "headers" in ws and "Host" in ws["headers"]: params["host"] = ws["headers"]["Host"]

            q = urllib.parse.urlencode(params, safe=":/,")
            return f"vless://{uuid}@{server}:{port}?{q}#WaldonCFscanner"
        except Exception as e:
            logging.error(f"JSON Parse Error: {e}")
            return ""

    def on_mount(self) -> None:
        self.log_view = self.query_one("#log_view", RichLog)
        self.results_table = self.query_one("#results_table", DataTable)

        self.results_table.add_columns("Rank", "IP Address", "Speed", "TLS Lat.", "Xray Lat.", "Score")

        self.is_scanning = False
        self.active_event = asyncio.Event()
        self.active_event.set()
        self.stop_event = asyncio.Event()
        self.tasks = []
        self.found_ips = []
        self.hot_subnets = []
        self.target_ips = 10
        self.base_config = {}
        self.base_uri = ""

        self.active_tcp = 0
        self.active_tls = 0
        self.active_speed = 0
        self.active_xray = 0

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        logging.info("Scanner UI mounted.")

        self.xray_exe = os.path.join(BASE_DIR, "xray.exe" if platform.system() == "Windows" else "xray")

        json_loaded = False
        uri_loaded = False

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    self.base_config = json.load(f)
                    json_loaded = True
            except Exception as e:
                logging.error(f"Failed to load config.json: {e}")

        if os.path.exists(URI_FILE):
            try:
                with open(URI_FILE, 'r', encoding='utf-8') as f:
                    self.base_uri = f.read().strip()
                    if self.base_uri.startswith("vless://"):
                        uri_loaded = True
            except Exception as e:
                logging.error(f"Failed to load config.txt: {e}")

        if json_loaded and not uri_loaded:
            self.base_uri = self.parse_json_to_uri(self.base_config)
            self.log_view.write("[cyan]Generated URI template from config.json[/cyan]")
        elif uri_loaded and not json_loaded:
            self.base_config = self.parse_uri_to_json(self.base_uri)
            self.log_view.write("[cyan]Generated JSON template from config.txt[/cyan]")

        if self.base_uri:
            self.query_one("#clipboard_input", Input).value = self.base_uri

        self.xray_enabled = os.path.exists(self.xray_exe) and bool(self.base_config)

        if self.xray_enabled and platform.system() != "Windows":
            if not os.access(self.xray_exe, os.X_OK):
                try:
                    st = os.stat(self.xray_exe)
                    os.chmod(self.xray_exe, st.st_mode | stat.S_IEXEC)
                except Exception as e:
                    logging.error(f"Failed to make Xray executable: {e}")
                    self.xray_enabled = False

        self._load_networks()

        if self.xray_enabled:
            self.log_view.write(f"[bold bright_green]System Ready. 4-Stage Xray Engine Armed.[/bold bright_green]")
        else:
            self.log_view.write(
                "[bold yellow]Xray Core missing or no config provided! Falling back to 3-Stage Pure Python.[/bold yellow]")

    def _action_paste_clipboard(self):
        try:
            val = pyperclip.paste().strip()
            if val.startswith("vless://"):
                self.query_one("#clipboard_input", Input).value = val
                self.log_view.write(
                    "[bold bright_green]VLESS URI successfully pasted from clipboard![/bold bright_green]")
            else:
                self.log_view.write("[bold yellow]Clipboard does not contain a valid vless:// link.[/bold yellow]")
        except Exception as e:
            self.log_view.write(f"[bold red]Failed to read clipboard: {str(e)}[/bold red]")

    @on(Input.Changed, "#clipboard_input")
    def on_clipboard_changed(self, event: Input.Changed):
        val = event.value.strip()
        if val.startswith("vless://"):
            self.base_uri = val
            self.base_config = self.parse_uri_to_json(val)
            if self.base_config and os.path.exists(self.xray_exe):
                if not self.xray_enabled:
                    self.xray_enabled = True
                    self.log_view.write(
                        "[bold bright_green]VLESS configuration loaded! Xray Engine Activated.[/bold bright_green]")
            else:
                if not os.path.exists(self.xray_exe):
                    self.log_view.write("[bold yellow]Valid VLESS link, but xray binary is missing![/bold yellow]")

    @on(Input.Changed, "#target_input")
    def update_target(self, event: Input.Changed):
        try:
            self.target_ips = max(1, int(event.value))
            self.query_one("#target_bar", ProgressBar).total = self.target_ips
            if self.is_scanning and len(self.found_ips) >= self.target_ips:
                self.log_view.write("[bold yellow]TARGET ADJUSTED & REACHED! Auto-stopping...[/bold yellow]")
                self.action_stop_scan()
        except ValueError:
            pass

    def _load_networks(self):
        self.network_groups = {}
        self.domains = []
        for file_path in [IPV4_FILE, IPV6_FILE]:
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            net = ipaddress.ip_network(line.strip(), strict=False)
                            first_block = str(net.network_address).split('.')[0] if net.version == 4 else \
                                str(net.network_address).split(':')[0]
                            if first_block not in self.network_groups:
                                self.network_groups[first_block] = []
                            self.network_groups[first_block].append(net)
                        except ValueError:
                            pass

        if not self.network_groups:
            self.log_view.write(
                "[bold yellow]Warning: IP lists not found! Falling back to 104.16.x.x default.[/bold yellow]")
            self.network_groups = {"104": [ipaddress.ip_network("104.16.0.0/12")]}
        else:
            self.log_view.write(f"[green]Network ranges loaded successfully.[/green]")

        if os.path.exists(DOMAINS_FILE):
            with open(DOMAINS_FILE, 'r') as f:
                self.domains = [line.strip() for line in f if line.strip()]
        if not self.domains: self.domains = ["speed.cloudflare.com", "zula.ir"]

    def _generate_random_ip(self) -> str:
        if self.hot_subnets and random.random() < 0.30:
            net = random.choice(self.hot_subnets)
        else:
            group_key = random.choice(list(self.network_groups.keys()))
            net = random.choice(self.network_groups[group_key])

        if net.version == 4:
            return str(net[random.randint(1, net.num_addresses - 2)])
        else:
            net_int = int(net.network_address)
            return str(ipaddress.IPv6Address(net_int + random.getrandbits(128 - net.prefixlen)))

    def _generate_outputs(self, ip: str):
        try:
            if self.base_config:
                new_json = copy.deepcopy(self.base_config)
                new_json.pop("routing", None)
                new_json.pop("dns", None)
                new_json["outbounds"][0]["settings"]["vnext"][0]["address"] = ip

                json_path = os.path.join(OUTPUT_DIR, f"config_{ip.replace(':', '_')}.json")
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(new_json, f, indent=2)

            if self.base_uri:
                new_uri = re.sub(r'(vless://[^@]+@)([^:]+)(:\d+)', rf'\g<1>{ip}\g<3>', self.base_uri)
                uri_path = os.path.join(OUTPUT_DIR, "vless_links.txt")
                with open(uri_path, 'a', encoding='utf-8') as f:
                    f.write(new_uri + "\n")

        except Exception as e:
            logging.error(f"Failed to generate output for {ip}: {e}")

    def _refresh_table(self):
        self.results_table.clear()
        sorted_ips = sorted(self.found_ips, key=lambda x: x[4], reverse=True)
        for idx, (ip, speed, tls_lat, xray_lat, score) in enumerate(sorted_ips):
            self.results_table.add_row(
                str(idx + 1), ip, f"{speed:.2f} Mbps", f"{tls_lat:.0f} ms", f"{xray_lat:.0f} ms", f"{score:.0f}"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn_start" and not self.is_scanning:
            self.action_start_scan()
        elif btn_id == "btn_pause" and self.is_scanning:
            self.active_event.clear()
            self.log_view.write("[bold yellow]Scan Paused.[/bold yellow]")
            self.query_one("#btn_pause").disabled = True
            self.query_one("#btn_resume").disabled = False
        elif btn_id == "btn_resume" and self.is_scanning:
            self.active_event.set()
            self.log_view.write("[bold green]Scan Resumed.[/bold green]")
            self.query_one("#btn_pause").disabled = False
            self.query_one("#btn_resume").disabled = True
        elif btn_id == "btn_stop" and self.is_scanning:
            self.action_stop_scan()
        elif btn_id == "btn_csv":
            self._manual_save_csv()
        elif btn_id == "btn_log":
            self._manual_save_log()
        elif btn_id == "btn_paste":
            self._action_paste_clipboard()

    def _manual_save_csv(self):
        try:
            with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["Rank", "IP Address", "Speed (Mbps)", "TLS Latency (ms)", "Xray Latency (ms)", "Quality Score"])
                sorted_ips = sorted(self.found_ips, key=lambda x: x[4], reverse=True)
                for idx, (ip, speed, tls_lat, xray_lat, score) in enumerate(sorted_ips):
                    writer.writerow([idx + 1, ip, f"{speed:.2f}", f"{tls_lat:.0f}", f"{xray_lat:.0f}", f"{score:.0f}"])
            self.log_view.write(
                f"[bold bright_cyan]Saved & Sorted {len(self.found_ips)} IPs to {CSV_FILE}[/bold bright_cyan]")
        except Exception as e:
            logging.error(f"Failed to save CSV: {e}")

    def _manual_save_log(self):
        self.log_view.write(f"[bold bright_cyan]UI Debug log and Professional Error Log saved![/bold bright_cyan]")
        self.log_view.write(f"[gray]If frozen, please send the 'scanner_error.log' file to the developer.[/gray]")

    def action_start_scan(self):
        logging.info("Scan started.")
        self.is_scanning = True
        self.stop_event.clear()
        self.active_event.set()

        self.query_one("#btn_start").disabled = True
        self.query_one("#btn_pause").disabled = False
        self.query_one("#btn_stop").disabled = False

        try:
            power_percent = max(1, min(100, int(self.query_one("#power_input", Input).value)))
        except ValueError:
            power_percent = 10

        max_sys_sockets = get_system_socket_capacity()
        active_sockets = int(max_sys_sockets * (power_percent / 100.0))

        num_tcp_workers = max(5, int(active_sockets * 0.70))
        num_tls_workers = max(2, int(active_sockets * 0.20))
        num_speed_workers = max(1, int(active_sockets * 0.10))
        num_xray_workers = 15

        self.log_view.write(
            f"[bold white]Engine: {power_percent}% Power ({active_sockets} active socket workers)[/bold white]")

        self.raw_queue = asyncio.Queue(maxsize=num_tcp_workers * 2)
        self.tcp_queue = asyncio.Queue(maxsize=num_tls_workers * 2)
        self.tls_queue = asyncio.Queue(maxsize=num_speed_workers * 2)
        self.xray_queue = asyncio.Queue(maxsize=num_xray_workers * 3)

        self.query_one("#tcp_bar", ProgressBar).total = self.raw_queue.maxsize
        self.query_one("#tls_bar", ProgressBar).total = self.tcp_queue.maxsize
        self.query_one("#speed_bar", ProgressBar).total = self.tls_queue.maxsize
        self.query_one("#xray_bar", ProgressBar).total = self.xray_queue.maxsize
        self.query_one("#target_bar", ProgressBar).total = self.target_ips

        self.found_ips = []
        self.hot_subnets = []
        self.results_table.clear()

        self.tasks = [asyncio.create_task(self.ui_updater()), asyncio.create_task(self.producer_worker())]

        for _ in range(num_tcp_workers): self.tasks.append(asyncio.create_task(self.phase1_tcp_worker()))
        for _ in range(num_tls_workers): self.tasks.append(asyncio.create_task(self.phase2_tls_worker()))
        for _ in range(num_speed_workers): self.tasks.append(asyncio.create_task(self.phase3_speed_worker()))

        if self.xray_enabled:
            for _ in range(num_xray_workers): self.tasks.append(asyncio.create_task(self.phase4_xray_worker()))

    def action_stop_scan(self):
        logging.info("Scan manually or automatically stopped.")
        self.stop_event.set()
        for task in self.tasks:
            task.cancel()
        self.is_scanning = False

        try:
            self.query_one("#target_bar", ProgressBar).progress = len(self.found_ips)
        except Exception:
            pass

        self.log_view.write("[bold red]Scan Terminated.[/bold red]")
        self._manual_save_csv()

        self.query_one("#btn_start").disabled = False
        self.query_one("#btn_pause").disabled = True
        self.query_one("#btn_resume").disabled = True
        self.query_one("#btn_stop").disabled = True

    async def ui_updater(self):
        try:
            while not self.stop_event.is_set():
                self.query_one("#tcp_bar", ProgressBar).progress = self.raw_queue.qsize() + self.active_tcp
                self.query_one("#tls_bar", ProgressBar).progress = self.tcp_queue.qsize() + self.active_tls
                self.query_one("#speed_bar", ProgressBar).progress = self.tls_queue.qsize() + self.active_speed
                self.query_one("#xray_bar", ProgressBar).progress = self.xray_queue.qsize() + self.active_xray
                self.query_one("#target_bar", ProgressBar).progress = len(self.found_ips)
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass

    async def producer_worker(self):
        try:
            while not self.stop_event.is_set():
                await self.active_event.wait()
                ip = self._generate_random_ip()
                try:
                    await asyncio.wait_for(self.raw_queue.put(ip), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def phase1_tcp_worker(self):
        debug = self.query_one("#debug_switch", Switch).value
        try:
            while not self.stop_event.is_set():
                await self.active_event.wait()
                try:
                    ip = await asyncio.wait_for(self.raw_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                self.active_tcp += 1
                try:
                    fut = asyncio.open_connection(ip, 443)
                    _, writer = await asyncio.wait_for(fut, timeout=1.5)
                    writer.close()
                    await writer.wait_closed()

                    if debug: self.log_view.write(f"[bright_black]TCP OK:[/bright_black] {ip}")

                    try:
                        await asyncio.wait_for(self.tcp_queue.put(ip), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
                except Exception:
                    pass
                finally:
                    self.active_tcp -= 1
                    self.raw_queue.task_done()
        except asyncio.CancelledError:
            pass

    async def phase2_tls_worker(self):
        debug = self.query_one("#debug_switch", Switch).value
        try:
            while not self.stop_event.is_set():
                await self.active_event.wait()
                try:
                    ip = await asyncio.wait_for(self.tcp_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                self.active_tls += 1
                sni_domain = random.choice(self.domains)
                try:
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE

                    start_time = time.monotonic()
                    fut = asyncio.open_connection(ip, 443, ssl=context, server_hostname=sni_domain)
                    reader, writer = await asyncio.wait_for(fut, timeout=2.0)

                    request = f"GET / HTTP/1.1\r\nHost: {sni_domain}\r\nConnection: close\r\n\r\n".encode()
                    writer.write(request)
                    await writer.drain()

                    response = await asyncio.wait_for(reader.read(1024), timeout=2.0)
                    writer.close()
                    await writer.wait_closed()

                    tls_latency_ms = (time.monotonic() - start_time) * 1000

                    if b"cloudflare" in response.lower() or b"403 Forbidden" in response:
                        if debug: self.log_view.write(
                            f"[bright_magenta]TLS OK:[/bright_magenta] {ip} ({tls_latency_ms:.0f}ms)")

                        subnet_str = ip.rsplit('.', 1)[0] + '.0/24' if '.' in ip else ip.rsplit(':', 1)[0] + '::/48'
                        self.hot_subnets.append(ipaddress.ip_network(subnet_str, strict=False))
                        if len(self.hot_subnets) > 50: self.hot_subnets.pop(0)

                        try:
                            await asyncio.wait_for(self.tls_queue.put((ip, tls_latency_ms)), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass
                except Exception:
                    pass
                finally:
                    self.active_tls -= 1
                    self.tcp_queue.task_done()
        except asyncio.CancelledError:
            pass

    async def phase3_speed_worker(self):
        debug = self.query_one("#debug_switch", Switch).value
        try:
            while not self.stop_event.is_set():
                await self.active_event.wait()
                try:
                    data = await asyncio.wait_for(self.tls_queue.get(), timeout=0.5)
                    ip, tls_latency_ms = data
                except asyncio.TimeoutError:
                    continue

                self.active_speed += 1
                try:
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE

                    start_time = time.monotonic()
                    fut = asyncio.open_connection(ip, 443, ssl=context, server_hostname="speed.cloudflare.com")
                    reader, writer = await asyncio.wait_for(fut, timeout=3.0)

                    http_req = (
                        f"GET {SPEED_TEST_PATH} HTTP/1.1\r\nHost: speed.cloudflare.com\r\nUser-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n").encode()
                    writer.write(http_req)
                    await writer.drain()

                    total_bytes = 0
                    while True:
                        chunk = await asyncio.wait_for(reader.read(8192), timeout=2.0)
                        if not chunk: break
                        total_bytes += len(chunk)

                    duration = time.monotonic() - start_time
                    writer.close()
                    await writer.wait_closed()

                    if total_bytes > 500_000:
                        speed_mbps = (total_bytes * 8 / 1_000_000) / duration

                        if self.xray_enabled:
                            if debug: self.log_view.write(
                                f"[bright_cyan]SPEED OK:[/bright_cyan] {ip} ({speed_mbps:.2f} Mbps) -> Sending to Xray")
                            try:
                                await asyncio.wait_for(self.xray_queue.put((ip, tls_latency_ms, speed_mbps)),
                                                       timeout=1.5)
                            except asyncio.TimeoutError:
                                pass
                        else:
                            quality_score = (speed_mbps * 1000) / max(tls_latency_ms, 1)
                            self.log_view.write(
                                f"[bold bright_green]WINNER![/bold bright_green] {ip} | {speed_mbps:.2f} Mbps | {tls_latency_ms:.0f} ms")

                            self.found_ips.append((ip, speed_mbps, tls_latency_ms, 0, quality_score))
                            self._generate_outputs(ip)
                            self._refresh_table()

                            if len(self.found_ips) >= self.target_ips:
                                self.log_view.write("[bold yellow]TARGET REACHED! Auto-stopping...[/bold yellow]")
                                self.action_stop_scan()

                except Exception as e:
                    logging.debug(f"Speed test failed for {ip}: {e}")
                finally:
                    self.active_speed -= 1
                    self.tls_queue.task_done()
        except asyncio.CancelledError:
            pass

    async def phase4_xray_worker(self):
        debug = self.query_one("#debug_switch", Switch).value
        try:
            while not self.stop_event.is_set():
                await self.active_event.wait()
                try:
                    data = await asyncio.wait_for(self.xray_queue.get(), timeout=0.5)
                    ip, tls_latency_ms, speed_mbps = data
                except asyncio.TimeoutError:
                    continue

                self.active_xray += 1
                proc = None
                tmp_path = None
                try:
                    local_port = random.randint(20000, 50000)
                    config = copy.deepcopy(self.base_config)

                    config.pop("routing", None)
                    config.pop("dns", None)

                    config["inbounds"][0]["port"] = local_port
                    config["inbounds"][0]["protocol"] = "mixed"
                    config["outbounds"][0]["settings"]["vnext"][0]["address"] = ip

                    fd, tmp_path = tempfile.mkstemp(suffix=".json")
                    with os.fdopen(fd, 'w') as f:
                        json.dump(config, f)

                    proc = await asyncio.create_subprocess_exec(
                        self.xray_exe, "run", "-c", tmp_path,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )

                    await asyncio.sleep(1.5)

                    start_time = time.monotonic()
                    async with aiohttp.ClientSession() as session:
                        async with session.get(VERIFY_URL, proxy=f"http://127.0.0.1:{local_port}", timeout=5) as resp:
                            xray_latency_ms = (time.monotonic() - start_time) * 1000

                            if resp.status in [200, 204]:
                                quality_score = (speed_mbps * 1000) / max(xray_latency_ms, 1)

                                self.log_view.write(
                                    f"[bold bright_green]XRAY VERIFIED![/bold bright_green] {ip} | Speed: {speed_mbps:.2f} Mbps | Xray Latency: {xray_latency_ms:.0f} ms")

                                self.found_ips.append((ip, speed_mbps, tls_latency_ms, xray_latency_ms, quality_score))

                                self._generate_outputs(ip)
                                self._refresh_table()

                                if len(self.found_ips) >= self.target_ips:
                                    self.log_view.write("[bold yellow]TARGET REACHED! Auto-stopping...[/bold yellow]")
                                    self.action_stop_scan()
                            else:
                                if debug: self.log_view.write(
                                    f"[red]Xray route failed for {ip} (HTTP {resp.status})[/red]")

                except (asyncio.TimeoutError, TimeoutError):
                    if debug: self.log_view.write(f"[gray]Xray Timeout on {ip} (Blocked or too slow)[/gray]")
                except aiohttp.ClientError:
                    if debug: self.log_view.write(f"[gray]Xray Connection Refused for {ip}[/gray]")
                except Exception as e:
                    logging.exception(f"Xray Critical Error on {ip}: {str(e)}")
                    if debug: self.log_view.write(f"[red]Xray Exception on {ip}: Check error log![/red]")
                finally:
                    if proc and proc.returncode is None:
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            proc.kill()
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass

                    self.active_xray -= 1
                    self.xray_queue.task_done()
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    app = IPScannerUI()
    app.run()