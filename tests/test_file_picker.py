import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import file_picker


@pytest.fixture(autouse=True)
def interactive_desktop(monkeypatch):
    monkeypatch.setattr(file_picker, 'current_desktop_name', lambda: 'Default')


@pytest.mark.parametrize('name', ['', 'CodexSandboxDesktop-test'])
def test_invisible_desktop_rejected_before_launch(monkeypatch, name):
    monkeypatch.setattr(file_picker, 'current_desktop_name', lambda: name)
    def unexpected(*args, **kwargs):
        raise AssertionError('不应启动不可见的文件选择窗口')
    monkeypatch.setattr(file_picker.subprocess, 'run', unexpected)
    with pytest.raises(ValueError, match='隔离桌面'):
        file_picker.select_video_files('D:/素材')


@pytest.mark.parametrize('response', ['[]', '["D:/素材/第一条.MP4", "D:/素材/第二条.MOV"]'])
def test_cancel_and_unicode_multiple_selection(monkeypatch, response):
    monkeypatch.setattr(file_picker.subprocess, 'run',
                        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=response))
    import json
    assert file_picker.select_video_files('D:/素材') == json.loads(response)


@pytest.mark.parametrize('response', ['not json', '{"path": "D:/素材.mp4"}', '[123]'])
def test_malformed_dialog_response_rejected(monkeypatch, response):
    monkeypatch.setattr(file_picker.subprocess, 'run',
                        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=response))
    with pytest.raises(ValueError):
        file_picker.select_video_files('D:/素材')


def test_unavailable_desktop_reports_error(monkeypatch):
    monkeypatch.setattr(file_picker.subprocess, 'run',
                        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=''))
    with pytest.raises(ValueError, match='Windows 桌面'):
        file_picker.select_video_files('D:/素材')


def test_process_launch_failure_reports_error(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('test: process launch unavailable')
    monkeypatch.setattr(file_picker.subprocess, 'run', unavailable)
    with pytest.raises(ValueError, match='无法启动系统文件选择窗口'):
        file_picker.select_video_files('D:/素材')


def test_timed_out_dialog_reports_retry(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('file-picker', 600)
    monkeypatch.setattr(file_picker.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='超时'):
        file_picker.select_video_files('D:/素材')


@pytest.mark.parametrize('response,expected', [('[]', None), ('["D:/中文导出目录"]', 'D:/中文导出目录')])
def test_folder_picker_selection_and_cancel_use_directory_mode(monkeypatch, response, expected):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=response)
    monkeypatch.setattr(file_picker.subprocess, 'run', run)
    assert file_picker.select_directory('D:/素材') == expected
    assert commands[0][-2:] == ['--directory', 'D:/素材']


def test_folder_picker_rejects_multiple_paths(monkeypatch):
    monkeypatch.setattr(file_picker.subprocess, 'run',
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout='["D:/a", "D:/b"]'))
    with pytest.raises(ValueError, match='一个导出文件夹'):
        file_picker.select_directory('D:/素材')
