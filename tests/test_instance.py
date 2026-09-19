import sys
from pathlib import Path
import pytest
from src.instance import StationInstance


def test_station_instance_single_acquire(tmp_path):
    inst1 = StationInstance(directory=tmp_path)
    assert inst1.acquire() is True
    try:
        # Second instance for the same directory must fail to acquire and return False
        inst2 = StationInstance(directory=tmp_path)
        assert inst2.acquire() is False
    finally:
        inst1.close()

    # After first is closed, a new instance should be able to acquire
    inst3 = StationInstance(directory=tmp_path)
    try:
        assert inst3.acquire() is True
    finally:
        inst3.close()


def test_station_instance_show_callback(tmp_path):
    inst1 = StationInstance(directory=tmp_path)
    assert inst1.acquire() is True
    show_called = []
    inst1.listen(lambda: show_called.append(True))
    try:
        inst2 = StationInstance(directory=tmp_path)
        assert inst2.acquire() is False
        import time
        time.sleep(0.3)
        assert len(show_called) >= 1
    finally:
        inst1.close()
