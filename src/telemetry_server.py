"""
LAAS Telemetry Server.
Exposes host hardware metrics in standard Telegraf Prometheus format (:9273/metrics)
and JSON (:9273/api/telemetry).
Enables any Windows machine running LAAS to act as a native cluster node.
Zero external dependencies (uses standard library + psutil).
"""
import http.server
import json
import logging
import os
import platform
import socket
import socketserver
import subprocess
import threading
import time
import psutil

from .config import config

log = logging.getLogger(__name__)

DEFAULT_TELEMETRY_PORT = 9273


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class TelemetryHandler(http.server.BaseHTTPRequestHandler):
    server_version = "LAASTelemetry/1.0"

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path == "" or path == "/":
            path = "/metrics"

        if path == "/metrics":
            self.send_prometheus_metrics()
        elif path == "/api/telemetry":
            self.send_json_telemetry()
        elif path == "/health":
            self.send_health()
        else:
            self.send_error(404, "Not Found")

    def send_prometheus_metrics(self):
        try:
            body = self.server.telemetry_manager.get_prometheus_text()
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            log.exception("Error generating prometheus metrics: %s", e)
            self.send_error(500, str(e))

    def send_json_telemetry(self):
        try:
            data_dict = self.server.telemetry_manager.get_telemetry_dict()
            body = json.dumps(data_dict, indent=2)
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(500, str(e))

    def send_health(self):
        body = json.dumps({"status": "online", "service": "laas-telemetry", "timestamp": time.time()})
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        # Suppress routine GET logs from flooding console
        pass


