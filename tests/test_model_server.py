"""Single model server: executable discovery, model folder, network binding, tolerant profiles."""
from pathlib import Path
import src.model_server as ms
from src.config import AppConfig
from src.profile_storage import ProfileStorage
from src.storage import atomic_write, read_document


def use_config(monkeypatch, tmp_path, **values):
    cfg = AppConfig(tmp_path / 'station.yaml')
    if values:
        cfg.update(values)
    monkeypatch.setattr(ms, 'config', cfg)
    return cfg


def test_executables_are_found_inside_engine_folder(tmp_path, monkeypatch):
    engine = tmp_path / 'engine'
    for relative in ('llama-swap/' + ms.SWAP_EXE, 'llama.cpp/build/' + ms.LLAMA_SERVER_EXE):
        (engine / relative).parent.mkdir(parents=True, exist_ok=True)
        (engine / relative).write_bytes(b'x')
    use_config(monkeypatch, tmp_path, runtime_dir=str(engine))
    assert ms.swap_executable() == engine / 'llama-swap' / ms.SWAP_EXE
    assert ms.llama_server_executable() == engine / 'llama.cpp/build' / ms.LLAMA_SERVER_EXE


def test_legacy_explicit_executables_still_define_engine_folder(tmp_path, monkeypatch):
    exe = tmp_path / 'stack/llama-swap' / ms.SWAP_EXE
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'x')
    use_config(monkeypatch, tmp_path, llama_swap_executable=str(exe))
    assert ms.runtime_dir() == tmp_path / 'stack'
    assert ms.swap_executable() == exe


def test_relative_model_paths_use_models_folder(tmp_path, monkeypatch):
    use_config(monkeypatch, tmp_path, models_dir=str(tmp_path / 'models'))
    assert Path(ms.resolve_model_file('qwen.gguf')) == tmp_path / 'models/qwen.gguf'
    absolute = str(tmp_path / 'elsewhere/qwen.gguf')
    assert ms.resolve_model_file(absolute) == absolute


def test_network_access_is_opt_in(tmp_path, monkeypatch):
    cfg = use_config(monkeypatch, tmp_path)
    assert ms.listen_address() == '127.0.0.1:9292' and ms.lan_urls() == []
    cfg.set('llama_swap_lan_access', True)
    assert ms.listen_address() == '0.0.0.0:9292'
    assert ms.api_url() == 'http://127.0.0.1:9292/v1'


def test_unknown_profile_fields_warn_and_survive_save(tmp_path):
    rows = [{'id': 'm', 'name': 'Model', 'weights_path': 'm.gguf', 'measured_decode_tps': 42}]
    atomic_write(tmp_path / 'model_profiles.yaml', rows)
    store = ProfileStorage(tmp_path)
    assert 'm' in store.model_profiles and 'measured_decode_tps' in store.warnings[0]
    store.save_model_profile(store.model_profiles['m'])
    assert read_document(tmp_path / 'model_profiles.yaml')[0]['measured_decode_tps'] == 42


def test_existing_setup_fills_engine_and_models_folders_once(tmp_path, monkeypatch):
    from src.profiles_schema import ModelProfile
    exe = tmp_path / 'stack/llama-swap' / ms.SWAP_EXE
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'x')
    weights = tmp_path / 'LLM/models/q.gguf'
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b'x')
    cfg = use_config(monkeypatch, tmp_path, llama_swap_executable=str(exe))
    changes = ms.fill_missing_folders([ModelProfile('q', 'Q', str(weights))])
    assert cfg.get('runtime_dir') == str(tmp_path / 'stack') and cfg.get('models_dir') == str(weights.parent)
    assert ms.fill_missing_folders([ModelProfile('q', 'Q', str(weights))]) == {} and changes


def test_build_model_entry_adds_override_kv_for_extended_context(tmp_path, monkeypatch):
    from src.model_backend import build_model_entry
    from src.profiles_schema import ModelProfile
    from src.gguf import GgufInfo

    exe = tmp_path / ms.LLAMA_SERVER_EXE
    exe.write_bytes(b'x')
    weights = tmp_path / 'qwen38.gguf'
    weights.write_bytes(b'x')

    fake_info = GgufInfo(str(weights), metadata={'general.architecture': 'qwen35', 'qwen35.context_length': 262144})
    monkeypatch.setattr('src.model_backend.read_gguf_cached', lambda path: fake_info)

    # Context 512K exceeds 262K -> adds override-kv
    model_512k = ModelProfile('qwen-long', 'Qwen Long', str(weights), context=524288)
    entry = build_model_entry(model_512k, [], str(exe))
    assert '--override-kv qwen35.context_length=int:524288' in entry['cmd']

    # Context 256K <= 262K -> does not add override-kv
    model_256k = ModelProfile('qwen-prod', 'Qwen Prod', str(weights), context=262144)
    entry_256k = build_model_entry(model_256k, [], str(exe))
    assert '--override-kv' not in entry_256k['cmd']


def test_build_model_entry_adds_vision_flags(tmp_path, monkeypatch):
    from src.model_backend import build_model_entry
    from src.profiles_schema import ModelProfile
    from src.gguf import GgufInfo

    exe = tmp_path / ms.LLAMA_SERVER_EXE
    exe.write_bytes(b'x')
    weights = tmp_path / 'qwen_vision.gguf'
    weights.write_bytes(b'x')
    mmproj = tmp_path / 'mmproj.gguf'
    mmproj.write_bytes(b'x')

    fake_info = GgufInfo(str(weights), metadata={})
    monkeypatch.setattr('src.model_backend.read_gguf_cached', lambda path: fake_info)

    model = ModelProfile(
        'qwen-vl', 'Qwen VL', str(weights),
        vision=True,
        mmproj_path=str(mmproj),
        no_mmproj_offload=True,
        image_min_tokens=1024,
        image_max_tokens=2240,
    )
    entry = build_model_entry(model, [], str(exe))
    assert '--no-mmproj-offload' in entry['cmd']
    assert '--image-min-tokens 1024' in entry['cmd']
    assert '--image-max-tokens 2240' in entry['cmd']



