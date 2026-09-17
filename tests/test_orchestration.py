"""Regressions for process ownership, synchronization and model transitions."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest
from src.storage import atomic_write,read_document,ConfigurationConflict
from src.agent_sync import preview_merge,apply_preview
from src.supervisor import ProcessSupervisor
from src.config import AppConfig
from src.profiles_schema import ModelProfile,GpuHardwareProfile
from src.profile_storage import ProfileStorage
from src.hardware_topology import HardwareTopology,GpuDeviceInfo
from src.gpu_modes import ProfileExecutionManager
from src.agents.qwen_code.adapter import QwenCodeAdapter


def test_smoke_failure_preserves_concurrent_human_edit(tmp_path):
    p=tmp_path/'settings.json';atomic_write(p,{'model':1})
    preview=preview_merge(p,[(['model'],2)])
    def failed():atomic_write(p,{'model':3});return False
    with pytest.raises(ConfigurationConflict):apply_preview(preview,accept_custom=True,smoke=failed)
    assert read_document(p)=={'model':3}

def test_failed_first_sync_removes_new_file(tmp_path):
    p=tmp_path/'settings.json';preview=preview_merge(p,[(['model'],2)])
    with pytest.raises(RuntimeError):apply_preview(preview,smoke=lambda:False)
    assert not p.exists()

def test_diff_hides_existing_credentials(tmp_path):
    p=tmp_path/'settings.json';atomic_write(p,{'provider':{'api_key':'VERY_PRIVATE','baseUrl':'old'}})
    preview=preview_merge(p,[(['provider'],{'api_key':'new','baseUrl':'new'})])
    assert 'VERY_PRIVATE' not in preview.diff and '<redacted>' in preview.diff

def test_provider_and_binding_history_both_survive(tmp_path):
    p=tmp_path/'settings.json'
    apply_preview(preview_merge(p,[(['providers','station'],{'model':'a'})]))
    apply_preview(preview_merge(p,[(['model'],'a')]))
    preview=preview_merge(p,[(['providers','station'],{'model':'b'})])
    assert preview.status=='OUT OF SYNC'

def test_backend_aliases_do_not_duplicate_qwen_catalog(tmp_path):
    q=QwenCodeAdapter();q.schema_confirmed=True;q.settings={'home':str(tmp_path)}
    a=ModelProfile('first','First','f.gguf',startup_key='legacy')
    b=ModelProfile('second','Second','f.gguf',startup_key='legacy')
    p=q.configure_model_provider([a,b]).data
    ids=[r['id'] for r in p.after['modelProviders']['local-agent-station']]
    assert ids==['first','second']

def test_pid_reuse_never_grants_process_ownership(tmp_path):
    import psutil
    manager=ProcessSupervisor(tmp_path);current=psutil.Process()
    manager.records['test']={'pid':current.pid,'created':current.create_time()-100,'exe':current.exe()}
    assert manager.owned_process('test') is None
    assert manager.stop('test')['success']
    assert current.is_running()

def test_supervisor_stops_only_its_own_process(tmp_path):
    manager=ProcessSupervisor(tmp_path)
    foreign=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        manager.start('test',[sys.executable,'-c','import time;time.sleep(60)'])
        assert manager.status('test')['running']
        assert manager.stop('test')['success']
        assert foreign.poll() is None
    finally:
        foreign.terminate();foreign.wait(timeout=5)

def setup_switch(tmp_path,monkeypatch,owned=True):
    import src.gpu_modes as module
    cfg=AppConfig(tmp_path/'station.yaml');cfg.set('active_model_profile','old')
    store=ProfileStorage(tmp_path/'profiles')
    store.model_profiles['new']=ModelProfile('new','New','fake.gguf',min_total_vram_mib=12000,min_free_vram_per_gpu_mib=0)
    manager=ProfileExecutionManager()
    occupied=HardwareTopology([GpuDeviceInfo(0,'GPU-test',vram_total_mib=24000,vram_free_mib=1000,display_active=False)],is_simulated=True)
    free=HardwareTopology([GpuDeviceInfo(0,'GPU-test',vram_total_mib=24000,vram_free_mib=22000,display_active=False)],is_simulated=True)
    monkeypatch.setattr(module,'config',cfg);monkeypatch.setattr(module,'profile_storage',store)
    monkeypatch.setattr(manager,'get_current_topology',lambda:occupied)
    monkeypatch.setattr(manager,'get_active_gpu_profile',lambda:GpuHardwareProfile('u','Unchanged'))
    calls=[]
    manager.engine=SimpleNamespace(get_active_model=lambda:None,request=lambda path:{'running':[{'model':'old','state':'ready'}]},switch_model=lambda *a:calls.append('start') or True)
    monkeypatch.setattr(module,'pm',SimpleNamespace(is_llama_swap_running=lambda:True,free_gpu=lambda:calls.append('unload') or True,
        backend_info=lambda *a:{'owned':owned,'stale_config':False,'listen':'127.0.0.1:9292','log':None},describe=lambda *a:'running'))
    monkeypatch.setattr(module.model_server,'listen_address',lambda:'127.0.0.1:9292')
    monkeypatch.setattr(manager,'_compile',lambda *a:(tmp_path/'swap.yaml',False,{}))
    monkeypatch.setattr(module,'supervisor',SimpleNamespace(status=lambda key:{'owned':owned}))
    monkeypatch.setattr(module.hardware,'reinit',lambda:None)
    monkeypatch.setattr(module.topology_engine,'discover_live',lambda **kw:free)
    monkeypatch.setattr(module,'compile_swap',lambda *a:(tmp_path/'swap.yaml',False,{}))
    return manager,calls,cfg

def test_model_switch_rechecks_memory_after_owned_unload(tmp_path,monkeypatch):
    manager,calls,cfg=setup_switch(tmp_path,monkeypatch)
    assert manager.apply_model_profile_only('new')['Success']
    assert calls==['unload','start'] and cfg.get('active_model_profile')=='new'

def test_model_switch_never_unloads_foreign_backend(tmp_path,monkeypatch):
    manager,calls,cfg=setup_switch(tmp_path,monkeypatch,owned=False)
    assert not manager.apply_model_profile_only('new')['Success']
    assert calls==[] and cfg.get('active_model_profile')=='old'

def test_changed_profile_with_same_model_id_is_reloaded(tmp_path,monkeypatch):
    manager,calls,cfg=setup_switch(tmp_path,monkeypatch)
    manager.engine.get_active_model=lambda:'new'
    manager.engine.request=lambda path:{'running':[{'model':'new','state':'ready'}]}
    cfg.set('active_model_signature','old-launch-parameters')
    assert manager.apply_model_profile_only('new')['Success']
    assert calls==['unload','start']

def test_qualification_metadata_does_not_change_launch_signature():
    from dataclasses import replace
    from src.model_backend import launch_signature
    model=ModelProfile('m','Model','fake.gguf')
    top=HardwareTopology([],is_simulated=True)
    signature=launch_signature(model,None,None,top)
    assert signature==launch_signature(replace(model,qualified=True,tool_calling=True),None,None,top)
    assert signature!=launch_signature(replace(model,context=8192),None,None,top)

def test_selecting_model_without_start_does_not_claim_it_is_running(tmp_path,monkeypatch):
    manager,calls,cfg=setup_switch(tmp_path,monkeypatch)
    # Selection validates capacity independently from activation.
    manager.get_current_topology=lambda:HardwareTopology([GpuDeviceInfo(0,'g',vram_total_mib=24000,vram_free_mib=22000,display_active=False)],is_simulated=True)
    assert manager.apply_model_profile_only('new',auto_start=False)['Success']
    assert calls==[] and cfg.get('active_model_profile')=='old' and cfg.get('selected_model_profile')=='new'

def test_migration_preserves_previously_saved_station_paths(tmp_path):
    from src.migration import preview_migration,apply_migration
    source=tmp_path/'old';source.mkdir();dest=tmp_path/'new'
    atomic_write(dest/'config/station.yaml',{'llama_server_executable':'C:/chosen/server.exe'})
    plan=preview_migration(source,dest)
    apply_migration(plan)
    assert read_document(dest/'config/station.yaml')['llama_server_executable']=='C:/chosen/server.exe'
    assert (dest/'config/model_profiles.yaml').exists()

@pytest.mark.parametrize('filename,text',[('x.json','{"a":1,"a":2}'),('x.yaml','a: 1\na: 2\n')])
def test_duplicate_keys_are_rejected(tmp_path,filename,text):
    from src.storage import ConfigurationError
    p=tmp_path/filename;p.write_text(text)
    with pytest.raises(ConfigurationError):read_document(p)

def test_second_instance_requests_show_without_starting_again(tmp_path):
    import threading
    from src.instance import StationInstance
    shown=threading.Event();first=StationInstance(tmp_path);second=StationInstance(tmp_path)
    try:
        assert first.acquire()
        first.listen(shown.set)
        assert not second.acquire()
        assert shown.wait(2)
    finally:first.close();second.close()
