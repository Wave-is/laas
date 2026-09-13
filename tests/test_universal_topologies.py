"""
test_universal_topologies.py
Comprehensive automated test suite verifying universal 1 to N GPU hardware handling,
heterogeneous GPUs, non-NVIDIA devices, and decoupled compatibility evaluation.
"""
import pytest
from src.hardware_topology import topology_engine, HardwareTopology, GpuDeviceInfo
from src.profile_storage import get_default_model_profiles

class TestRegistry:
    @staticmethod
    def get_model_profile(id):
        if id == "none":
            return get_default_model_profiles()[0]
        return ModelProfile(id=id, name="Synthetic test model", weights_path="synthetic.gguf",
            min_gpu_count=2 if id == "qwen3.8-27b-production" else 1,
            min_total_vram_mib=40960 if id == "qwen3.8-27b-production" else 8192,
            gpu_selection_policy="all_compute_gpus" if id == "qwen3.8-27b-production" else "largest_vram_gpu",
            status="stable", qualified=True)
profile_storage = TestRegistry()
from src.profiles_schema import ModelProfile, GpuHardwareProfile, CompatibilityStatus, GpuSelectionPolicy
from src.compatibility import compatibility_evaluator

def test_scenario_1_gpu_no_tcc():
    """1 GPU NVIDIA (GeForce RTX 4090), TCC unsupported."""
    top = topology_engine.create_simulated_topology("1_gpu_no_tcc")
    assert top.gpu_count == 1
    dev = top.devices[0]
    assert not dev.tcc_supported
    assert dev.driver_mode == "WDDM"

    # Multi-GPU model requiring 2 GPUs should be INCOMPATIBLE
    qwen = profile_storage.get_model_profile("qwen3.8-27b-production")
    res = compatibility_evaluator.evaluate(qwen, top)
    assert not res.can_run
    assert res.status == CompatibilityStatus.INCOMPATIBLE
    assert "Недостаточно доступных GPU" in res.summary

    # Compact single-GPU model should be COMPATIBLE
    game_agent = profile_storage.get_model_profile("game-agent-production")
    res_agent = compatibility_evaluator.evaluate(game_agent, top)
    assert res_agent.can_run

def test_scenario_1_gpu_with_tcc_display_active():
    """1 GPU NVIDIA, TCC supported, display active."""
    top = topology_engine.create_simulated_topology("1_gpu_with_tcc_display")
    assert top.gpu_count == 1
    dev = top.devices[0]
    assert dev.tcc_supported
    assert dev.display_active

    # Single-GPU model on display card runs with warning
    game_agent = profile_storage.get_model_profile("game-agent-production")
    res = compatibility_evaluator.evaluate(game_agent, top)
    assert res.can_run
    assert any("активен рабочий стол Windows" in w for w in res.warnings)

def test_scenario_2_gpu_with_nvlink():
    """2 identical GPUs with NVLink bridge."""
    top = topology_engine.create_simulated_topology("2_gpu_nvlink")
    assert top.gpu_count == 2
    assert len(top.physical_nvlink_cliques) == 1
    assert top.physical_nvlink_cliques[0] == [0, 1]

    # In WDDM/WDDM, Qwen 3.8 is COMPATIBLE WITH WARNING (PCIe fallback)
    qwen = profile_storage.get_model_profile("qwen3.8-27b-production")
    res_wddm = compatibility_evaluator.evaluate(qwen, top)
    assert res_wddm.can_run
    assert res_wddm.status == CompatibilityStatus.COMPATIBLE_WITH_WARNING
    assert "PCIe / Host Fallback Transport" in res_wddm.transport_mode

    # In TCC/TCC, P2P is active -> fully COMPATIBLE
    top.devices[0].is_tcc = True
    top.devices[0].driver_mode = "TCC"
    top.devices[1].is_tcc = True
    top.devices[1].driver_mode = "TCC"
    top.devices[0].p2p_peers = [1]
    top.devices[1].p2p_peers = [0]
    top.cuda_p2p_cliques = [[0, 1]]

    res_tcc = compatibility_evaluator.evaluate(qwen, top)
    assert res_tcc.can_run
    assert res_tcc.status == CompatibilityStatus.COMPATIBLE
    assert "CUDA Direct P2P" in res_tcc.transport_mode

def test_scenario_2_gpu_no_nvlink():
    """2 identical GPUs without NVLink."""
    top = topology_engine.create_simulated_topology("2_gpu_no_nvlink")
    assert top.gpu_count == 2
    assert len(top.physical_nvlink_cliques) == 0

    qwen = profile_storage.get_model_profile("qwen3.8-27b-production")
    res = compatibility_evaluator.evaluate(qwen, top)
    # Since require_p2p is False, it runs with PCIe fallback warning
    assert res.can_run
    assert res.status == CompatibilityStatus.COMPATIBLE_WITH_WARNING

