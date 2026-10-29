import hashlib
import json
from pathlib import Path
import sys
import stat
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from installer_core import InstallError, install_package, sha256, validate_target


def package(tmp_path, version, *, extra=None):
    files = {'LongVideoEditor.exe': b'app-' + version.encode(),
             '_internal/tools/ffmpeg.exe': b'ffmpeg',
             '_internal/tools/ffprobe.exe': b'ffprobe',
             '_internal/static/style.css': b'style-' + version.encode(),
             '启动工作台.vbs': b'current launcher', '使用说明.md': b'user guide'}
    files.update(extra or {})
    manifest = {'version': version, 'platform': 'windows-x64', 'files': [
        {'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in files.items()]}
    files['package-manifest.json'] = json.dumps(manifest).encode()
    archive = tmp_path / (version + '-' + str(len(list(tmp_path.glob('*.zip')))) + '.zip')
    with zipfile.ZipFile(archive, 'w') as zipped:
        for name, data in files.items():
            zipped.writestr('LongVideoEditor/' + name, data)
    return archive, files


def put_old(target, files):
    for name, data in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def upgrade(archive, target, data, **kwargs):
    return install_package(archive, target, sha256(archive), user_data=data,
                           stop_service=kwargs.pop('stop_service', lambda _: None),
                           validator=kwargs.pop('validator', lambda *args: None), **kwargs)


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_fresh_install_keeps_existing_user_data_opaque(tmp_path):
    archive, expected = package(tmp_path, '0.2.0-test.6')
    data, target = tmp_path / 'data', tmp_path / '应用 程序'
    data.mkdir()
    (data / 'editor.sqlite3').write_bytes(b'private-project-database')
    (data / 'saved-key.dpapi').write_bytes(b'opaque encrypted bytes')
    before = tree(data)
    result = upgrade(archive, target, data)
    assert tree(target) == expected
    assert all((data / name).read_bytes() == value for name, value in before.items())
    assert json.loads((data / 'installation.json').read_text())['directory'] == str(target)
    assert result['previous_version'] is None
    assert not list(tmp_path.glob('.*.upgrade-*'))


def test_upgrade_removes_obsolete_program_files_and_inherits_personal_files(tmp_path):
    _, old = package(tmp_path, '0.2.0-test.5',
                     extra={'启动工作台.cmd': b'old launcher', '_internal/obsolete.dll': b'old resource'})
    archive, expected = package(tmp_path, '0.2.0-test.6')
    target, data = tmp_path / '安装目录', tmp_path / 'user-data'
    put_old(target, old)
    personal = {'video/成片.mp4': b'user video', 'editor.sqlite3': b'user database',
                '_internal/personal-notes.json': b'private notes'}
    put_old(target, personal)
    result = upgrade(archive, target, data)
    assert tree(target) == {**expected, **personal}
    assert result['removed_obsolete_files'] == 2
    assert result['modified_files_backup'] is None
    assert result['previous_version'] == '0.2.0-test.5'


def test_modified_program_files_are_saved_separately_before_cleanup(tmp_path):
    _, old = package(tmp_path, '0.2.0-test.5')
    archive, expected = package(tmp_path, '0.2.0-test.6')
    target = tmp_path / 'app'
    put_old(target, old)
    modified = b'custom user styling'
    (target / '_internal/static/style.css').write_bytes(modified)
    result = upgrade(archive, target, tmp_path / 'data')
    assert tree(target) == expected
    assert (Path(result['modified_files_backup']) / '_internal/static/style.css').read_bytes() == modified


def test_failed_new_runtime_validation_restores_the_exact_old_program(tmp_path):
    _, old = package(tmp_path, '0.2.0-test.5', extra={'启动工作台.cmd': b'old launcher'})
    archive, _ = package(tmp_path, '0.2.0-test.6')
    target = tmp_path / 'app'
    put_old(target, {**old, 'my-video.mp4': b'personal data'})
    before = tree(target)
    def failed(*args):
        raise RuntimeError('synthetic validation failure')
    with pytest.raises(InstallError, match='已恢复'):
        upgrade(archive, target, tmp_path / 'data', validator=failed)
    assert tree(target) == before
    assert not list(tmp_path.glob('.*.upgrade-*'))


def test_running_jobs_prevent_replacement(tmp_path):
    _, old = package(tmp_path, '0.2.0-test.5')
    archive, _ = package(tmp_path, '0.2.0-test.6')
    target = tmp_path / 'app'
    put_old(target, old)
    def busy(*args):
        raise InstallError('后台任务尚未完成')
    with pytest.raises(InstallError, match='后台任务'):
        upgrade(archive, target, tmp_path / 'data', stop_service=busy)
    assert tree(target) == old


def test_unrecognized_nonempty_folder_is_never_cleaned(tmp_path):
    archive, _ = package(tmp_path, '0.2.0-test.6')
    target = tmp_path / 'documents'
    put_old(target, {'private.txt': b'personal'})
    with pytest.raises(InstallError, match='不是可识别'):
        upgrade(archive, target, tmp_path / 'data')
    assert tree(target) == {'private.txt': b'personal'}


def test_collision_with_unknown_file_aborts_before_any_replacement(tmp_path):
    _, old = package(tmp_path, '0.2.0-test.5')
    archive, _ = package(tmp_path, '0.2.0-test.6', extra={'_internal/new-resource.json': b'new'})
    target = tmp_path / 'app'
    put_old(target, {**old, '_internal/new-resource.json': b'personal'})
    before = tree(target)
    with pytest.raises(InstallError, match='个人文件冲突'):
        upgrade(archive, target, tmp_path / 'data')
    assert tree(target) == before


def test_downgrade_is_rejected_and_same_version_can_be_repaired(tmp_path):
    _, old = package(tmp_path, '0.2.0-test.6')
    older, _ = package(tmp_path, '0.2.0-test.5')
    same, expected = package(tmp_path, '0.2.0-test.6')
    target = tmp_path / 'app'
    put_old(target, old)
    with pytest.raises(InstallError, match='低于'):
        upgrade(older, target, tmp_path / 'data')
    assert tree(target) == old
    upgrade(same, target, tmp_path / 'data')
    assert tree(target) == expected


@pytest.mark.parametrize('bad_name', ['../outside.dll', '_internal/../../outside.dll',
                                    '_internal/C:secret', '_internal/NUL', '_internal/input.mp4'])
def test_invalid_package_paths_do_not_touch_the_destination(tmp_path, bad_name):
    archive, _ = package(tmp_path, '0.2.0-test.6', extra={bad_name:b'bad'})
    target = tmp_path / 'app'
    with pytest.raises(InstallError):
        upgrade(archive, target, tmp_path / 'data')
    assert not target.exists()


def test_changed_archive_is_rejected(tmp_path):
    archive, _ = package(tmp_path, '0.2.0-test.6')
    digest = sha256(archive)
    with archive.open('ab') as stream:
        stream.write(b'modified')
    with pytest.raises(InstallError, match='校验失败'):
        install_package(archive, tmp_path / 'app', digest, user_data=tmp_path / 'data')


def test_data_directory_and_its_parent_cannot_be_install_targets(tmp_path):
    data = tmp_path / 'user-data'
    for target in (data, data / 'program', tmp_path):
        with pytest.raises(InstallError):
            validate_target(target, data)


def test_old_readonly_program_resource_can_be_cleaned(tmp_path):
    _, old = package(tmp_path, '0.2.0-test.5', extra={'_internal/old.dll':b'obsolete'})
    archive, expected = package(tmp_path, '0.2.0-test.6')
    target = tmp_path / 'app'
    put_old(target, old)
    (target / '_internal/old.dll').chmod(stat.S_IREAD)
    result = upgrade(archive, target, tmp_path / 'data')
    assert tree(target) == expected and result['cleanup_warning'] is None
    assert not list(tmp_path.glob('.*.upgrade-*'))


def test_installation_staging_stays_on_the_destination_volume(tmp_path, monkeypatch):
    archive, expected = package(tmp_path, '0.2.0-test.6')
    output_drive = tmp_path / 'D-drive'
    output_drive.mkdir()
    target = output_drive / 'app'
    original = Path.rename
    movements = []
    def rename(source, destination):
        assert source.is_relative_to(output_drive) == Path(destination).is_relative_to(output_drive), 'WinError 17'
        movements.append((source, Path(destination)))
        return original(source, destination)
    monkeypatch.setattr(Path, 'rename', rename)
    upgrade(archive, target, tmp_path / 'C-user-data')
    assert movements and tree(target) == expected


def test_bundled_public_ca_store_is_preserved_but_private_pem_is_not_managed(tmp_path):
    archive, expected = package(tmp_path, '0.2.0-test.6',
                                extra={'_internal/certifi/cacert.pem':b'public CA certificates'})
    target = tmp_path / 'app'
    upgrade(archive, target, tmp_path / 'data')
    assert tree(target) == expected
    invalid, _ = package(tmp_path, '0.2.0-test.6', extra={'_internal/private.pem':b'private'})
    with pytest.raises(InstallError, match='用户数据类型'):
        upgrade(invalid, tmp_path / 'other-app', tmp_path / 'data')
