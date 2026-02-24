import sys
import subprocess
import os
import urllib.request
import zipfile
import platform
import json
import asyncio
import urllib.parse

# --- BOOT LOGS & WINDOWS ENCODING PATCH ---
if platform.system() == 'Windows':
    os.system("title WaldonCFscanner - Booting Engine...")
    os.system("chcp 65001 > nul")
    sys.stdout.reconfigure(encoding='utf-8')

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

print("Booting Scanner Engine... Please wait a few seconds...")

IS_COMPILED = getattr(sys, 'frozen', False)

if IS_COMPILED:
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR


def get_resource_path(filename):
    external_path = os.path.join(BASE_DIR, filename)
    if os.path.exists(external_path): return external_path
    internal_path = os.path.join(BUNDLE_DIR, filename)
    if os.path.exists(internal_path): return internal_path
    return external_path


CSV_FILE = os.path.join(BASE_DIR, "clean_ips.csv")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
URI_FILE = os.path.join(BASE_DIR, "config.txt")
OUTPUT_DIR = os.path.join(BASE_DIR, "output_configs")
ERROR_LOG_FILE = os.path.join(BASE_DIR, "scanner_error.log")

IPV4_FILE = get_resource_path("ipv4.txt")
IPV6_FILE = get_resource_path("ipv6.txt")
DOMAINS_FILE = get_resource_path("cloudflare-domains.txt")


def ensure_dependencies():
    required_packages = {"aiohttp": "aiohttp", "textual": "textual", "pyperclip": "pyperclip"}
    missing_packages = []
    for module_name, pip_name in required_packages.items():
        try:
            __import__(module_name)
        except ImportError:
            missing_packages.append(pip_name)

    if missing_packages:
        print(f"📦 First-time setup detected. Installing: {', '.join(missing_packages)}")
        cmd = [sys.executable, "-m", "pip", "install", "--user"]
        if sys.platform.startswith('linux'): cmd.append("--break-system-packages")
        cmd.extend(missing_packages)
        try:
            subprocess.check_call(cmd)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except subprocess.CalledProcessError:
            print("❌ ERROR: Failed to install dependencies.")
            sys.exit(1)


if not IS_COMPILED: ensure_dependencies()