def test_scenario_2_gpu_heterogeneous():
    """2 heterogeneous GPUs (24GB + 12GB)."""
    top = topology_engine.create_simulated_topology("2_gpu_heterogeneous")
    assert top.gpu_count == 2
    assert top.devices[0].vram_total_mib == 24576
    assert top.devices[1].vram_total_mib == 12288

    # Qwen requires 40GB total VRAM; 24 + 12 = 36GB < 40GB -> INCOMPATIBLE
    qwen = profile_storage.get_model_profile("qwen3.8-27b-production")
    res = compatibility_evaluator.evaluate(qwen, top)
    assert not res.can_run
    assert res.status == CompatibilityStatus.INCOMPATIBLE
    assert "Недостаточно видеопамяти" in res.summary

    # Compact single-GPU model works on largest GPU (GPU 0)
    game_agent = profile_storage.get_model_profile("game-agent-production")
    res_agent = compatibility_evaluator.evaluate(game_agent, top)
    assert res_agent.can_run
    assert res_agent.assigned_gpus[0].vram_total_mib == 24576

def test_scenario_3_gpu_1_graphics_2_compute():
    """3 GPUs: GPU 0 is graphics (3060), GPU 1 & 2 are compute (A5000) with NVLink."""
    top = topology_engine.create_simulated_topology("3_gpu_1_graphics_2_compute")
    assert top.gpu_count == 3
    assert len(top.physical_nvlink_cliques) == 1
    assert top.physical_nvlink_cliques[0] == [1, 2]
    assert len(top.cuda_p2p_cliques) == 1
    assert top.cuda_p2p_cliques[0] == [1, 2]

    qwen = profile_storage.get_model_profile("qwen3.8-27b-production")
    # Qwen uses all compute GPUs (GPU 1 and GPU 2)
    res = compatibility_evaluator.evaluate(qwen, top)
    assert res.can_run
    assert len(res.assigned_gpus) == 2
    assert all(g.is_tcc for g in res.assigned_gpus)

def test_scenario_3_gpu_partial_nvlink():
    """3 GPUs: NVLink only between GPU 1 and GPU 2."""
    top = topology_engine.create_simulated_topology("3_gpu_nvlink_partial")
    assert top.gpu_count == 3
    assert top.physical_nvlink_cliques == [[1, 2]]

def test_scenario_4_gpu_cluster():
    """4 GPUs in compute cluster."""
    top = topology_engine.create_simulated_topology("4_gpu_cluster")
    assert top.gpu_count == 4
    qwen = profile_storage.get_model_profile("qwen3.8-27b-production")
    res = compatibility_evaluator.evaluate(qwen, top)
    assert res.can_run
    assert len(res.assigned_gpus) == 4

def test_scenario_amd_only():
    """AMD Radeon RX 7900 XTX."""
    top = topology_engine.create_simulated_topology("amd_only")
    assert top.gpu_count == 1
    assert top.devices[0].vendor == "AMD"
    assert not top.devices[0].tcc_supported

def test_scenario_intel_only():
    """Intel Arc A770."""
    top = topology_engine.create_simulated_topology("intel_only")
    assert top.gpu_count == 1
    assert top.devices[0].vendor == "Intel"

def test_scenario_cpu_only():
    """CPU-only machine."""
    top = topology_engine.create_simulated_topology("cpu_only")
    assert top.gpu_count == 0
    assert top.devices == []

    # Multi-GPU model requiring 2 GPUs is INCOMPATIBLE
    qwen = profile_storage.get_model_profile("qwen3.8-27b-production")
    res = compatibility_evaluator.evaluate(qwen, top)
    assert not res.can_run
    assert res.status == CompatibilityStatus.INCOMPATIBLE

    # Unloaded model 'none' is always compatible
    none_model = profile_storage.get_model_profile("none")
    res_none = compatibility_evaluator.evaluate(none_model, top)
    assert res_none.can_run

def test_strict_p2p_vs_preferred_p2p():
    """Verify require_p2p=True blocks without P2P, while prefer_p2p=True allows with warning."""
    top = topology_engine.create_simulated_topology("2_gpu_no_nvlink")

    # 1. Model with require_p2p = True
    strict_model = ModelProfile(
        id="strict-test",
        name="Strict P2P Model",
        weights_path="dummy.gguf",
        min_gpu_count=2,
        min_total_vram_mib=16000,
        require_p2p=True
    )
    res_strict = compatibility_evaluator.evaluate(strict_model, top)
    assert not res_strict.can_run
    assert res_strict.status == CompatibilityStatus.INCOMPATIBLE
    assert "строго требует аппаратный CUDA Direct P2P" in res_strict.summary

    # 2. Model with prefer_p2p = True, require_p2p = False
    flexible_model = ModelProfile(
        id="flexible-test",
        name="Flexible Model",
        weights_path="dummy.gguf",
        min_gpu_count=2,
        min_total_vram_mib=16000,
        prefer_p2p=True,
        require_p2p=False
    )
    res_flex = compatibility_evaluator.evaluate(flexible_model, top)
    assert res_flex.can_run
    assert res_flex.status == CompatibilityStatus.COMPATIBLE_WITH_WARNING
