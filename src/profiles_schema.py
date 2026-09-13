"""
profiles_schema.py
Data models and schemas for decoupled GPU Hardware Profiles, Model Profiles,
and Station Presets in Local Agent AI Station.
"""
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Any, Optional

class GpuDeviceRole(str, Enum):
    GRAPHICS = "graphics"      # Dedicated to display, desktop, gaming, video render
    COMPUTE = "compute"        # Dedicated to AI/LLM compute
    MIXED = "mixed"            # Available for both graphics and compute
    EXCLUDED = "excluded"      # Completely excluded from AI usage
    AUTO = "auto"              # Automatically assigned based on active display

class DriverModeTarget(str, Enum):
    WDDM = "WDDM"
    TCC = "TCC"
    UNCHANGED = "UNCHANGED"
    UNSUPPORTED = "UNSUPPORTED"

class GpuSelectionPolicy(str, Enum):
    AUTO = "auto"
    BEST_P2P_CLIQUE = "best_p2p_clique"
    ALL_COMPUTE_GPUS = "all_compute_gpus"
    LARGEST_VRAM_GPU = "largest_vram_gpu"
    ALL_SELECTED_GPUS = "all_selected_gpus"
    DEDICATED_COMPUTE_ONLY = "dedicated_compute_only"
    EXCLUDE_DISPLAY_GPU = "exclude_display_gpu"
    EXPLICIT_UUID_LIST = "explicit_uuid_list"

class CompatibilityStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    COMPATIBLE = "COMPATIBLE"
    COMPATIBLE_WITH_WARNING = "COMPATIBLE_WITH_WARNING"
    INCOMPATIBLE = "INCOMPATIBLE"

@dataclass
class GpuDeviceRule:
    gpu_stable_id: str                          # UUID or PCI ID or adapter LUID
    role: str = GpuDeviceRole.AUTO.value
    target_driver_mode: str = DriverModeTarget.UNCHANGED.value
    allow_directx: bool = True
    allow_compute: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'GpuDeviceRule':
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class GpuHardwareProfile:
    id: str
    name: str
    description: str = ""
    rules: List[GpuDeviceRule] = field(default_factory=list)
    expected_p2p: bool = False
    require_nvlink: bool = False
    kill_processes_before_switch: bool = False
    general_policy: str = "all_gpus"           # all_wddm, one_graphics_rest_compute, all_compute, custom
    included_devices: List[str] = field(default_factory=list)
    excluded_devices: List[str] = field(default_factory=list)
    preferred_p2p_groups: List[List[str]] = field(default_factory=list)
    prefer_p2p: bool = False
    require_p2p: bool = False
    allow_graphics: bool = True
    process_drain_policy: str = "owned_only"
    post_switch_probe: bool = True
    fallback_profile: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res['rules'] = [r.to_dict() if isinstance(r, GpuDeviceRule) else r for r in self.rules]
        return res

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'GpuHardwareProfile':
        d = d.copy()
        raw_rules = d.pop('rules', [])
        rules = [GpuDeviceRule.from_dict(r) if isinstance(r, dict) else r for r in raw_rules]
        return cls(rules=rules, **{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class ModelProfile:
    id: str
    name: str
    weights_path: str
    quant: str = ""
    mmproj_path: Optional[str] = None
    context: int = 131072
    mtp_depth: int = 0
    batch: int = 2048
    ubatch: int = 512
    kv_type: str = "f16"
    split_mode: str = "tensor"                  # tensor, layer, none
    tensor_split_policy: str = "auto"           # auto, "1,1", "1,1,1"
    gpu_selection_policy: str = GpuSelectionPolicy.ALL_COMPUTE_GPUS.value
    explicit_gpu_uuids: List[str] = field(default_factory=list)
    cpu_offload: bool = False
    gpu_layers: int = 99
    vision: bool = False
    min_gpu_count: int = 1
    min_total_vram_mib: int = 16384
    min_free_vram_per_gpu_mib: int = 8192
    prefer_p2p: bool = False
    require_p2p: bool = False                     # If False, runs over PCIe fallback with warning
    startup_key: str = ""                       # Key in llama-swap or custom startup cmd
    intended_use: str = ""
    status: str = "manual"
    provider_type: str = "llama_cpp"
    endpoint: str = "http://127.0.0.1:9292/v1"
    backend: str = "cuda"
    modalities: List[str] = field(default_factory=lambda: ['text'])
    tool_calling: bool = False
    reasoning: bool = False
    max_gpu_count: Optional[int] = None
    preferred_hardware_profiles: List[str] = field(default_factory=list)
    allowed_hardware_profiles: List[str] = field(default_factory=list)
    startup_timeout: int = 240
    health_check: str = "chat"
    tags: List[str] = field(default_factory=list)
    qualified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def backend_model_id(self):
        return self.id if self.provider_type == 'llama_cpp' else (self.startup_key or self.id)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'ModelProfile':
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class StationPreset:
    id: str
    name: str
    icon: str = "🚀"
    description: str = ""
    gpu_profile_id: str = "gpu-all-wddm"
    model_profile_id: str = "none"
    auto_start_model: bool = False
    warmup: bool = False
    stop_model_on_leave: bool = False
    require_confirmation: bool = False
    fallback_model_id: Optional[str] = None
    fallback_gpu_profile_id: Optional[str] = None
    is_builtin: bool = False
    primary_agent_runtime: str = "qwen-code"
    preferred_frontend: str = "qwen-terminal"
    background_agent_runtimes: List[str] = field(default_factory=list)
    auto_start_agents: bool = False
    confirmation_policy: str = "hardware_changes"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'StationPreset':
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
