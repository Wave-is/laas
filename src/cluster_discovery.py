"""
LAN UDP Broadcast Auto-Discovery for LAAS Cluster Nodes.
Allows instances of Local Agent AI Station on the same local subnet to automatically
discover each other's presence, endpoints, and GPU capabilities without manual IP entry.
"""
import json
import logging
import socket
import threading
import time
from typing import Dict, List, Optional

from .paths import APP_NAME
from .version import VERSION

log = logging.getLogger(__name__)

DISCOVERY_PORT = 47150
BROADCAST_INTERVAL = 12.0
NODE_TTL = 45.0


def get_local_ips() -> List[str]:
    """Return all non-loopback IPv4 addresses of this machine."""
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith('127.') and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # Fallback: connect to dummy external address
    if not ips:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(('10.255.255.255', 1))
                ip = s.getsockname()[0]
                if ip and not ip.startswith('127.'):
                    ips.append(ip)
        except Exception:
            pass
    return ips or ['127.0.0.1']


class ClusterDiscovery:
    """Manages UDP broadcast beacons and listener for LAN node discovery."""

    def __init__(self, port: int = DISCOVERY_PORT):
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self.discovered_nodes: Dict[str, dict] = {}
        self.listener_thread: Optional[threading.Thread] = None
        self.beacon_thread: Optional[threading.Thread] = None
        self.local_ips = set(get_local_ips())

    def start(self):
        """Start listening for beacons and broadcasting own presence."""
        if self.sock is not None:
            return
        self.stop_event.clear()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.0)
            sock.bind(('', self.port))
            self.sock = sock
        except Exception as e:
            log.warning("Could not bind UDP discovery socket on port %s: %s", self.port, e)
            return

        self.listener_thread = threading.Thread(target=self._listen_loop, daemon=True, name="DiscoveryListener")
        self.listener_thread.start()

        self.beacon_thread = threading.Thread(target=self._beacon_loop, daemon=True, name="DiscoveryBeacon")
        self.beacon_thread.start()
        log.info("Cluster UDP discovery started on port %s", self.port)

    def stop(self):
        """Stop discovery threads and close socket."""
        self.stop_event.set()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _build_local_payload(self) -> dict:
        """Construct the broadcast announcement for this Station instance."""
        from .config import config
        from .hardware_topology import topology_engine
        from . import model_server

        hostname = socket.gethostname()
        lan_ip = self.local_ips.copy().pop() if self.local_ips else '127.0.0.1'
        
        # Read GPU info if available
        gpus = []
        try:
            top = topology_engine.current or topology_engine.discover_live()
            for d in top.devices:
                gpus.append({
                    "name": d.name.replace('NVIDIA ', '').replace('GeForce ', ''),
                    "vram_gb": round((d.vram_total_mib or 0) / 1024, 1),
                    "vendor": d.vendor
                })
        except Exception:
            pass

        # Check telemetry port
        tel_url = f"http://{lan_ip}:47050/metrics"

        # Check local model server
        srv_port = model_server.port()
        srv_url = f"http://{lan_ip}:{srv_port}"

        return {
            "magic": "LAAS_CLUSTER_BEACON",
            "version": VERSION,
            "hostname": hostname,
            "ip": lan_ip,
            "port": srv_port,
            "url": srv_url,
            "telemetry_url": tel_url,
            "type": "llama_swap",
            "gpus": gpus,
            "timestamp": time.time()
        }

    def broadcast_presence(self):
        """Send a single UDP broadcast beacon."""
        if not self.sock:
            return
        payload = self._build_local_payload()
        data = json.dumps(payload).encode('utf-8')
        try:
            self.sock.sendto(data, ('255.255.255.255', self.port))
        except Exception as e:
            log.debug("UDP broadcast send failed: %s", e)

    def probe(self):
        """Send a probe request soliciting immediate responses from all nodes."""
        if not self.sock:
            return
        probe_msg = json.dumps({
            "magic": "LAAS_CLUSTER_PROBE",
            "hostname": socket.gethostname(),
            "timestamp": time.time()
        }).encode('utf-8')
        try:
            self.sock.sendto(probe_msg, ('255.255.255.255', self.port))
            # Also announce ourselves in response
            self.broadcast_presence()
        except Exception as e:
            log.debug("UDP probe send failed: %s", e)

    def _listen_loop(self):
        while not self.stop_event.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
                msg = json.loads(data.decode('utf-8'))
            except (socket.timeout, OSError):
                continue
            except Exception:
                continue

            magic = msg.get("magic")
            if magic == "LAAS_CLUSTER_PROBE":
                # A peer asked who is on the network -> reply with presence
                sender_ip = addr[0]
                if sender_ip not in self.local_ips and sender_ip != '127.0.0.1':
                    try:
                        reply = json.dumps(self._build_local_payload()).encode('utf-8')
                        self.sock.sendto(reply, (sender_ip, self.port))
                    except Exception:
                        pass
                continue

            if magic == "LAAS_CLUSTER_BEACON":
                peer_ip = msg.get("ip") or addr[0]
                # If peer reported 127.0.0.1 or local IP, map to the real sender IP
                if peer_ip in ('127.0.0.1', 'localhost'):
                    peer_ip = addr[0]

                # Do not register ourselves as an external discovered node
                if peer_ip in self.local_ips and msg.get("hostname") == socket.gethostname():
                    continue

                key = f"{peer_ip}:{msg.get('port', 9292)}"
                msg["resolved_ip"] = peer_ip
                msg["last_seen"] = time.time()
                # If URL had 127.0.0.1, fix it to peer_ip
                if "url" in msg and ("127.0.0.1" in msg["url"] or "localhost" in msg["url"]):
                    msg["url"] = f"http://{peer_ip}:{msg.get('port', 9292)}"
                if "telemetry_url" in msg and ("127.0.0.1" in msg["telemetry_url"] or "localhost" in msg["telemetry_url"]):
                    msg["telemetry_url"] = f"http://{peer_ip}:47050/metrics"

                with self._lock:
                    self.discovered_nodes[key] = msg

    def _beacon_loop(self):
        # Initial burst
        self.broadcast_presence()
        while not self.stop_event.is_set():
            self.stop_event.wait(BROADCAST_INTERVAL)
            if not self.stop_event.is_set():
                self.broadcast_presence()

    def get_discovered_nodes(self) -> List[dict]:
        """Return fresh list of nodes discovered on the LAN within NODE_TTL."""
        now = time.time()
        with self._lock:
            active = []
            expired = []
            for key, node in self.discovered_nodes.items():
                last_seen = node.get("last_seen") or node.get("timestamp", 0)
                if now - last_seen <= NODE_TTL:
                    active.append(node)
                else:
                    expired.append(key)
            for k in expired:
                del self.discovered_nodes[k]
            return sorted(active, key=lambda n: (n.get("hostname", ""), n.get("ip", "")))


cluster_discovery = ClusterDiscovery()