def ensure_xray_core():
    sys_os = platform.system()
    sys_machine = platform.machine().lower()
    is_termux = "com.termux" in os.environ.get("PREFIX", "")

    exe_name = "xray.exe" if sys_os == "Windows" else "xray"
    xray_path = os.path.join(BASE_DIR, exe_name)

    if os.path.exists(xray_path):
        try:
            # Self-Healing: Test if the existing binary is broken (e_type: 2)
            subprocess.run([xray_path, "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return
        except Exception:
            print("⚠️ Existing Xray binary is broken or incompatible with this architecture. Redownloading...")
            os.remove(xray_path)

    print(f"🔍 Xray-core missing. Fetching latest for {sys_os} ({sys_machine})...")
    api_url = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"

    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())

        # SMART ARCHITECTURE DETECTION
        if sys_os == "Windows":
            asset_suffix = "windows-64.zip"
        elif is_termux:
            if "aarch64" in sys_machine or "armv8" in sys_machine or "arm64" in sys_machine:
                asset_suffix = "android-arm64-v8a.zip"
            else:
                asset_suffix = "android-amd64.zip"
        else:
            if "aarch64" in sys_machine or "armv8" in sys_machine or "arm64" in sys_machine:
                asset_suffix = "linux-arm64-v8a.zip"
            else:
                asset_suffix = "linux-64.zip"

        download_url = next(
            (a['browser_download_url'] for a in data.get('assets', []) if a['name'].endswith(asset_suffix)), None)

        if not download_url:
            print(f"❌ Could not find suitable Xray binary ({asset_suffix}) on GitHub.")
            return

        print(f"⬇️ Downloading {asset_suffix} (approx 20MB)...")
        zip_path = os.path.join(BASE_DIR, "xray_temp.zip")
        urllib.request.urlretrieve(download_url, zip_path)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for member in zip_ref.namelist():
                if member == exe_name or member.endswith(f"/{exe_name}"):
                    source = zip_ref.open(member)
                    with open(xray_path, "wb") as target: target.write(source.read())
                    break
        os.remove(zip_path)

        if sys_os != "Windows":
            import stat
            os.chmod(xray_path, os.stat(xray_path).st_mode | stat.S_IEXEC)

        print("✅ Xray-core installed successfully!\n")
    except Exception as e:
        print(f"❌ Failed to auto-download Xray: {e}")


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
import pyperclip

from textual import on
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, DataTable, ProgressBar, Label, Button, Input, Switch
from textual.containers import Horizontal, Vertical, Grid
from textual.binding import Binding

if os.path.exists(ERROR_LOG_FILE):
    os.remove(ERROR_LOG_FILE)

logging.basicConfig(
    filename=ERROR_LOG_FILE,
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


def get_system_socket_capacity() -> int:
    cores = os.cpu_count() or 4
    return min(cores * 150, 1000) if platform.system() == 'Windows' else min(cores * 300, 3000)


class IPScannerUI(App):
    TITLE = "High-Speed Xray VLESS/Trojan Verification Engine"

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
                yield Label("GitHub: github.com/amirrezas/WaldonCFscanner", id="github-link")
            with Grid(id="settings-grid"):
                yield Label("Power (1-100):", classes="lbl")
                yield Input("10", id="power_input", classes="inp")
                yield Label("Target IPs:", classes="lbl")
                yield Input("10", id="target_input", classes="inp")
                yield Label("Debug Mode:", classes="lbl")
                yield Switch(id="debug_switch", value=True)
            with Horizontal(id="clipboard-row"):
                yield Label("URI:", classes="lbl")
                yield Input(placeholder="Paste vless:// or trojan:// here", id="clipboard_input")
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
                yield Label("4. Xray Payload", classes="queue-title")
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
            parsed = urllib.parse.urlparse(uri)
            scheme = parsed.scheme.lower()
            if scheme not in ["vless", "trojan"]: return {}

            netloc = parsed.netloc
            if "@" in netloc:
                uuid, server_port = netloc.split("@", 1)
            else:
                return {}

            if ":" in server_port:
                server, port = server_port.split(":", 1)
                port = int(port)
            else:
                server = server_port
                port = 443

            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            params = {k: v[0] for k, v in qs.items()}

            net = params.get("type", "tcp")
            sec = params.get("security", "none")
            sni = params.get("sni", "")
            host = params.get("host", "")
            path = params.get("path", "/")
            fp = params.get("fp", "chrome")
            mode = params.get("mode", "auto")
            serviceName = params.get("serviceName", path)

            if not sni: sni = host
            if not sni: sni = server
            if not host: host = sni

            default_alpn = "http/1.1" if net == "ws" else "h2,http/1.1"
            alpn_str = params.get("alpn", default_alpn)
            alpn_list = [x.strip() for x in alpn_str.split(",")]

            config = {
                "log": {"loglevel": "warning"},
                "inbounds": [{"port": 10809, "listen": "127.0.0.1", "protocol": "mixed",
                              "settings": {"allowTransparent": False}}],
                "outbounds": [{
                    "protocol": scheme,
                    "settings": {},
                    "streamSettings": {
                        "network": net,
                        "security": sec
                    }
                }]
            }

            if scheme == "vless":
                config["outbounds"][0]["settings"] = {
                    "vnext": [{
                        "address": server,
                        "port": port,
                        "users": [{"id": uuid, "encryption": params.get("encryption", "none")}]
                    }]
                }
            elif scheme == "trojan":
                config["outbounds"][0]["settings"] = {
                    "servers": [{
                        "address": server,
                        "port": port,
                        "password": uuid
                    }]
                }

            if sec == "tls":
                config["outbounds"][0]["streamSettings"]["tlsSettings"] = {
                    "allowInsecure": True,
                    "serverName": sni,
                    "fingerprint": fp,
                    "alpn": alpn_list
                }

            if net == "ws":
                config["outbounds"][0]["streamSettings"]["wsSettings"] = {
                    "path": path,
                    "headers": {"Host": host} if host else {}
                }
            elif net == "xhttp":
                config["outbounds"][0]["streamSettings"]["xhttpSettings"] = {
                    "path": path,
                    "host": host,
                    "mode": mode
                }
            elif net == "grpc":
                config["outbounds"][0]["streamSettings"]["grpcSettings"] = {
                    "serviceName": serviceName,
                    "multiMode": (mode == "multi")
                }
            elif net == "tcp" and params.get("headerType") == "http":
                config["outbounds"][0]["streamSettings"]["tcpSettings"] = {
                    "header": {
                        "type": "http",
                        "request": {
                            "headers": {"Host": [host]} if host else {},
                            "path": [path]
                        }
                    }
                }

            return config
        except Exception as e:
            logging.error(f"URI Parse Error: {e}")
            return {}

    def parse_json_to_uri(self, config: dict) -> str:
        try:
            out = config["outbounds"][0]
            scheme = out.get("protocol", "vless")

            if scheme == "vless":
                vnext = out["settings"]["vnext"][0]
                uuid = vnext["users"][0]["id"]
                server = vnext["address"]
                port = vnext["port"]
                encryption = vnext["users"][0].get("encryption", "none")
                params = {"type": "tcp", "encryption": encryption}
            elif scheme == "trojan":
                srv = out["settings"]["servers"][0]
                uuid = srv["password"]
                server = srv["address"]
                port = srv["port"]
                params = {"type": "tcp"}
            else:
                return ""

            stream = out.get("streamSettings", {})
            net = stream.get("network", "tcp")
            sec = stream.get("security", "none")

            params["type"] = net
            params["security"] = sec

            if sec == "tls":
                tls = stream.get("tlsSettings", {})
                if "serverName" in tls: params["sni"] = tls["serverName"]
                if "fingerprint" in tls: params["fp"] = tls["fingerprint"]
                if "alpn" in tls: params["alpn"] = ",".join(tls["alpn"])

            if net == "ws":
                ws = stream.get("wsSettings", {})
                if "path" in ws: params["path"] = ws["path"]
                if "headers" in ws and "Host" in ws["headers"]: params["host"] = ws["headers"]["Host"]
            elif net == "xhttp":
                xhttp = stream.get("xhttpSettings", {})
                if "path" in xhttp: params["path"] = xhttp["path"]
                if "host" in xhttp: params["host"] = xhttp["host"]
                if "mode" in xhttp: params["mode"] = xhttp["mode"]
            elif net == "grpc":
                grpc = stream.get("grpcSettings", {})
                if "serviceName" in grpc: params["serviceName"] = grpc["serviceName"]
                if grpc.get("multiMode"): params["mode"] = "multi"
            elif net == "tcp":
                tcp = stream.get("tcpSettings", {})
                header = tcp.get("header", {})
                if header.get("type") == "http":
                    params["headerType"] = "http"
                    req = header.get("request", {})
                    if "path" in req and req["path"]: params["path"] = req["path"][0]
                    if "headers" in req and "Host" in req["headers"]: params["host"] = req["headers"]["Host"][0]

            q = urllib.parse.urlencode(params, safe=":/,")
            return f"{scheme}://{uuid}@{server}:{port}?{q}#WaldonCFscanner"
        except Exception as e:
            logging.error(f"JSON Parse Error: {e}")
            return ""

    def on_mount(self) -> None:
        self.log_view = self.query_one("#log_view", RichLog)
        self.results_table = self.query_one("#results_table", DataTable)
        self.results_table.add_columns("Rank", "IP Address", "Speed", "TLS Lat.", "TTFB", "Score")

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
                    if self.base_uri.startswith("vless://") or self.base_uri.startswith("trojan://"):
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
            if val.startswith("vless://") or val.startswith("trojan://"):
                self.query_one("#clipboard_input", Input).value = val
                self.log_view.write("[bold bright_green]URI successfully pasted from clipboard![/bold bright_green]")
            else:
                self.log_view.write("[bold yellow]Clipboard does not contain a valid vless/trojan link.[/bold yellow]")
        except Exception as e:
            self.log_view.write(f"[bold red]Failed to read clipboard: {str(e)}[/bold red]")

    @on(Input.Changed, "#clipboard_input")
    def on_clipboard_changed(self, event: Input.Changed):
        val = event.value.strip()
        if val.startswith("vless://") or val.startswith("trojan://"):
            self.base_uri = val
            self.base_config = self.parse_uri_to_json(val)
            if self.base_config and os.path.exists(self.xray_exe):
                if not self.xray_enabled:
                    self.xray_enabled = True
                    self.log_view.write(
                        "[bold bright_green]Configuration loaded! Xray Engine Activated.[/bold bright_green]")

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
            return str(ipaddress.IPv6Address(int(net.network_address) + random.getrandbits(128 - net.prefixlen)))

    def _generate_outputs_smart(self, new_uri: str, config: dict, ip: str):
        try:
            json_path = os.path.join(OUTPUT_DIR, f"config_{ip.replace(':', '_')}.json")
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)

            uri_path = os.path.join(OUTPUT_DIR, "vless_links.txt")
            with open(uri_path, 'a', encoding='utf-8') as f:
                f.write(new_uri + "\n")
        except Exception as e:
            logging.error(f"Failed to generate output for {ip}: {e}")

    def _refresh_table(self):
        self.results_table.clear()
        sorted_ips = sorted(self.found_ips, key=lambda x: x[4], reverse=True)
        for idx, (ip, speed, tls_lat, xray_lat, score) in enumerate(sorted_ips):
            self.results_table.add_row(str(idx + 1), ip, f"{speed:.0f} KB/s", f"{tls_lat:.0f} ms", f"{xray_lat:.0f} ms",
                                       f"{score:.0f}")

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
                    ["Rank", "IP Address", "Speed (KB/s)", "TLS Latency (ms)", "TTFB (ms)", "Quality Score"])
                sorted_ips = sorted(self.found_ips, key=lambda x: x[4], reverse=True)
                for idx, (ip, speed, tls_lat, xray_lat, score) in enumerate(sorted_ips):
                    writer.writerow([idx + 1, ip, f"{speed:.0f}", f"{tls_lat:.0f}", f"{xray_lat:.0f}", f"{score:.0f}"])
            self.log_view.write(
                f"[bold bright_cyan]Saved & Sorted {len(self.found_ips)} IPs to {CSV_FILE}[/bold bright_cyan]")
        except Exception as e:
            logging.error(f"Failed to save CSV: {e}")

    def _manual_save_log(self):
        self.log_view.write(f"[bold bright_cyan]Professional Error Log saved![/bold bright_cyan]")
        self.log_view.write(f"[gray]Check 'scanner_error.log' in the folder.[/gray]")

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
        for task in self.tasks: task.cancel()
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
                        f"GET /__down?bytes=100000 HTTP/1.1\r\nHost: speed.cloudflare.com\r\nUser-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n").encode()
                    writer.write(http_req)
                    await writer.drain()

                    total_bytes = 0
                    while True:
                        chunk = await asyncio.wait_for(reader.read(8192), timeout=2.0)
                        if not chunk: break
                        total_bytes += len(chunk)

                    writer.close()
                    await writer.wait_closed()

                    if total_bytes > 50000:
                        if self.xray_enabled:
                            if debug: self.log_view.write(
                                f"[bright_cyan]SPEED OK:[/bright_cyan] {ip} -> Sending to Xray")
                            try:
                                await asyncio.wait_for(self.xray_queue.put((ip, tls_latency_ms)), timeout=1.5)
                            except asyncio.TimeoutError:
                                pass
                except Exception as e:
                    pass
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
                    ip, tls_latency_ms = data
                except asyncio.TimeoutError:
                    continue

                self.active_xray += 1
                proc = None
                tmp_path = None
                drain_task = None

                try:
                    local_port = random.randint(20000, 50000)
                    config = copy.deepcopy(self.base_config)

                    config.pop("routing", None)
                    config.pop("dns", None)
                    config["inbounds"][0]["port"] = local_port
                    config["inbounds"][0]["protocol"] = "mixed"

                    scheme = config["outbounds"][0]["protocol"]
                    if scheme == "vless":
                        config["outbounds"][0]["settings"]["vnext"][0]["address"] = ip
                    elif scheme == "trojan":
                        config["outbounds"][0]["settings"]["servers"][0]["address"] = ip

                    fd, tmp_path = tempfile.mkstemp(suffix=".json")
                    with os.fdopen(fd, 'w') as f:
                        json.dump(config, f)

                    proc = await asyncio.create_subprocess_exec(
                        self.xray_exe, "run", "-c", tmp_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT
                    )

                    async def drain_output():
                        try:
                            while True:
                                line = await proc.stdout.readline()
                                if not line: break
                                text = line.decode('utf-8', errors='ignore').strip()
                                logging.debug(f"[XRAY {ip}] {text}")
                                if debug and "deprecated" not in text:
                                    self.log_view.write(f"[gray]⚙️ XRAY ({ip}): {text}[/gray]")
                        except Exception:
                            pass

                    drain_task = asyncio.create_task(drain_output())
                    await asyncio.sleep(1.5)

                    start_time = time.monotonic()

                    async with aiohttp.ClientSession() as session:
                        async with session.get("https://speed.cloudflare.com/__down?bytes=500000",
                                               proxy=f"http://127.0.0.1:{local_port}", timeout=10) as resp:
                            ttfb_ms = (time.monotonic() - start_time) * 1000

                            if resp.status == 200:
                                body = await resp.read()
                                total_bytes = len(body)
                                download_time = max(time.monotonic() - start_time - (ttfb_ms / 1000), 0.1)

                                if total_bytes >= 100000:
                                    speed_kbps = (total_bytes / 1024) / download_time

                                    parsed = urllib.parse.urlparse(self.base_uri)
                                    scheme = parsed.scheme
                                    uuid, server_port = parsed.netloc.split("@", 1)
                                    original_server = server_port.split(":")[0]
                                    port = server_port.split(":")[1] if ":" in server_port else "443"

                                    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                                    params = {k: v[0] for k, v in qs.items()}

                                    if not params.get("sni"): params["sni"] = original_server
                                    if not params.get("host"): params["host"] = params["sni"]
                                    if not params.get("fp"): params["fp"] = "chrome"

                                    new_query = urllib.parse.urlencode(params, safe=":/,")
                                    fragment = f"#{parsed.fragment}" if parsed.fragment else "#Verified"
                                    formatted_ip = f"[{ip}]" if ":" in ip else ip

                                    new_uri = f"{scheme}://{uuid}@{formatted_ip}:{port}?{new_query}{fragment}"
                                    quality_score = speed_kbps / max(ttfb_ms, 1)

                                    self.log_view.write(
                                        f"[bold bright_green]XRAY VERIFIED![/bold bright_green] {ip} | {speed_kbps:.0f} KB/s | TTFB: {ttfb_ms:.0f} ms")
                                    self.found_ips.append((ip, speed_kbps, tls_latency_ms, ttfb_ms, quality_score))
                                    self._generate_outputs_smart(new_uri, config, ip)
                                    self._refresh_table()

                                    if len(self.found_ips) >= self.target_ips:
                                        self.log_view.write(
                                            "[bold yellow]TARGET REACHED! Auto-stopping...[/bold yellow]")
                                        self.action_stop_scan()
                                else:
                                    if debug: self.log_view.write(f"[red]Payload too small from {ip}[/red]")
                            else:
                                if debug: self.log_view.write(f"[red]❌ HTTP {resp.status} Error on {ip}[/red]")

                except (asyncio.TimeoutError, TimeoutError):
                    if debug: self.log_view.write(f"[gray]❌ Timeout on {ip} (Too Slow/Blocked)[/gray]")
                except aiohttp.ClientError as e:
                    if debug: self.log_view.write(f"[gray]❌ Proxy Reject on {ip}: {str(e)}[/gray]")
                except Exception as e:
                    logging.exception(f"Xray Critical Error on {ip}: {str(e)}")
                    if debug: self.log_view.write(f"[red]❌ Critical Parse Error on {ip}: Check error log![/red]")
                finally:
                    if drain_task: drain_task.cancel()
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