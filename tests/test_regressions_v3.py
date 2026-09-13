from copy import deepcopy
from pathlib import Path
import pytest
from src.config import AppConfig, DEFAULT_SETTINGS
from src.storage import atomic_write, read_document, ConfigurationError, ConfigurationConflict
from src.profile_storage import ProfileStorage
from src.hardware import parse_smi_csv
from src.hardware_topology import GpuDeviceInfo, HardwareTopology, TopologyDiscoveryEngine, parse_topology_matrix
from src.profiles_schema import ModelProfile, GpuHardwareProfile, GpuDeviceRule
from src.compatibility import compatibility_evaluator as evaluator
from src.agent_sync import preview_merge, apply_preview
from src.services.gpu_mode_client import validate_plan
from src.model_backend import tensor_split
from src.migration import preview_migration, apply_migration

def gpu(i, memory=24000, display=False, vendor='NVIDIA'):
    return GpuDeviceInfo(i, f'GPU-test-{i}', vendor=vendor, display_active=display,
        vram_total_mib=memory, vram_free_mib=memory, driver_mode='WDDM', pending_driver_mode='WDDM')

def model(**kwargs):
    return ModelProfile(**dict(dict(id='m', name='Test', weights_path='test.gguf', min_gpu_count=1,
        min_total_vram_mib=8000, min_free_vram_per_gpu_mib=1000, qualified=True, status='stable'), **kwargs))

def topology(*devices):
    return HardwareTopology(list(devices), is_simulated=True)

def test_missing_profile_file_does_not_erase_existing_models(tmp_path):
    path = tmp_path / 'model_profiles.yaml'
    atomic_write(path, [model().to_dict()])
    before = path.read_bytes()
    store = ProfileStorage(tmp_path)
    assert store.get_model_profile('m')
    assert path.read_bytes() == before
    assert not (tmp_path / 'hardware_profiles.yaml').exists()

def test_corrupted_configuration_is_never_replaced_by_defaults(tmp_path):
    path = tmp_path / 'model_profiles.yaml'
    path.write_text('[:broken', encoding='utf-8')
    with pytest.raises(ConfigurationError):
        ProfileStorage(tmp_path)
    assert path.read_text() == '[:broken'

def test_failed_setting_write_does_not_update_memory(tmp_path, monkeypatch):
    config = AppConfig(tmp_path / 'station.yaml')
    monkeypatch.setattr('src.config.atomic_write', lambda *a, **kw: (_ for _ in ()).throw(OSError('Disk full')))
    with pytest.raises(OSError):
        config.set('language', 'uk')
    assert config.get('language') == 'ru'

def test_settings_instances_do_not_share_default_lists(tmp_path):
    first, second = AppConfig(tmp_path/'a.yaml'), AppConfig(tmp_path/'b.yaml')
    values = first.get('excluded_gpu_uuids')
    values.append('other')
    assert second.get('excluded_gpu_uuids') == []
    assert DEFAULT_SETTINGS['excluded_gpu_uuids'] == []

def test_manual_file_changes_are_not_overwritten(tmp_path):
    config = AppConfig(tmp_path / 'station.yaml')
    config.set('language', 'en')
    atomic_write(config.path, {'language': 'uk'})
    with pytest.raises(ConfigurationConflict):
        config.set('language', 'ru')
    assert read_document(config.path)['language'] == 'uk'

def test_failed_gpu_discovery_never_returns_simulated_workstation(monkeypatch):
    monkeypatch.setattr('src.hardware_topology.hardware.query_all_gpus', lambda *a: [])
    monkeypatch.setattr('src.hardware_topology.hardware.last_error', 'Driver unavailable')
    top = TopologyDiscoveryEngine().discover_live()
    assert top.gpu_count == 0 and not top.is_simulated
    assert top.discovery_error

def test_cpu_only_means_zero_gpus():
    assert TopologyDiscoveryEngine().create_simulated_topology('cpu_only').gpu_count == 0

def test_smi_na_values_are_unknown_not_24gb_or_fake_nvlink():
    text = '0,GPU-abc,0000:01:00.0,GeForce,WDDM,WDDM,N/A,N/A,N/A,N/A,N/A,Disabled,1.0'
    row = parse_smi_csv(text)[0]
    assert row['vram_total_mib'] is None and row['temp_c'] is None
    assert row['nvlink_active'] is None and row['tcc_supported'] is None

def test_connected_component_is_not_a_clique():
    devices = [gpu(i) for i in (2, 5, 9)]
    devices[0].p2p_peers = [5]
    devices[1].p2p_peers = [2, 9]
    devices[2].p2p_peers = [5]
    assert TopologyDiscoveryEngine()._find_cliques(devices, lambda g: g.p2p_peers) == [[2, 5], [5, 9]]

def test_partial_topology_parser_with_non_contiguous_indices():
    matrix = '\x1b[4mGPU2 GPU5 GPU9\x1b[0m\nGPU2 X NS NS\nGPU5 NS X OK\nGPU9 NS OK X'
    assert parse_topology_matrix(matrix)[(5, 9)] == 'OK'

@pytest.mark.parametrize('count', [0, 1, 2, 3, 4, 8])
def test_gpu_count_matrix(count):
    result = evaluator.evaluate(model(gpu_selection_policy='all_selected_gpus'), topology(*(gpu(i) for i in range(count))))
    assert result.can_run == (count > 0)
    assert len(result.assigned_gpus) == count

