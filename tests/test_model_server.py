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
