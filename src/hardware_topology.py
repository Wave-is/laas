"""Hardware facts and explicit simulation fixtures, with true maximal P2P cliques."""
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from .hardware import hardware, run_smi

@dataclass
class GpuDeviceInfo:
    index: int
    uuid: str
    pci_bus_id: str = ''
    dxgi_luid: Optional[str] = None
    name: str = 'Unknown GPU'
    vendor: str = 'NVIDIA'
    driver_mode: str = 'UNKNOWN'
    pending_driver_mode: str = 'UNKNOWN'
    is_tcc: bool = False
    tcc_supported: Optional[bool] = None
    display_active: Optional[bool] = None
    vram_total_mib: Optional[int] = None
    vram_free_mib: Optional[int] = None
    vram_used_mib: Optional[int] = None
    vram_used_pct: Optional[float] = None
    temp_c: Optional[int] = None
    load_percent: Optional[int] = None
    compute_capability: str = ''
    nvlink_peers: List[int] = field(default_factory=list)
    p2p_peers: List[int] = field(default_factory=list)
    def to_dict(self):
        return asdict(self)

@dataclass
class HardwareTopology:
    devices: List[GpuDeviceInfo] = field(default_factory=list)
    physical_nvlink_cliques: List[List[int]] = field(default_factory=list)
    cuda_p2p_cliques: List[List[int]] = field(default_factory=list)
    is_simulated: bool = False
    discovery_error: Optional[str] = None
    p2p_verified: bool = False
    nvlink_verified: bool = False
    @property
    def gpu_count(self):
        return len([d for d in self.devices if d.vendor != 'CPU'])
    def get_device_by_uuid(self, uuid):
        return next((d for d in self.devices if d.uuid.lower() == uuid.lower()), None)
    def get_device_by_index(self, idx):
        return next((d for d in self.devices if d.index == idx), None)
    def get_compute_devices(self):
        return [d for d in self.devices if d.vendor != 'CPU' and (d.is_tcc or d.display_active is False)]
    def get_graphics_devices(self):
        return [d for d in self.devices if d.driver_mode == 'WDDM']
    def is_all_tcc(self):
        return bool(self.devices) and all(d.is_tcc for d in self.devices)
    def is_p2p_active_between(self, a, b):
        da, db = self.get_device_by_index(a), self.get_device_by_index(b)
        return bool(da and db and b in da.p2p_peers and a in db.p2p_peers)
    def to_dict(self):
        return dict(asdict(self), gpu_count=self.gpu_count)

def parse_topology_matrix(output):
    output = re.sub(r'\x1b\[[0-9;]*m', '', output)
    columns = []
    pairs = {}
    for line in output.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        if not columns and tokens[0].startswith('GPU'):
            if all(re.fullmatch(r'GPU\d+', t) for t in tokens):
                columns = [int(t[3:]) for t in tokens]
            elif len(tokens) > 1 and tokens[1].startswith('GPU'):
                columns = [int(t[3:]) for t in tokens if re.fullmatch(r'GPU\d+', t)]
            continue
        if columns and re.fullmatch(r'GPU\d+', tokens[0]) and len(tokens) > len(columns):
            row = int(tokens[0][3:])
            for col, status in zip(columns, tokens[1:]):
                pairs[row, col] = status
    return pairs

