from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import file_picker
import media
import runtime_paths
from runtime_paths import default_layout


def test_bundle_data_stays_outside_program_and_source_workspace(tmp_path):
    app = tmp_path / 'readonly-program' / '_internal'
    home = tmp_path / '用户目录'
    local = tmp_path / '用户缓存'
    layout = default_layout(app=app, home=home, frozen=True,
                            environment={'LOCALAPPDATA': str(local)})
    assert layout['data'] == local / 'LongVideoEditor'
    assert layout['exports'] == home / 'Videos/LongVideoEditor'
    assert layout['transcripts'] == home / 'Documents/LongVideoEditor'
    assert layout['tools'] == app / 'tools'
    assert not app.exists() and not local.exists()


def test_packaged_tcl_and_tk_paths_preserve_the_exact_resource_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_paths, 'is_frozen', lambda: True)
    for name in ('_tcl_data', '_tk_data'):
        (tmp_path / name).mkdir()
    monkeypatch.setenv('TCL_LIBRARY', 'previous-tcl')
    monkeypatch.setenv('TK_LIBRARY', 'previous-tk')
    runtime_paths.configure_tk_resources(app=tmp_path)
    for key, name in (('TCL_LIBRARY', '_tcl_data'), ('TK_LIBRARY', '_tk_data')):
        assert runtime_paths.os.environ[key] == '\\\\?\\' + str(tmp_path / name)


def test_source_layout_and_explicit_data_overrides_are_preserved(tmp_path):
    app = tmp_path / 'outputs/longvideo-editor'
    original = default_layout(app=app, environment={}, frozen=False)
    assert original['data'] == tmp_path / 'work/longvideo-editor-data'
    overridden = default_layout(app=app, frozen=True, environment={
        'LONGVIDEO_DATA': str(tmp_path / 'custom-data'),
        'LONGVIDEO_EXPORTS': str(tmp_path / 'custom-videos'),
        'LONGVIDEO_TRANSCRIPTS': str(tmp_path / 'custom-text')})
    assert overridden['data'] == tmp_path / 'custom-data'
    assert overridden['exports'] == tmp_path / 'custom-videos'
    assert overridden['transcripts'] == tmp_path / 'custom-text'


def test_frozen_picker_relaunches_the_bundled_exe(monkeypatch):
    calls = []
    monkeypatch.setattr(file_picker.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(file_picker.sys, 'executable', 'D:/程序/LongVideoEditor.exe')
    monkeypatch.setattr(file_picker, 'current_desktop_name', lambda: 'Default')
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='["D:/输出目录"]')
    monkeypatch.setattr(file_picker.subprocess, 'run', run)
    assert file_picker.select_directory('D:/初始目录') == 'D:/输出目录'
    assert calls == [['D:/程序/LongVideoEditor.exe', '--picker-mode', 'directory',
                      '--initial-directory', 'D:/初始目录']]


def test_bundle_requires_its_own_tools_and_never_falls_back_to_machine_config(tmp_path, monkeypatch):
    monkeypatch.setattr(media, 'is_frozen', lambda: True)
    monkeypatch.setattr(media, 'TOOLS', tmp_path)
    (tmp_path / 'paths.json').write_text('{"ffmpeg":"private-machine.exe"}')
    with pytest.raises(ValueError, match='资源不完整'):
        media.binaries()
    for name in ('ffmpeg.exe', 'ffprobe.exe'):
        (tmp_path / name).touch()
    assert media.binaries() == {name: str(tmp_path / f'{name}.exe')
                                for name in ('ffmpeg', 'ffprobe')}
