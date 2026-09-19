import json
import time
from src.cluster_discovery import ClusterDiscovery, get_local_ips


def test_cluster_discovery_payload():
    discovery = ClusterDiscovery(port=47155)
    payload = discovery._build_local_payload()
    assert payload["magic"] == "LAAS_CLUSTER_BEACON"
    assert "hostname" in payload
    assert "ip" in payload
    assert "port" in payload
    assert "url" in payload
    assert "type" in payload
    assert isinstance(payload["gpus"], list)


def test_cluster_discovery_message_handling():
    discovery = ClusterDiscovery(port=47156)
    # Simulate receiving a beacon from a peer node
    mock_beacon = {
        "magic": "LAAS_CLUSTER_BEACON",
        "version": "0.2.0-beta.7",
        "hostname": "RemoteWorker1",
        "ip": "192.168.1.150",
        "port": 9292,
        "url": "http://192.168.1.150:9292",
        "telemetry_url": "http://192.168.1.150:47050/metrics",
        "type": "llama_swap",
        "gpus": [{"name": "RTX 4090", "vram_gb": 24.0, "vendor": "NVIDIA"}],
        "timestamp": time.time()
    }
    key = "192.168.1.150:9292"
    discovery.discovered_nodes[key] = mock_beacon

    nodes = discovery.get_discovered_nodes()
    assert len(nodes) == 1
    assert nodes[0]["hostname"] == "RemoteWorker1"
    assert nodes[0]["ip"] == "192.168.1.150"
    assert nodes[0]["gpus"][0]["name"] == "RTX 4090"


def test_cluster_discovery_ttl_expiry():
    discovery = ClusterDiscovery(port=47157)
    # Simulate an expired beacon
    mock_expired = {
        "magic": "LAAS_CLUSTER_BEACON",
        "hostname": "OldNode",
        "ip": "192.168.1.200",
        "port": 9292,
        "last_seen": time.time() - 100.0  # Older than NODE_TTL (45s)
    }
    discovery.discovered_nodes["192.168.1.200:9292"] = mock_expired

    nodes = discovery.get_discovered_nodes()
    assert len(nodes) == 0
    assert "192.168.1.200:9292" not in discovery.discovered_nodes