def test_missing_explicit_uuid_never_falls_back_to_another_gpu():
    result = evaluator.evaluate(model(gpu_selection_policy='explicit_uuid_list', explicit_gpu_uuids=['missing']), topology(gpu(0)))
    assert not result.can_run and result.assigned_gpus == []

def test_explicit_uuid_cannot_bypass_hardware_exclusion():
    hardware = GpuHardwareProfile('x', 'X', excluded_devices=['GPU-test-0'])
    result = evaluator.evaluate(model(gpu_selection_policy='explicit_uuid_list', explicit_gpu_uuids=['GPU-test-0']), topology(gpu(0)), hardware)
    assert not result.can_run

@pytest.mark.parametrize('policy', ['dedicated_compute_only', 'exclude_display_gpu'])
def test_strict_display_exclusion_never_falls_back(policy):
    result = evaluator.evaluate(model(gpu_selection_policy=policy), topology(gpu(0, display=True)))
    assert not result.can_run and not result.assigned_gpus

def test_unknown_memory_is_unknown_and_blocks_unsafe_start():
    device = gpu(0)
    device.vram_free_mib = None
    result = evaluator.evaluate(model(), topology(device))
    assert result.status.value == 'UNKNOWN' and not result.can_run

def test_free_memory_is_checked_not_only_total_memory():
    device = gpu(0)
    device.vram_free_mib = 100
    assert not evaluator.evaluate(model(), topology(device)).can_run

def test_cuda_never_assigns_amd_or_intel_to_llama_cuda():
    result = evaluator.evaluate(model(), topology(gpu(0, vendor='AMD'), gpu(1, vendor='Intel')))
    assert not result.can_run and not result.assigned_gpus

def test_p2p_preference_is_warning_and_requirement_blocks():
    top = topology(gpu(0), gpu(1))
    assert evaluator.evaluate(model(min_gpu_count=2, prefer_p2p=True), top).can_run
    assert not evaluator.evaluate(model(min_gpu_count=2, require_p2p=True), top).can_run

def test_blank_weights_is_not_treated_as_unloaded_model():
    assert not evaluator.evaluate(model(weights_path=''), topology(gpu(0))).can_run

def test_best_clique_never_includes_unlinked_graphics_card():
    top = topology(gpu(0), gpu(1), gpu(2))
    top.cuda_p2p_cliques = [[1, 2]]
    selected = evaluator._resolve_assigned_gpus(model(gpu_selection_policy='best_p2p_clique'), top, None)
    assert [g.index for g in selected] == [1, 2]

def test_unequal_cards_do_not_get_equal_tensor_split():
    assert tensor_split(model(), [gpu(0, 24000), gpu(1, 12000)]) == '2.0000,1.0000'

def test_sync_preserves_cloud_settings_and_is_idempotent(tmp_path):
    path = tmp_path / 'settings.json'
    old = {'modelProviders': {'openai': [{'id': 'cloud'}]}, 'env': {'PERSONAL': 'value'}, 'ui': {'theme': 'custom'}}
    atomic_write(path, old)
    changes = [(['modelProviders', 'local-agent-station'], [{'id': 'local'}])]
    apply_preview(preview_merge(path, changes))
    result = read_document(path)
    assert result['env'] == old['env'] and result['ui'] == old['ui']
    assert result['modelProviders']['openai'] == old['modelProviders']['openai']
    assert preview_merge(path, changes).status == 'IN SYNC'

def test_sync_refuses_manually_modified_managed_provider(tmp_path):
    path = tmp_path / 'settings.json'
    changes = [(['modelProviders', 'local-agent-station'], [{'id': 'local'}])]
    apply_preview(preview_merge(path, changes))
    atomic_write(path, {'modelProviders': {'local-agent-station': [{'id': 'edited'}]}})
    preview = preview_merge(path, changes)
    assert preview.status == 'CUSTOM MODIFIED'
    with pytest.raises(ConfigurationConflict):
        apply_preview(preview)

def test_failed_sync_smoke_restores_previous_configuration(tmp_path):
    path = tmp_path / 'settings.json'
    atomic_write(path, {'cloud': True})
    with pytest.raises(RuntimeError):
        apply_preview(preview_merge(path, [(['local'], True)]), smoke=lambda: False)
    assert read_document(path) == {'cloud': True}

@pytest.mark.parametrize('entry', [
    {'gpu_stable_id': '', 'target_mode': 'TCC'},
    {'gpu_stable_id': '0', 'target_mode': 'TCC'},
    {'gpu_stable_id': 'GPU-12345678-1234-1234-1234-123456789012', 'target_mode': 'cmd'},
    {'gpu_stable_id': 'GPU-12345678-1234-1234-1234-123456789012', 'target_mode': 'TCC', 'Arguments': '-fdm'},
])
def test_privileged_protocol_rejects_untyped_or_injectable_requests(entry):
    with pytest.raises(ValueError):
        validate_plan([entry])

def test_migration_preview_has_no_writes_and_apply_preserves_source(tmp_path):
    legacy, destination = tmp_path/'v2', tmp_path/'v3'
    atomic_write(legacy/'settings.json', {'language': 'uk'})
    initial = (legacy/'settings.json').read_bytes()
    plan = preview_migration(legacy, destination)
    assert not destination.exists()
    report = apply_migration(plan)
    assert (legacy/'settings.json').read_bytes() == initial
    assert read_document(destination/'config/station.yaml')['language'] == 'uk'
    assert Path(report['backup']).is_dir()
    with pytest.raises(ValueError):
        apply_migration(plan)
