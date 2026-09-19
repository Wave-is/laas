"""
Cluster Manager for LAAS.
Discovers, polls, and monitors distributed LLM inference nodes over HTTP.
Thread-safe background sampler with rolling history for UI charts.
"""
from collections import deque
from copy import deepcopy
import json
import logging
import os
import platform
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from .config import config

log = logging.getLogger(__name__)

HISTORY_MAX = 60  # ~2-3 minutes of timeline data

DEFAULT_NODES = [
    {
        "id": "renderpc-local",
        "name": "RenderPC (ComfyUI / RTX 3060)",
        "url": "http://127.0.0.1:8188",
        "telemetry_url": "http://127.0.0.1:9273/metrics",
        "type": "comfyui",
        "enabled": True,
        "notes": "Image Generation Worker • RTX 3060 12GB • FLUX & Z-Image"
    },
    {
        "id": "ai-station",
        "name": "AI Station (2× A5000)",
        "url": "http://192.168.1.100:9292",
        "type": "llama_swap",
        "enabled": True,
        "notes": "Cluster Primary • 2× RTX A5000 NVLink 48GB • MTP3"
    },
    {
        "id": "wavevm",
        "name": "WaveVM (A4000)",
        "url": "http://192.168.1.101:8080",
        "type": "llama_server",
        "enabled": True,
        "notes": "Local VM • RTX A4000 16GB • Qwen3.8-27B 64K"
    },
    {
        "id": "remote-worker",
        "name": "Remote Worker (Example)",
        "url": "http://192.0.2.1:8080",
        "telemetry_url": "http://192.0.2.1:9273/metrics",
        "type": "llama_server",
        "enabled": True,
        "notes": "Remote Worker • RTX A4000 16GB • Telegraf Prometheus"
    }
]


class ClusterManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._nodes = []
        self._snapshots = {}  # node_id -> latest node state dict
        self._history = {
            "timestamps": deque(maxlen=HISTORY_MAX),
            "node_gpu_utils": {},  # node_id -> deque
        }
        self.load_nodes()

    def load_nodes(self):
        with self._lock:
            saved = config.get("cluster_nodes")
            if isinstance(saved, list) and len(saved) > 0:
                self._nodes = deepcopy(saved)
                # Auto-migrate legacy renderpc-local node
                for n in self._nodes:
                    if n.get("id") == "renderpc-local":
                        if n.get("url") == "http://127.0.0.1:8888" or n.get("type") == "local":
                            n["url"] = "http://127.0.0.1:8188"
                            n["type"] = "comfyui"
                            n["name"] = "RenderPC (ComfyUI / RTX 3060)"
                            n["notes"] = "Image Generation Worker • RTX 3060 12GB • FLUX & Z-Image"
                        if not n.get("telemetry_url"):
                            n["telemetry_url"] = "http://127.0.0.1:9273/metrics"
                config.set("cluster_nodes", self._nodes)
            else:
                self._nodes = deepcopy(DEFAULT_NODES)
                config.set("cluster_nodes", self._nodes)

            # Initialize history deques for each node
            for n in self._nodes:
                nid = n["id"]
                if nid not in self._history["node_gpu_utils"]:
                    self._history["node_gpu_utils"][nid] = deque(maxlen=HISTORY_MAX)

    def save_nodes(self):
        with self._lock:
            config.set("cluster_nodes", self._nodes)

    def get_nodes(self):
        with self._lock:
            return deepcopy(self._nodes)

    def add_node(self, node_data):
        with self._lock:
            nid = node_data.get("id") or f"node-{int(time.time())}"
            node_data["id"] = nid
            node_data.setdefault("enabled", True)
            self._nodes.append(node_data)
            self._history["node_gpu_utils"][nid] = deque(maxlen=HISTORY_MAX)
            self.save_nodes()
        if node_data.get("type") == "comfyui":
            try:
                from .skill_distributor import skill_distributor
                skill_distributor.deploy(server_url=node_data.get("url"))
            except Exception as ex:
                log.debug("Auto-deploy comfyui skill failed in add_node: %s", ex)
        elif node_data.get("type") in ("llama_server", "llama_swap"):
            try:
                from .skill_distributor import skill_distributor
                skill_distributor.sync_models_to_agents()
            except Exception as ex:
                log.debug("Auto-sync models failed in add_node: %s", ex)
        return nid

    def update_node(self, node_id, new_data):
        updated = False
        with self._lock:
            for i, n in enumerate(self._nodes):
                if n["id"] == node_id:
                    new_data["id"] = node_id
                    self._nodes[i] = new_data
                    self.save_nodes()
                    updated = True
                    break
        if updated:
            if new_data.get("type") == "comfyui":
                try:
                    from .skill_distributor import skill_distributor
                    skill_distributor.deploy(server_url=new_data.get("url"))
                except Exception as ex:
                    log.debug("Auto-deploy comfyui skill failed in update_node: %s", ex)
            elif new_data.get("type") in ("llama_server", "llama_swap"):
                try:
                    from .skill_distributor import skill_distributor
                    skill_distributor.sync_models_to_agents()
                except Exception as ex:
                    log.debug("Auto-sync models failed in update_node: %s", ex)
        return updated

    def remove_node(self, node_id):
        with self._lock:
            self._nodes = [n for n in self._nodes if n["id"] != node_id]
            self._snapshots.pop(node_id, None)
            self._history["node_gpu_utils"].pop(node_id, None)
            self.save_nodes()

    def export_nodes_xml(self, filepath=None) -> str:
        with self._lock:
            root = ET.Element("laas-cluster", version="1.0")
            nodes_el = ET.SubElement(root, "nodes")
            for n in self._nodes:
                node_el = ET.SubElement(nodes_el, "node", id=str(n.get("id", "")), enabled=str(n.get("enabled", True)).lower())
                name_el = ET.SubElement(node_el, "name")
                name_el.text = str(n.get("name", ""))
                type_el = ET.SubElement(node_el, "type")
                type_el.text = str(n.get("type", "llama_server"))
                url_el = ET.SubElement(node_el, "url")
                url_el.text = str(n.get("url", ""))
                tel_el = ET.SubElement(node_el, "telemetry_url")
                tel_el.text = str(n.get("telemetry_url", ""))
                notes_el = ET.SubElement(node_el, "notes")
                notes_el.text = str(n.get("notes", ""))

            # Export services
            try:
                from .shared_services import SharedServices
                ss = SharedServices()
                profs = ss.profiles()
                if profs:
                    services_el = ET.SubElement(root, "services")
                    for sid, sp in profs.items():
                        s_el = ET.SubElement(services_el, "service", id=str(sid), enabled="true")
                        name_el = ET.SubElement(s_el, "name")
                        name_el.text = str(sp.get("name", ""))
                        kind_el = ET.SubElement(s_el, "kind")
                        kind_el.text = str(sp.get("kind", "comfyui"))
                        url_el = ET.SubElement(s_el, "url")
                        url_el.text = str(sp.get("url", ""))
                        h_el = ET.SubElement(s_el, "health_url")
                        h_el.text = str(sp.get("health_url", ""))
                        if sp.get("executable"):
                            ET.SubElement(s_el, "executable").text = str(sp.get("executable", ""))
                        if sp.get("working_directory"):
                            ET.SubElement(s_el, "working_directory").text = str(sp.get("working_directory", ""))
                        if sp.get("arguments"):
                            args_el = ET.SubElement(s_el, "arguments")
                            for a in sp["arguments"]:
                                ET.SubElement(args_el, "arg").text = str(a)
            except Exception as e:
                log.debug("Could not export services to XML: %s", e)

            tree = ET.ElementTree(root)
            ET.indent(tree, space="  ", level=0)
            xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            xml_str = xml_bytes.decode("utf-8")
            if filepath:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(xml_str)
            return xml_str

    def import_nodes_xml(self, xml_source, merge=True) -> int:
        try:
            if isinstance(xml_source, str) and (xml_source.endswith(".xml") or (os.path.exists(xml_source) and not xml_source.strip().startswith("<"))):
                tree = ET.parse(xml_source)
                root = tree.getroot()
            else:
                root = ET.fromstring(xml_source)
        except Exception as e:
            raise ValueError(f"Failed to parse XML: {e}")

        if root.tag != "laas-cluster":
            raise ValueError(f"Invalid root tag: <{root.tag}>, expected <laas-cluster>")

        nodes_el = root.find("nodes")
        if nodes_el is None:
            raise ValueError("Missing <nodes> container in XML")

        imported_nodes = []
        for n_el in nodes_el.findall("node"):
            nid = n_el.get("id") or f"node-{int(time.time())}"
            enabled_str = (n_el.get("enabled") or "true").lower()
            enabled = enabled_str in ("true", "1", "yes")

            name = (n_el.findtext("name") or nid).strip()
            ntype = (n_el.findtext("type") or "llama_server").strip()
            url = (n_el.findtext("url") or "").strip()
            tel_url = (n_el.findtext("telemetry_url") or "").strip()
            notes = (n_el.findtext("notes") or "").strip()

            if not url:
                continue

            imported_nodes.append({
                "id": nid,
                "name": name,
                "type": ntype,
                "url": url,
                "telemetry_url": tel_url,
                "enabled": enabled,
                "notes": notes
            })

        # Import services if present
        services_el = root.find("services")
        if services_el is not None:
            try:
                from .shared_services import SharedServices
                from .service_profiles import profile_from_form
                ss = SharedServices()
                existing_profs = ss.profiles()
                for s_el in services_el.findall("service"):
                    sid = s_el.get("id") or f"service-{int(time.time())}"
                    sname = (s_el.findtext("name") or sid).strip()
                    skind = (s_el.findtext("kind") or "comfyui").strip()
                    surl = (s_el.findtext("url") or "").strip()
                    shealth = (s_el.findtext("health_url") or "").strip()
                    exec_path = (s_el.findtext("executable") or "").strip()
                    work_dir = (s_el.findtext("working_directory") or "").strip()
                    args = []
                    args_el = s_el.find("arguments")
                    if args_el is not None:
                        args = [a.text for a in args_el.findall("arg") if a.text]

                    loc = "local" if exec_path and os.path.isfile(exec_path) else "remote"
                    args_str = "\n".join(args) if loc == "local" else ""

                    if surl:
                        try:
                            p = profile_from_form(
                                original=existing_profs.get(sid),
                                name=sname,
                                location=loc,
                                kind=skind,
                                url=surl,
                                health_url=shealth,
                                executable=exec_path if loc == "local" else "",
                                working_directory=work_dir if loc == "local" else "",
                                arguments=args_str,
                                monitor=True,
                                restart=(loc == "local")
                            )
                            p["id"] = sid
                            _, exp = ss.edit_snapshot(sid)
                            ss.save(p, exp)
                        except Exception as ex:
                            log.debug("Could not import service %s: %s", sid, ex)
            except Exception as ex:
                log.debug("Error importing services: %s", ex)

        if not imported_nodes:
            return 0

        with self._lock:
            if not merge:
                self._nodes = []
                self._snapshots.clear()
                self._history["node_gpu_utils"].clear()

            existing_by_id = {n["id"]: i for i, n in enumerate(self._nodes)}
            existing_by_url = {n["url"]: i for i, n in enumerate(self._nodes)}

            count = 0
            for imp in imported_nodes:
                idx = existing_by_id.get(imp["id"])
                if idx is None:
                    idx = existing_by_url.get(imp["url"])

                if idx is not None:
                    self._nodes[idx].update(imp)
                else:
                    self._nodes.append(imp)
                    existing_by_id[imp["id"]] = len(self._nodes) - 1
                    existing_by_url[imp["url"]] = len(self._nodes) - 1

                nid = imp["id"]
                if nid not in self._history["node_gpu_utils"]:
                    self._history["node_gpu_utils"][nid] = deque(maxlen=HISTORY_MAX)
                count += 1

            self.save_nodes()
            try:
                for n in imported_nodes:
                    if n.get("type") == "comfyui":
                        from .skill_distributor import skill_distributor
                        skill_distributor.deploy(server_url=n.get("url"))
                        break
            except Exception as ex:
                log.debug("Auto-deploy comfyui skill failed in import_nodes_xml: %s", ex)
            try:
                if any(n.get("type") in ("llama_server", "llama_swap") for n in imported_nodes):
                    from .skill_distributor import skill_distributor
                    skill_distributor.sync_models_to_agents()
            except Exception as ex:
                log.debug("Auto-sync models failed in import_nodes_xml: %s", ex)
            return count

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="ClusterSampler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                self._sample_all_nodes()
            except Exception as e:
                log.exception("Cluster sample loop error: %s", e)
            poll_sec = max(1.0, float(config.get("cluster_poll_interval_sec", 2.0)))
            self._stop_event.wait(poll_sec)

    def _sample_all_nodes(self):
        with self._lock:
            nodes_to_poll = [deepcopy(n) for n in self._nodes if n.get("enabled", True)]

        results = {}
        threads = []

        def worker(node):
            results[node["id"]] = self._poll_single_node(node)

        for n in nodes_to_poll:
            t = threading.Thread(target=worker, args=(n,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=3.0)

        now_str = time.strftime("%H:%M:%S")

        with self._lock:
            self._history["timestamps"].append(now_str)
            for nid, snap in results.items():
                self._snapshots[nid] = snap
                gpus = snap.get("gpus", [])
                avg_util = 0.0
                if gpus:
                    utils = [g.get("util_percent", 0.0) for g in gpus if "util_percent" in g]
                    avg_util = round(sum(utils) / len(utils), 1) if utils else 0.0
                if nid in self._history["node_gpu_utils"]:
                    self._history["node_gpu_utils"][nid].append(avg_util)

    def _poll_single_node(self, node):
        ntype = node.get("type", "llama_server")
        url = (node.get("url") or "").rstrip("/")
        telemetry_url = (node.get("telemetry_url") or "").rstrip("/")
        t0 = time.time()

        if ntype == "comfyui" or ":8188" in url:
            return self._poll_comfyui(node, t0)
        elif ntype == "local":
            return self._poll_local_renderpc(node, t0)
        elif ntype == "llama_swap":
            return self._poll_llama_swap(url, t0)
        elif ntype == "laas_exporter":
            target = telemetry_url or url
            return self._poll_laas_exporter(target, t0)
        else:
            # Default llama_server with optional telemetry_url
            return self._poll_llama_server(url, telemetry_url, t0)

    def _poll_local_renderpc(self, node, t0):
        # RenderPC local GPU via nvidia-smi
        gpus = []
        try:
            cmd = ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw", "--format=csv,noheader,nounits"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 7:
                        gpus.append({
                            "index": int(parts[0]) if parts[0].isdigit() else 0,
                            "name": parts[1],
                            "util_percent": float(parts[2]),
                            "vram_used_gb": round(float(parts[3]) / 1024.0, 2),
                            "vram_total_gb": round(float(parts[4]) / 1024.0, 2),
                            "temp_c": float(parts[5]),
                            "power_w": float(parts[6])
                        })
        except Exception:
            pass

        # If local monitor service is running on 8888, fetch inference/additional data
        inf = {"online": True, "is_processing": False}
        return {
            "status": "online",
            "latency_ms": round((time.time() - t0) * 1000),
            "ip": "127.0.0.1 (WireGuard)",
            "role": "Agent Host / Workstation",
            "gpus": gpus,
            "inference": inf
        }

    def _poll_llama_swap(self, base_url, t0):
        # Queries Prometheus metrics from llama-swap
        url = f"{base_url}/metrics?model=qwen3.8-27b-production"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "laas-cluster"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                text = resp.read().decode("utf-8", errors="ignore")

            gpu0 = {"name": "NVIDIA RTX A5000 #0", "vram_total_gb": 24.0}
            gpu1 = {"name": "NVIDIA RTX A5000 #1", "vram_total_gb": 24.0}
            cpu_utils = []
            mem_total, mem_used = 0, 0

            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("llamaswap_cpu_util_percent"):
                    try:
                        cpu_utils.append(float(line.split()[-1]))
                    except:
                        pass
                elif line.startswith("llamaswap_memory_total_bytes"):
                    try:
                        mem_total = float(line.split()[-1])
                    except:
                        pass
                elif line.startswith("llamaswap_memory_used_bytes"):
                    try:
                        mem_used = float(line.split()[-1])
                    except:
                        pass
                elif 'id="0"' in line:
                    val = float(line.split()[-1])
                    if "gpu_util_percent" in line:
                        gpu0["util_percent"] = round(val, 1)
                    elif "gpu_temperature_celsius" in line:
                        gpu0["temp_c"] = round(val, 1)
                    elif "gpu_power_draw_watts" in line:
                        gpu0["power_w"] = round(val, 1)
                    elif "gpu_memory_used_bytes" in line:
                        gpu0["vram_used_gb"] = round(val / (1024**3), 2)
                elif 'id="1"' in line:
                    val = float(line.split()[-1])
                    if "gpu_util_percent" in line:
                        gpu1["util_percent"] = round(val, 1)
                    elif "gpu_temperature_celsius" in line:
                        gpu1["temp_c"] = round(val, 1)
                    elif "gpu_power_draw_watts" in line:
                        gpu1["power_w"] = round(val, 1)
                    elif "gpu_memory_used_bytes" in line:
                        gpu1["vram_used_gb"] = round(val / (1024**3), 2)

            is_active = (gpu0.get("util_percent", 0) > 10 or gpu1.get("util_percent", 0) > 10)

            return {
                "status": "online",
                "latency_ms": round((time.time() - t0) * 1000),
                "model": "qwen3.8-27b-production (256K, MTP3)",
                "gpus": [gpu0, gpu1],
                "cpu_util": round(sum(cpu_utils) / len(cpu_utils), 1) if cpu_utils else 0.0,
                "ram_used_gb": round(mem_used / (1024**3), 1) if mem_total else 0.0,
                "ram_total_gb": round(mem_total / (1024**3), 1) if mem_total else 0.0,
                "inference": {
                    "online": True,
                    "is_processing": is_active,
                    "n_ctx": 262144
                }
            }
        except Exception as e:
            return {"status": "offline", "error": str(e), "latency_ms": round((time.time() - t0) * 1000)}

    def _poll_laas_exporter(self, target_url, t0):
        # Pure HTTP telemetry endpoint from laas-node-exporter.py
        endpoint = f"{target_url}/api/telemetry" if not target_url.endswith("/api/telemetry") else target_url
        try:
            req = urllib.request.Request(endpoint, headers={"User-Agent": "laas-cluster"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            data["latency_ms"] = round((time.time() - t0) * 1000)
            return data
        except Exception as e:
            return {"status": "offline", "error": str(e), "latency_ms": round((time.time() - t0) * 1000)}

    def _poll_llama_server(self, base_url, telemetry_url, t0):
        # Queries /slots from llama-server
        slots_url = f"{base_url}/slots"
        slots = []
        try:
            req = urllib.request.Request(slots_url, headers={"User-Agent": "laas-cluster"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                slots = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            # If slots fails, test /health
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=1.5) as r:
                    pass
            except:
                return {"status": "offline", "error": str(e), "latency_ms": round((time.time() - t0) * 1000)}

        slot = slots[0] if slots else {}
        is_processing = slot.get("is_processing", False)
        n_ctx = slot.get("n_ctx", 65536)
        prompt_tokens = slot.get("n_prompt_tokens", 0)
        prompt_cached = slot.get("n_prompt_tokens_cache", 0)
        next_tok = slot.get("next_token", [{}])[0] if slot.get("next_token") else {}
        decoded_tokens = next_tok.get("n_decoded", 0)
        remain_tokens = next_tok.get("n_remain", 0)

        gpu_info = {"name": "NVIDIA RTX A4000", "vram_total_gb": 16.0}
        cpu_util = None
        ram_used = None
        ram_total = None

        # Telemetry: query Telegraf Prometheus or JSON Exporter
        if telemetry_url:
            tel_data = self._fetch_telemetry_metrics(telemetry_url)
            if tel_data.get("gpus"):
                gpu_info = tel_data["gpus"][0]
            if tel_data.get("cpu_util") is not None:
                cpu_util = tel_data["cpu_util"]
            if tel_data.get("ram_used_gb") is not None:
                ram_used = tel_data["ram_used_gb"]
            if tel_data.get("ram_total_gb") is not None:
                ram_total = tel_data["ram_total_gb"]

        result = {
            "status": "online",
            "latency_ms": round((time.time() - t0) * 1000),
            "gpus": [gpu_info],
            "inference": {
                "online": True,
                "is_processing": is_processing,
                "n_ctx": n_ctx,
                "prompt_tokens": prompt_tokens,
                "prompt_cached": prompt_cached,
                "decoded_tokens": decoded_tokens,
                "remain_tokens": remain_tokens,
            }
        }
        if cpu_util is not None:
            result["cpu_util"] = cpu_util
        if ram_used is not None:
            result["ram_used_gb"] = ram_used
        if ram_total is not None:
            result["ram_total_gb"] = ram_total
        return result

    def _parse_telegraf_prometheus(self, raw_text: str) -> dict:
        gpus_by_idx = {}
        cpus = []
        mem_used = None
        mem_total = None

        idx_re = re.compile(r'index="(\d+)"')
        name_re = re.compile(r'name="([^"]+)"')

        for line in raw_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.rsplit(None, 1)
            if len(parts) != 2:
                continue
            metric_part, val_str = parts
            try:
                val = float(val_str)
            except ValueError:
                continue

            if "nvidia_smi_" in metric_part:
                m_idx = idx_re.search(metric_part)
                idx = int(m_idx.group(1)) if m_idx else 0
                if idx not in gpus_by_idx:
                    m_name = name_re.search(metric_part)
                    gpu_name = m_name.group(1) if m_name else f"GPU #{idx}"
                    gpus_by_idx[idx] = {
                        "index": idx,
                        "name": gpu_name,
                        "util_percent": 0.0,
                        "vram_used_gb": 0.0,
                        "vram_total_gb": 0.0,
                        "temp_c": 0.0,
                        "power_w": 0.0,
                    }
                g = gpus_by_idx[idx]
                if metric_part.startswith("nvidia_smi_utilization_gpu"):
                    g["util_percent"] = round(val, 1)
                elif metric_part.startswith("nvidia_smi_temperature_gpu"):
                    g["temp_c"] = round(val, 1)
                elif metric_part.startswith("nvidia_smi_power_draw"):
                    g["power_w"] = round(val, 1)
                elif metric_part.startswith("nvidia_smi_memory_used"):
                    if val > 1_000_000:
                        g["vram_used_gb"] = round(val / (1024**3), 2)
                    else:
                        g["vram_used_gb"] = round(val / 1024.0, 2)
                elif metric_part.startswith("nvidia_smi_memory_total"):
                    if val > 1_000_000:
                        g["vram_total_gb"] = round(val / (1024**3), 2)
                    else:
                        g["vram_total_gb"] = round(val / 1024.0, 2)
            elif metric_part.startswith("cpu_usage_active"):
                cpus.append(val)
            elif metric_part.startswith("mem_used"):
                mem_used = round(val / (1024**3), 1)
            elif metric_part.startswith("mem_total"):
                mem_total = round(val / (1024**3), 1)

        res = {}
        if gpus_by_idx:
            res["gpus"] = [gpus_by_idx[k] for k in sorted(gpus_by_idx.keys())]
        if cpus:
            res["cpu_util"] = round(sum(cpus) / len(cpus), 1)
        if mem_used is not None:
            res["ram_used_gb"] = mem_used
        if mem_total is not None:
            res["ram_total_gb"] = mem_total
        return res

    def _fetch_telemetry_metrics(self, telemetry_url: str) -> dict:
        if not telemetry_url:
            return {}
        try:
            t_endpoint = telemetry_url
            if ":9273" in telemetry_url or telemetry_url.endswith("/metrics"):
                if not t_endpoint.endswith("/metrics"):
                    t_endpoint = f"{t_endpoint}/metrics"
                req_t = urllib.request.Request(t_endpoint, headers={"User-Agent": "laas-cluster"})
                with urllib.request.urlopen(req_t, timeout=1.5) as resp_t:
                    raw = resp_t.read().decode("utf-8")
                return self._parse_telegraf_prometheus(raw)
            else:
                ep = f"{t_endpoint}/api/telemetry" if not t_endpoint.endswith("/api/telemetry") else t_endpoint
                req_t = urllib.request.Request(ep, headers={"User-Agent": "laas-cluster"})
                with urllib.request.urlopen(req_t, timeout=1.5) as resp_t:
                    t_data = json.loads(resp_t.read().decode("utf-8"))
                res = {}
                if t_data.get("gpus"):
                    res["gpus"] = t_data["gpus"]
                if t_data.get("cpu"):
                    res["cpu_util"] = t_data["cpu"].get("util_percent")
                if t_data.get("ram"):
                    res["ram_used_gb"] = t_data["ram"].get("used_gb")
                    res["ram_total_gb"] = t_data["ram"].get("total_gb")
                return res
        except Exception as e:
            log.debug("Telemetry fetch error from %s: %s", telemetry_url, e)
            return {}

    def _poll_comfyui(self, node: dict, t0: float) -> dict:
        url = (node.get("url") or "").rstrip("/")
        telemetry_url = (node.get("telemetry_url") or "").rstrip("/")

        # 1. Query ComfyUI /prompt endpoint for queue status
        queue_remaining = 0
        comfy_online = False
        try:
            prompt_req = urllib.request.Request(f"{url}/prompt", headers={"User-Agent": "laas-cluster"})
            with urllib.request.urlopen(prompt_req, timeout=2.0) as resp:
                p_data = json.loads(resp.read().decode("utf-8"))
                exec_info = p_data.get("exec_info", {})
                queue_remaining = int(exec_info.get("queue_remaining", 0))
                comfy_online = True
        except Exception:
            # Fallback health check: /system_stats
            try:
                stat_req = urllib.request.Request(f"{url}/system_stats", headers={"User-Agent": "laas-cluster"})
                with urllib.request.urlopen(stat_req, timeout=1.5) as resp:
                    comfy_online = True
            except Exception:
                pass

        if not comfy_online:
            return {"status": "offline", "latency_ms": round((time.time() - t0) * 1000)}

        # 2. Hardware telemetry (Telegraf Prometheus / LAAS Telemetry Server)
        tel_data = self._fetch_telemetry_metrics(telemetry_url) if telemetry_url else {}
        gpus = tel_data.get("gpus", [])

        # Fallback 1: ComfyUI /system_stats devices list
        if not gpus:
            try:
                stat_req = urllib.request.Request(f"{url}/system_stats", headers={"User-Agent": "laas-cluster"})
                with urllib.request.urlopen(stat_req, timeout=1.5) as resp:
                    s_data = json.loads(resp.read().decode("utf-8"))
                    devices = s_data.get("devices", [])
                    for d in devices:
                        if d.get("type") == "cuda":
                            v_tot = round(d.get("vram_total", 0) / (1024**3), 2)
                            v_free = round(d.get("vram_free", 0) / (1024**3), 2)
                            v_used = round(max(0.0, v_tot - v_free), 2)
                            raw_name = d.get("name", "NVIDIA GPU")
                            clean_name = raw_name.split(" : ")[0].replace("cuda:0 ", "").strip()
                            gpus.append({
                                "index": d.get("index", 0),
                                "name": clean_name,
                                "util_percent": 100.0 if queue_remaining > 0 else 0.0,
                                "vram_used_gb": v_used,
                                "vram_total_gb": v_tot,
                                "temp_c": 0.0,
                                "power_w": 0.0
                            })
            except Exception:
                pass

        # Fallback 2: Local nvidia-smi if local host
        if not gpus and any(h in url for h in ("127.0.0.1", "localhost", "127.0.0.1")):
            try:
                cmd = ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw", "--format=csv,noheader,nounits"]
                cflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5, creationflags=cflags)
                if res.returncode == 0:
                    for line in res.stdout.strip().splitlines():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 7:
                            gpus.append({
                                "index": int(parts[0]) if parts[0].isdigit() else 0,
                                "name": parts[1],
                                "util_percent": float(parts[2]),
                                "vram_used_gb": round(float(parts[3]) / 1024.0, 2),
                                "vram_total_gb": round(float(parts[4]) / 1024.0, 2),
                                "temp_c": float(parts[5]),
                                "power_w": float(parts[6])
                            })
            except Exception:
                pass

        if not gpus:
            gpus = [{
                "name": "NVIDIA GeForce RTX 3060",
                "util_percent": 100.0 if queue_remaining > 0 else 0.0,
                "vram_used_gb": 0.0,
                "vram_total_gb": 12.0
            }]

        is_processing = queue_remaining > 0

        res = {
            "status": "online",
            "latency_ms": round((time.time() - t0) * 1000),
            "gpus": gpus,
            "inference": {
                "online": True,
                "type": "comfyui",
                "is_processing": is_processing,
                "queue_remaining": queue_remaining
            }
        }
        if tel_data.get("cpu_util") is not None:
            res["cpu_util"] = tel_data["cpu_util"]
        if tel_data.get("ram_used_gb") is not None:
            res["ram_used_gb"] = tel_data["ram_used_gb"]
        if tel_data.get("ram_total_gb") is not None:
            res["ram_total_gb"] = tel_data["ram_total_gb"]
        return res

    def get_snapshot(self):
        with self._lock:
            snaps = deepcopy(self._snapshots)
            hist = {
                "timestamps": list(self._history["timestamps"]),
                "node_gpu_utils": {nid: list(pts) for nid, pts in self._history["node_gpu_utils"].items()}
            }
            nodes = deepcopy(self._nodes)

        # Calculate totals
        total_gpus = 0
        total_vram_gb = 0.0
        active_inferences = 0
        online_nodes = 0

        for nid, s in snaps.items():
            if s.get("status") == "online":
                online_nodes += 1
                gpus = s.get("gpus", [])
                total_gpus += len(gpus)
                for g in gpus:
                    total_vram_gb += float(g.get("vram_total_gb", 0.0))
                inf = s.get("inference", {})
                if inf.get("is_processing"):
                    active_inferences += 1

        summary = {
            "total_nodes": len(nodes),
            "online_nodes": online_nodes,
            "total_gpus": total_gpus,
            "total_vram_gb": round(total_vram_gb, 1),
            "active_inferences": active_inferences
        }

        return {
            "nodes": nodes,
            "snapshots": snaps,
            "summary": summary,
            "history": hist
        }

    def test_node(self, node_data):
        return self._poll_single_node(node_data)


# Global singleton instance
cluster_manager = ClusterManager()
