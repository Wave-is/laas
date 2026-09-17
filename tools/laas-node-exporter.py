#!/usr/bin/env python3
"""
LAAS Node Exporter — Lightweight HTTP Telemetry Agent for Linux/Windows nodes.
Exposes CPU, RAM, GPU (NVIDIA), and local Llama-server metrics over pure HTTP.
Zero external dependencies (uses standard Python library).
"""
import argparse
import http.server
import json
import os
import platform
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

LLAMA_URL = "http://127.0.0.1:8080"
DEFAULT_PORT = 9835

# Cache telemetry to avoid calling nvidia-smi on every single HTTP request
telemetry_cache = {}
cache_lock = threading.Lock()
last_sample_time = 0

def get_cpu_ram_linux():
    cpu_pct = 0.0
    mem_info = {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0}

    # Memory from /proc/meminfo
    if os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            mem = {}
            for line in lines:
                parts = line.split(":")
                if len(parts) == 2:
                    k = parts[0].strip()
                    v = parts[1].strip().split()[0]
                    mem[k] = float(v)
            total = mem.get("MemTotal", 0) / (1024 * 1024)
            avail = mem.get("MemAvailable", mem.get("MemFree", 0)) / (1024 * 1024)
            mem_info["total_gb"] = round(total, 1)
            mem_info["free_gb"] = round(avail, 1)
            mem_info["used_gb"] = round(max(0, total - avail), 1)
        except Exception:
            pass

    # CPU load from /proc/loadavg or uptime
    if os.path.exists("/proc/loadavg"):
        try:
            with open("/proc/loadavg", "r") as f:
                load = f.read().split()[0]
            cores = os.cpu_count() or 1
            cpu_pct = round(min(100.0, (float(load) / cores) * 100.0), 1)
        except Exception:
            pass

    return cpu_pct, mem_info

def get_gpus_nvidiasmi():
    gpus = []
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,fan.speed",
            "--format=csv,noheader,nounits"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
        if res.returncode == 0 and res.stdout.strip():
            for line in res.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 7:
                    idx = int(parts[0]) if parts[0].isdigit() else 0
                    name = parts[1]
                    util = float(parts[2]) if parts[2].replace('.', '', 1).isdigit() else 0.0
                    mem_used = round(float(parts[3]) / 1024.0, 2) if parts[3].replace('.', '', 1).isdigit() else 0.0
                    mem_total = round(float(parts[4]) / 1024.0, 2) if parts[4].replace('.', '', 1).isdigit() else 0.0
                    temp = float(parts[5]) if parts[5].replace('.', '', 1).isdigit() else 0.0
                    power = float(parts[6]) if parts[6].replace('.', '', 1).isdigit() else 0.0
                    fan = float(parts[7]) if len(parts) > 7 and parts[7].replace('.', '', 1).isdigit() else 0.0

                    gpus.append({
                        "index": idx,
                        "name": name,
                        "util_percent": util,
                        "vram_used_gb": mem_used,
                        "vram_total_gb": mem_total,
                        "temp_c": temp,
                        "power_w": power,
                        "fan_percent": fan
                    })
    except Exception:
        pass
    return gpus

def get_llama_slots(llama_url):
    inf = {
        "online": False,
        "is_processing": False,
        "n_ctx": 65536,
        "prompt_tokens": 0,
        "prompt_cached": 0,
        "decoded_tokens": 0,
        "remain_tokens": 0,
    }
    try:
        req = urllib.request.Request(f"{llama_url}/slots", headers={"User-Agent": "laas-exporter"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                slots = json.loads(resp.read().decode())
                if slots:
                    inf["online"] = True
                    s = slots[0]
                    inf["is_processing"] = s.get("is_processing", False)
                    inf["n_ctx"] = s.get("n_ctx", 65536)
                    inf["prompt_tokens"] = s.get("n_prompt_tokens", 0)
                    inf["prompt_cached"] = s.get("n_prompt_tokens_cache", 0)
                    next_tok = s.get("next_token", [{}])[0] if s.get("next_token") else {}
                    inf["decoded_tokens"] = next_tok.get("n_decoded", 0)
                    inf["remain_tokens"] = next_tok.get("n_remain", 0)
    except Exception:
        pass
    return inf

def collect_telemetry(llama_url):
    global telemetry_cache, last_sample_time
    now = time.time()
    if now - last_sample_time < 1.0 and telemetry_cache:
        return telemetry_cache

    cpu_pct, mem_info = get_cpu_ram_linux()
    gpus = get_gpus_nvidiasmi()
    inf = get_llama_slots(llama_url)

    snap = {
        "status": "online",
        "timestamp": now,
        "hostname": platform.node(),
        "platform": platform.system(),
        "cpu": {
            "util_percent": cpu_pct,
            "cores": os.cpu_count() or 1
        },
        "ram": mem_info,
        "gpus": gpus,
        "inference": inf
    }

    with cache_lock:
        telemetry_cache = snap
        last_sample_time = now

    return snap

class TelemetryHandler(http.server.BaseHTTPRequestHandler):
    llama_url = LLAMA_URL

    def log_message(self, format, *args):
        # Silence default access logging to keep stdout clean
        pass

    def do_GET(self):
        if self.path in ("/", "/api/telemetry"):
            data = collect_telemetry(self.llama_url)
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

def main():
    parser = argparse.ArgumentParser(description="LAAS Node Exporter (Pure HTTP)")
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT, help=f"HTTP port (default: {DEFAULT_PORT})")
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind (default: 0.0.0.0)")
    parser.add_argument("--llama-url", default=LLAMA_URL, help=f"Local llama-server base URL (default: {LLAMA_URL})")
    args = parser.parse_args()

    TelemetryHandler.llama_url = args.llama_url

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((args.host, args.port), TelemetryHandler) as httpd:
        print(f"LAAS Node Exporter listening on http://{args.host}:{args.port}")
        print(f"Local Llama-server target: {args.llama_url}")
        print("Ready to serve telemetry over pure HTTP.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down exporter.")

if __name__ == "__main__":
    main()