class TopologyDiscoveryEngine:
    def __init__(self):
        self._probe_cache = None
        self._probe_at = 0
        self._signature = None
    def discover_live(self, force=False):
        rows = hardware.query_all_gpus(force)
        devices = [GpuDeviceInfo(**{k: v for k, v in row.items() if k in GpuDeviceInfo.__dataclass_fields__}) for row in rows]
        top = HardwareTopology(devices=devices, discovery_error=hardware.last_error)
        signature = tuple((d.uuid, d.driver_mode) for d in devices)
        if force or signature != self._signature or time.monotonic() - self._probe_at > 30:
            self._signature = signature
            self._probe_at = time.monotonic()
            probes = {}
            if sum(d.vendor == 'NVIDIA' for d in devices) > 1:
                for kind, args in [('read', ('topo', '-p2p', 'r')), ('write', ('topo', '-p2p', 'w')), ('nvlink', ('topo', '-m'))]:
                    try:
                        probes[kind] = parse_topology_matrix(run_smi(*args))
                    except Exception:
                        probes[kind] = {}
            self._probe_cache = probes
        probes = self._probe_cache or {}
        read, write, links = [probes.get(k, {}) for k in ('read', 'write', 'nvlink')]
        nvidia = [d for d in devices if d.vendor == 'NVIDIA']
        required_pairs = [(a.index, b.index) for a in nvidia for b in nvidia if a != b]
        top.p2p_verified = bool(required_pairs) and all(p in read and p in write for p in required_pairs)
        top.nvlink_verified = bool(required_pairs) and all(p in links for p in required_pairs)
        for d in devices:
            d.p2p_peers = [other.index for other in devices if other != d and
                read.get((d.index, other.index)) == 'OK' and write.get((d.index, other.index)) == 'OK']
            d.nvlink_peers = [other.index for other in devices if other != d and
                re.fullmatch(r'NV\d+', links.get((d.index, other.index), ''))]
        top.physical_nvlink_cliques = self._find_cliques(devices, lambda d: d.nvlink_peers)
        top.cuda_p2p_cliques = self._find_cliques(devices, lambda d: d.p2p_peers)
        return top
    def _cpu_fallback_topology(self):
        return HardwareTopology(devices=[], is_simulated=True)
    def _find_cliques(self, devices, peer_getter):
        indices = {d.index for d in devices}
        directed = {d.index: set(peer_getter(d)) & indices for d in devices}
        neighbors = {i: {j for j in directed[i] if i in directed[j] and i != j} for i in indices}
        cliques = []
        def visit(r, p, x):
            if not p and not x:
                if len(r) > 1:
                    cliques.append(sorted(r))
                return
            pivot = max(p | x, key=lambda i: len(neighbors[i] & p), default=None)
            for v in sorted(p - (neighbors[pivot] if pivot is not None else set())):
                visit(r | {v}, p & neighbors[v], x & neighbors[v])
                p.remove(v)
                x.add(v)
        visit(set(), set(indices), set())
        return sorted(cliques, key=lambda c: (-len(c), c))

    def create_simulated_topology(self, scenario: str) -> HardwareTopology:
        if scenario == "1_gpu_no_tcc":
            # Consumer GeForce RTX 4090 24GB
            dev = GpuDeviceInfo(
                index=0, uuid="GPU-SIM-4090-0", pci_bus_id="0000:01:00.0", name="NVIDIA GeForce RTX 4090",
                vendor="NVIDIA", driver_mode="WDDM", is_tcc=False, tcc_supported=False, display_active=True,
                vram_total_mib=24576, vram_free_mib=23000, vram_used_mib=1576, vram_used_pct=6.4,
                temp_c=40, load_percent=5
            )
            return HardwareTopology(devices=[dev], is_simulated=True)

        elif scenario == "1_gpu_with_tcc_display":
            # Single workstation GPU with display connected
            dev = GpuDeviceInfo(
                index=0, uuid="GPU-SIM-A5000-0", pci_bus_id="0000:01:00.0", name="NVIDIA RTX A5000",
                vendor="NVIDIA", driver_mode="WDDM", is_tcc=False, tcc_supported=True, display_active=True,
                vram_total_mib=24576, vram_free_mib=24000, vram_used_mib=576, vram_used_pct=2.3,
                temp_c=35, load_percent=0
            )
            return HardwareTopology(devices=[dev], is_simulated=True)

        elif scenario == "2_gpu_nvlink":
            # 2x RTX A5000 NVLink, displays on iGPU
            d0 = GpuDeviceInfo(
                index=0, uuid="GPU-00000000-0000-0000-0000-000000000001", pci_bus_id="0000:01:00.0", name="NVIDIA RTX A5000",
                vendor="NVIDIA", driver_mode="WDDM", is_tcc=False, tcc_supported=True, display_active=False,
                vram_total_mib=24576, vram_free_mib=24000, vram_used_mib=576, vram_used_pct=2.3,
                temp_c=33, load_percent=0, nvlink_peers=[1]
            )
            d1 = GpuDeviceInfo(
                index=1, uuid="GPU-00000000-0000-0000-0000-000000000002", pci_bus_id="0000:02:00.0", name="NVIDIA RTX A5000",
                vendor="NVIDIA", driver_mode="WDDM", is_tcc=False, tcc_supported=True, display_active=False,
                vram_total_mib=24576, vram_free_mib=24000, vram_used_mib=576, vram_used_pct=2.3,
                temp_c=34, load_percent=0, nvlink_peers=[0]
            )
            return HardwareTopology(devices=[d0, d1], physical_nvlink_cliques=[[0, 1]], is_simulated=True)

        elif scenario == "2_gpu_no_nvlink":
            # 2x RTX 4090 without NVLink
            d0 = GpuDeviceInfo(
                index=0, uuid="GPU-SIM-4090-0", pci_bus_id="0000:01:00.0", name="NVIDIA GeForce RTX 4090",
                vendor="NVIDIA", driver_mode="WDDM", is_tcc=False, tcc_supported=False, display_active=True,
                vram_total_mib=24576, vram_free_mib=23000, vram_used_mib=1576, vram_used_pct=6.4, temp_c=42
            )
            d1 = GpuDeviceInfo(
                index=1, uuid="GPU-SIM-4090-1", pci_bus_id="0000:02:00.0", name="NVIDIA GeForce RTX 4090",
                vendor="NVIDIA", driver_mode="WDDM", is_tcc=False, tcc_supported=False, display_active=False,
                vram_total_mib=24576, vram_free_mib=24000, vram_used_mib=576, vram_used_pct=2.3, temp_c=38
            )
            return HardwareTopology(devices=[d0, d1], is_simulated=True)

        elif scenario == "2_gpu_heterogeneous":
            # GPU 0: RTX 4090 24GB, GPU 1: RTX 3060 12GB
            d0 = GpuDeviceInfo(
                index=0, uuid="GPU-SIM-4090-0", pci_bus_id="0000:01:00.0", name="NVIDIA GeForce RTX 4090",
                vendor="NVIDIA", driver_mode="WDDM", is_tcc=False, tcc_supported=False, display_active=True,
                vram_total_mib=24576, vram_free_mib=23000, vram_used_mib=1576, vram_used_pct=6.4
            )
            d1 = GpuDeviceInfo(
                index=1, uuid="GPU-SIM-3060-0", pci_bus_id="0000:02:00.0", name="NVIDIA GeForce RTX 3060",
                vendor="NVIDIA", driver_mode="WDDM", is_tcc=False, tcc_supported=False, display_active=False,
                vram_total_mib=12288, vram_free_mib=11500, vram_used_mib=788, vram_used_pct=6.4
            )
            return HardwareTopology(devices=[d0, d1], is_simulated=True)

        elif scenario == "3_gpu_1_graphics_2_compute":
            # GPU 0: 3060 Display, GPU 1 & 2: A5000 in TCC with NVLink
            d0 = GpuDeviceInfo(
                index=0, uuid="GPU-SIM-3060-0", name="NVIDIA GeForce RTX 3060",
                vendor="NVIDIA", driver_mode="WDDM", is_tcc=False, tcc_supported=False, display_active=True,
                vram_total_mib=12288, vram_free_mib=11500, vram_used_mib=788
            )
            d1 = GpuDeviceInfo(
                index=1, uuid="GPU-SIM-A5000-0", name="NVIDIA RTX A5000",
                vendor="NVIDIA", driver_mode="TCC", is_tcc=True, tcc_supported=True, display_active=False,
                vram_total_mib=24576, vram_free_mib=24000, vram_used_mib=576, nvlink_peers=[2], p2p_peers=[2]
            )
            d2 = GpuDeviceInfo(
                index=2, uuid="GPU-SIM-A5000-1", name="NVIDIA RTX A5000",
                vendor="NVIDIA", driver_mode="TCC", is_tcc=True, tcc_supported=True, display_active=False,
                vram_total_mib=24576, vram_free_mib=24000, vram_used_mib=576, nvlink_peers=[1], p2p_peers=[1]
            )
            return HardwareTopology(
                devices=[d0, d1, d2],
                physical_nvlink_cliques=[[1, 2]],
                cuda_p2p_cliques=[[1, 2]],
                is_simulated=True
            )

        elif scenario == "3_gpu_nvlink_partial":
            # 3 GPUs, NVLink only between 1 and 2
            d0 = GpuDeviceInfo(index=0, uuid="GPU-SIM-A5000-0", name="NVIDIA RTX A5000", driver_mode="WDDM", is_tcc=False, tcc_supported=True, vram_total_mib=24576, vram_free_mib=24000)
            d1 = GpuDeviceInfo(index=1, uuid="GPU-SIM-A5000-1", name="NVIDIA RTX A5000", driver_mode="TCC", is_tcc=True, tcc_supported=True, vram_total_mib=24576, vram_free_mib=24000, nvlink_peers=[2], p2p_peers=[2])
            d2 = GpuDeviceInfo(index=2, uuid="GPU-SIM-A5000-2", name="NVIDIA RTX A5000", driver_mode="TCC", is_tcc=True, tcc_supported=True, vram_total_mib=24576, vram_free_mib=24000, nvlink_peers=[1], p2p_peers=[1])
            return HardwareTopology(devices=[d0, d1, d2], physical_nvlink_cliques=[[1, 2]], cuda_p2p_cliques=[[1, 2]], is_simulated=True)

        elif scenario == "4_gpu_cluster":
            devices = [
                GpuDeviceInfo(index=i, uuid=f"GPU-SIM-A5000-{i}", name="NVIDIA RTX A5000", driver_mode="TCC", is_tcc=True, tcc_supported=True, vram_total_mib=24576, vram_free_mib=24000)
                for i in range(4)
            ]
            return HardwareTopology(devices=devices, is_simulated=True)

        elif scenario == "amd_only":
            dev = GpuDeviceInfo(
                index=0, uuid="GPU-AMD-7900XTX", name="AMD Radeon RX 7900 XTX",
                vendor="AMD", driver_mode="WDDM", is_tcc=False, tcc_supported=False, display_active=True,
                vram_total_mib=24576, vram_free_mib=23500
            )
            return HardwareTopology(devices=[dev], is_simulated=True)

        elif scenario == "intel_only":
            dev = GpuDeviceInfo(
                index=0, uuid="GPU-INTEL-A770", name="Intel Arc A770 Graphics",
                vendor="Intel", driver_mode="WDDM", is_tcc=False, tcc_supported=False, display_active=True,
                vram_total_mib=16384, vram_free_mib=15500
            )
            return HardwareTopology(devices=[dev], is_simulated=True)

        elif scenario == "cpu_only":
            return self._cpu_fallback_topology()

        else:
            return self._cpu_fallback_topology()

topology_engine = TopologyDiscoveryEngine()
