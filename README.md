# ⚡ WaldonCFscanner-python | The Ultimate Xray-Core VLESS Verifier

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An incredibly fast, highly-optimized Cloudflare IP Scanner built specifically to bypass severe internet censorship (GFW) using modern proxy protocols. 

Unlike traditional ping-based scanners (which often find IPs that connect but fail to route data), **WaldonCFscanner** utilizes a revolutionary **4-Stage Hardware-Aware Pipeline**. It directly embeds the `Xray-core` into the scanning process to cryptographically prove that an IP can successfully establish a VLESS/VMess tunnel before it is ever shown to you.

Created by [@amirrezas](https://github.com/amirrezas). Inspired by the works of MortezaBashsiz and the global anti-censorship community.

---

## ✨ Zero-Setup Execution (Run with a Single Command!)
We believe anti-censorship tools should be accessible to everyone. You do not need to understand Python, `pip`, or GitHub to use this tool. 

If you have Python installed, just run the script. The engine will automatically build its own environment:
1. It detects missing Python libraries (`aiohttp`, `textual`) and installs them.
2. It queries the official XTLS GitHub API, downloads the latest `Xray-core` for your specific operating system, and extracts it silently.

```bash
python scanner.py
```
*(Use `python3 scanner.py` on Linux/Mac)*

---

## 🧠 How The 4-Stage Engine Works
To find working IPs out of millions without crashing your OS, the scanner acts like an assembly line:

1. **🛡️ Stage 1: TCP Probing:** Lightning-fast non-blocking sockets check if the IP is physically online. (Processes thousands per second).
2. **⚡ Stage 2: TLS SNI Injection:** Performs a cryptographic handshake using an Iranian domain as the SNI. This proves the IP belongs to Cloudflare AND proves the national firewall isn't actively dropping the domain.
3. **🚀 Stage 3: Pure Python Speed Test:** Attempts to stream a 1MB dummy payload directly from the edge node to measure raw bandwidth capacity.
4. **🔐 Stage 4: Xray Verification (The VIP Room):** Only the fastest IPs reach here. The script dynamically spins up a headless `Xray-core`, injects your specific config, and tests a live VLESS proxy tunnel to measure the true Time-to-First-Byte (TTFB) latency.

### Advanced Algorithms Under the Hood
* **Stratified Randomization:** Prevents getting "stuck" in massive `104.x.x.x` ranges by grouping IPs by their first octet, ensuring a truly global search across all Cloudflare datacenters.
* **Hot-Subnet Feedback Loop:** If an IP passes Stage 2, the engine flags that `/24` subnet as "Hot" and temporarily focuses resources there to mine it for more working IPs.
* **Backpressure Drop Mechanism:** Prevents RAM exhaustion by intelligently dropping IPs if the heavy Xray queues become too full.

---

## 🛠️ Customization & Config Generation

By default, the scanner works out-of-the-box. However, you can personalize it by placing template files in the same folder:

1. **JSON Generation:** Create a `config.json` containing your server's base VLESS config.
2. **URI Generation:** Create a `config.txt` and paste your clipboard link (e.g., `vless://uuid@172.64.x.x:443?type=ws...`).

When the scanner finds a winning IP, it will automatically create an `output_configs/` folder and generate ready-to-use `.json` files and a `vless_links.txt` file with the clean IPs injected directly into your templates!

## 📊 The TUI Dashboard
The interactive Terminal User Interface (TUI) lets you monitor the engine in real-time.
* **Power (1-100):** The engine reads your total CPU cores and OS socket limits. Adjusting this slider allocates how much of your hardware the scanner is allowed to use. 
* **Target IPs:** The scan will automatically terminate once it finds this exact number of perfect IPs.
* **Quality Score:** IPs are automatically sorted live based on a formula that mathematically balances high download throughput with ultra-low Xray TTFB latency.

## 🤝 Debugging & Support
If the scanner freezes or fails to route traffic, click the **"Save Log"** button in the dashboard. This generates a professional `scanner_error.log` file containing full Python stack traces and Xray binary errors. 

Please open an [Issue on GitHub](https://github.com/amirrezas/WaldonCFscanner-python/issues) and attach this log file so we can improve the engine!

---
*Disclaimer: This tool is intended for network diagnostics, latency optimization, and ensuring open access to the free internet. Please use responsibly.*