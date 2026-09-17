"""Unit tests for schedules manager and idle unload."""
from datetime import datetime
import pytest
from src import schedules as sc


def test_format_days():
    assert sc.format_days([0, 1, 2, 3, 4, 5, 6]) == 'Каждый день'
    assert sc.format_days([0, 1, 2, 3, 4]) == 'Будни (Пн–Пт)'
    assert sc.format_days([5, 6]) == 'Выходные (Сб–Вс)'
    assert 'Пн' in sc.format_days([0, 2])
    assert sc.format_days([]) == 'Никогда'


def test_add_and_toggle_task(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, 'schedules_path', lambda: tmp_path / 'schedules.yaml')
    mgr = sc.ScheduleManager()

    # Invalid time format raises ValueError
    with pytest.raises(ValueError):
        mgr.add_task(name='Bad', time_str='25:00', days=[0], action='gpu_profile', target='test')

    task = mgr.add_task(name='Morning TCC', time_str='08:30', days=[0, 1, 2, 3, 4], action='gpu_profile', target='gpu-all-tcc')
    assert task['time'] == '08:30'
    assert task['enabled'] is True
    assert len(mgr.tasks) == 1

    # Toggle task
    assert mgr.toggle_task(task['id'], False) is True
    assert mgr.tasks[0]['enabled'] is False

    # Reload from disk
    mgr2 = sc.ScheduleManager()
    assert len(mgr2.tasks) == 1
    assert mgr2.tasks[0]['enabled'] is False

    # Remove task
    assert mgr2.remove_task(task['id']) is True
    assert len(mgr2.tasks) == 0


def test_schedule_triggering(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, 'schedules_path', lambda: tmp_path / 'schedules.yaml')
    executed = []
    mgr = sc.ScheduleManager(action_runner=lambda act, tgt: executed.append((act, tgt)))

    mgr.add_task(name='Night', time_str='23:00', days=[0, 1, 2, 3, 4, 5, 6], action='unload_model', target='none')

    # Monday at 22:59 - not due
    dt_early = datetime(2026, 9, 14, 22, 59, 0)
    mgr.poll(None, now_dt=dt_early)
    assert len(executed) == 0

    # Monday at 23:00 - due!
    dt_due = datetime(2026, 9, 14, 23, 0, 15)
    mgr.poll(None, now_dt=dt_due)
    assert len(executed) == 1
    assert executed[0] == ('unload_model', 'none')

    # Same minute later - should not fire again
    dt_same = datetime(2026, 9, 14, 23, 0, 45)
    mgr.poll(None, now_dt=dt_same)
    assert len(executed) == 1


def test_idle_unload(monkeypatch):
    executed = []
    notified = []
    mgr = sc.ScheduleManager(
        action_runner=lambda act, tgt: executed.append((act, tgt)),
        notify=lambda msg: notified.append(msg)
    )

    from src.config import config
    monkeypatch.setattr(config, 'get', lambda k, d=None: True if k == 'idle_unload_enabled' else (15 if k == 'idle_unload_timeout_minutes' else d))

    t0 = 1000.0
    info = {'active_model': 'qwen', 'in_flight': 0, 'requests_total': 5}

    # First observation records activity
    mgr._check_idle_unload(info, now_ts=t0)
    assert len(executed) == 0

    # 10 minutes later (600s < 900s) -> no unload
    mgr._check_idle_unload(info, now_ts=t0 + 600)
    assert len(executed) == 0

    # Active request in flight resets activity timer
    info_active = {'active_model': 'qwen', 'in_flight': 1, 'requests_total': 6}
    mgr._check_idle_unload(info_active, now_ts=t0 + 700)
    assert len(executed) == 0

    # In flight back to 0 at t0 + 800
    info_done = {'active_model': 'qwen', 'in_flight': 0, 'requests_total': 6}
    mgr._check_idle_unload(info_done, now_ts=t0 + 800)
    assert len(executed) == 0

    # 15 minutes after t0 + 800 (t0 + 800 + 901) -> triggers unload!
    mgr._check_idle_unload(info_done, now_ts=t0 + 800 + 901)
    assert len(executed) == 1
    assert executed[0] == ('unload_model', 'none')
    assert len(notified) == 1