class TelemetryServer:
    def __init__(self):
        self._server = None
        self._thread = None
        self._lock = threading.RLock()
        self._cache = {}
        self._last_sample = 0
        self._hostname = socket.gethostname()

    def sample_hardware(self):
        now = time.time()
        with self._lock:
            if now - self._last_sample < 1.0 and self._cache:
                return self._cache

        # 1. CPU & RAM via psutil
        cpu_cores = psutil.cpu_percent(interval=None, percpu=True)
        mem = psutil.virtual_memory()
        cpu_avg = round(sum(cpu_cores) / len(cpu_cores), 1) if cpu_cores else 0.0

        # 2. GPU via nvidia-smi
        gpus = []
        try:
            cmd = ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw", "--format=csv,noheader,nounits"]
            cflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0, creationflags=cflags)
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 7:
                        gpus.append({
                            "index": int(parts[0]) if parts[0].isdigit() else 0,
                            "name": parts[1],
                            "util_percent": float(parts[2]),
                            "vram_used_mb": float(parts[3]),
                            "vram_total_mb": float(parts[4]),
                            "vram_used_gb": round(float(parts[3]) / 1024.0, 2),
                            "vram_total_gb": round(float(parts[4]) / 1024.0, 2),
                            "temp_c": float(parts[5]),
                            "power_w": float(parts[6])
                        })
        except Exception as e:
            log.debug("nvidia-smi poll error: %s", e)

        snapshot = {
            "timestamp": now,
            "hostname": self._hostname,
            "cpu_cores": cpu_cores,
            "cpu_avg": cpu_avg,
            "physical_cpus": psutil.cpu_count(logical=False) or 1,
            "mem_total_bytes": mem.total,
            "mem_used_bytes": mem.used,
            "mem_total_gb": round(mem.total / (1024**3), 1),
            "mem_used_gb": round(mem.used / (1024**3), 1),
            "gpus": gpus
        }

        with self._lock:
            self._cache = snapshot
            self._last_sample = now

        return snapshot

    def get_prometheus_text(self) -> str:
        data = self.sample_hardware()
        host = data["hostname"]
        lines = []

        # Memory
        lines.append('# HELP mem_total Telegraf collected metric')
        lines.append('# TYPE mem_total gauge')
        lines.append(f'mem_total{{host="{host}"}} {data["mem_total_bytes"]}')
        lines.append('# HELP mem_used Telegraf collected metric')
        lines.append('# TYPE mem_used gauge')
        lines.append(f'mem_used{{host="{host}"}} {data["mem_used_bytes"]}')

        # Physical CPUs
        lines.append('# HELP system_n_physical_cpus Telegraf collected metric')
        lines.append('# TYPE system_n_physical_cpus gauge')
        lines.append(f'system_n_physical_cpus{{host="{host}"}} {data["physical_cpus"]}')

        # CPU per-core
        lines.append('# HELP cpu_usage_active Telegraf collected metric')
        lines.append('# TYPE cpu_usage_active gauge')
        for i, val in enumerate(data["cpu_cores"]):
            lines.append(f'cpu_usage_active{{cpu="cpu{i}",host="{host}"}} {val}')

        # GPUs
        if data["gpus"]:
            lines.append('# HELP nvidia_smi_utilization_gpu Telegraf collected metric')
            lines.append('# TYPE nvidia_smi_utilization_gpu untyped')
            for g in data["gpus"]:
                idx = g["index"]
                name = g["name"]
                lines.append(f'nvidia_smi_utilization_gpu{{host="{host}",index="{idx}",name="{name}"}} {g["util_percent"]}')

            lines.append('# HELP nvidia_smi_memory_used Telegraf collected metric')
            lines.append('# TYPE nvidia_smi_memory_used untyped')
            for g in data["gpus"]:
                idx = g["index"]
                name = g["name"]
                lines.append(f'nvidia_smi_memory_used{{host="{host}",index="{idx}",name="{name}"}} {int(g["vram_used_mb"])}')

            lines.append('# HELP nvidia_smi_memory_total Telegraf collected metric')
            lines.append('# TYPE nvidia_smi_memory_total untyped')
            for g in data["gpus"]:
                idx = g["index"]
                name = g["name"]
                lines.append(f'nvidia_smi_memory_total{{host="{host}",index="{idx}",name="{name}"}} {int(g["vram_total_mb"])}')

            lines.append('# HELP nvidia_smi_temperature_gpu Telegraf collected metric')
            lines.append('# TYPE nvidia_smi_temperature_gpu untyped')
            for g in data["gpus"]:
                idx = g["index"]
                name = g["name"]
                lines.append(f'nvidia_smi_temperature_gpu{{host="{host}",index="{idx}",name="{name}"}} {int(g["temp_c"])}')

            lines.append('# HELP nvidia_smi_power_draw Telegraf collected metric')
            lines.append('# TYPE nvidia_smi_power_draw untyped')
            for g in data["gpus"]:
                idx = g["index"]
                name = g["name"]
                lines.append(f'nvidia_smi_power_draw{{host="{host}",index="{idx}",name="{name}"}} {g["power_w"]}')

        lines.append('')
        return '\n'.join(lines)

    def get_telemetry_dict(self) -> dict:
        data = self.sample_hardware()
        return {
            "status": "online",
            "hostname": data["hostname"],
            "platform": platform.system(),
            "cpu": {
                "util_percent": data["cpu_avg"],
                "cores": len(data["cpu_cores"]),
                "physical_cpus": data["physical_cpus"]
            },
            "ram": {
                "total_gb": data["mem_total_gb"],
                "used_gb": data["mem_used_gb"],
                "free_gb": round(data["mem_total_gb"] - data["mem_used_gb"], 1)
            },
            "gpus": data["gpus"]
        }

    def start(self, host="0.0.0.0", port=None):
        if port is None:
            port = int(config.get("telemetry_port", DEFAULT_TELEMETRY_PORT))

        with self._lock:
            if self._server:
                return

            try:
                server = ThreadingHTTPServer((host, port), TelemetryHandler)
                server.telemetry_manager = self
                self._server = server
                self._thread = threading.Thread(target=server.serve_forever, name="LAASTelemetryServer", daemon=True)
                self._thread.start()
                log.info("LAAS Telegraf-compatible Telemetry Server listening on http://%s:%d/metrics", host, port)
            except Exception as e:
                log.warning("Could not start Telemetry Server on %s:%d: %s", host, port, e)

    def stop(self):
        with self._lock:
            if self._server:
                try:
                    self._server.shutdown()
                    self._server.server_close()
                except Exception:
                    pass
                self._server = None
                self._thread = None
                log.info("LAAS Telemetry Server stopped.")


telemetry_server = TelemetryServer()
