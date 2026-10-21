import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop_start


def test_listener_only_matches_this_local_port():
    table = '''
      TCP    127.0.0.1:8765   0.0.0.0:0       LISTENING    1234
      TCP    127.0.0.1:8765   127.0.0.1:9999  ESTABLISHED  1234
      TCP    127.0.0.1:8766   0.0.0.0:0       LISTENING    5678
      TCP    0.0.0.0:8765     0.0.0.0:0       LISTENING    9876
    '''
    assert desktop_start.listener_pids(table) == {1234}


def own_service():
    return {'model': 'paraformer-v2',
            'exports_path': str(desktop_start.APP.parents[1] / 'outputs/short-clips')}


def test_other_service_never_stopped(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('不能关闭其他服务')
    monkeypatch.setattr(desktop_start.subprocess, 'run', unexpected)
    with pytest.raises(ValueError, match='其他服务'):
        desktop_start.retire_isolated_service({'model': 'different-app'})


def test_own_service_identity_accepts_custom_export_folder(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop_start.urllib.request, 'urlopen',
                        lambda *a, **k: io.BytesIO(b'[]'))
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0,
            stdout='TCP 127.0.0.1:8765 0.0.0.0:0 LISTENING 1234' if command[0] == 'netstat.exe' else '')
    monkeypatch.setattr(desktop_start.subprocess, 'run', run)
    monkeypatch.setattr(desktop_start, 'running_service', lambda: None)
    desktop_start.retire_isolated_service({'app_id': 'longvideo-editor',
        'app_root': str(desktop_start.APP), 'exports_path': 'D:/自定义导出'})
    assert calls[1] == ['taskkill.exe', '/PID', '1234', '/T', '/F']


def test_active_jobs_prevent_restart(monkeypatch):
    monkeypatch.setattr(desktop_start.urllib.request, 'urlopen',
                        lambda *a, **k: io.BytesIO(json.dumps([{'status': 'running'}]).encode()))
    def unexpected(*args, **kwargs):
        raise AssertionError('有后台任务时不能关闭服务')
    monkeypatch.setattr(desktop_start.subprocess, 'run', unexpected)
    with pytest.raises(ValueError, match='后台任务'):
        desktop_start.retire_isolated_service(own_service())


def test_idle_service_restart_targets_verified_listener(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop_start.urllib.request, 'urlopen',
                        lambda *a, **k: io.BytesIO(b'[]'))
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0,
            stdout='TCP 127.0.0.1:8765 0.0.0.0:0 LISTENING 1234' if command[0] == 'netstat.exe' else '')
    monkeypatch.setattr(desktop_start.subprocess, 'run', run)
    monkeypatch.setattr(desktop_start, 'running_service', lambda: None)
    desktop_start.retire_isolated_service(own_service())
    assert calls[1] == ['taskkill.exe', '/PID', '1234', '/T', '/F']
