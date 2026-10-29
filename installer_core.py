"""Manifest-scoped Windows installation, with rollback and separate user data."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid
import zipfile

APP_ID = 'longvideo-editor'
MANIFEST = 'package-manifest.json'
ROOT_FILES = {'LongVideoEditor.exe', '启动工作台.vbs', '启动工作台.cmd', '使用说明.md', MANIFEST}
DATA_SUFFIXES = {'.mp4', '.mov', '.mkv', '.avi', '.mp3', '.wav', '.m4a', '.srt', '.vtt',
                 '.sqlite', '.sqlite3', '.db', '.dpapi', '.key', '.pem', '.log'}
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
SERVICE_PORT = int(os.environ.get('LONGVIDEO_PORT', '8765'))


class InstallError(ValueError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def relative_path(value):
    if not isinstance(value, str) or not value or any(c in value for c in '\\:<>\"|?*\0'):
        raise InstallError('程序文件清单中存在不安全的路径。')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('', '.', '..') or p.rstrip('. ') != p for p in value.split('/')):
        raise InstallError('程序文件清单中存在不安全的路径。')
    if any(re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)', p, re.I) for p in path.parts):
        raise InstallError('程序文件清单中存在 Windows 保留名称。')
    if value not in ROOT_FILES and (len(path.parts) < 2 or path.parts[0] != '_internal'):
        raise InstallError('程序文件清单包含非程序目录。')
    public_trust_store = value == '_internal/certifi/cacert.pem'
    if ((path.suffix.lower() in DATA_SUFFIXES and not public_trust_store)
            or any(part.startswith('.env') for part in path.parts)):
        raise InstallError('程序文件清单包含用户数据类型，已停止升级。')
    return value


def version_key(value):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-test\.(\d+))?', str(value))
    if not match:
        raise InstallError('无法识别程序版本。')
    return (*map(int, match.groups()[:3]), 1 if match[4] is None else 0, int(match[4] or 0))


def read_manifest(value):
    if not isinstance(value, dict) or value.get('platform') != 'windows-x64':
        raise InstallError('程序文件清单格式无效。')
    version_key(value.get('version'))
    rows, used = {}, set()
    for row in value.get('files', []):
        name = relative_path(row.get('path'))
        if name.casefold() in used or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('sha256', ''))):
            raise InstallError('程序文件清单重复或校验信息无效。')
        if not isinstance(row.get('bytes'), int) or row['bytes'] < 0:
            raise InstallError('程序文件大小记录无效。')
        used.add(name.casefold())
        rows[name] = row
    if not {'LongVideoEditor.exe', '_internal/tools/ffmpeg.exe', '_internal/tools/ffprobe.exe'} <= rows.keys():
        raise InstallError('程序文件清单缺少必需资源。')
    return value['version'], rows


def check_package(archive, expected_digest):
    archive = Path(archive)
    if not re.fullmatch(r'[0-9a-f]{64}', expected_digest) or sha256(archive) != expected_digest:
        raise InstallError('安装包校验失败，请重新下载。')
    with zipfile.ZipFile(archive) as zipped:
        info = zipped.infolist()
        names = [item.filename for item in info]
        if len(names) != len(set(name.casefold() for name in names)):
            raise InstallError('安装包中存在重复文件。')
        prefix = 'LongVideoEditor/'
        for item in info:
            if not item.filename.startswith(prefix) or item.is_dir():
                raise InstallError('安装包目录结构无效。')
            relative_path(item.filename[len(prefix):])
            if stat.S_ISLNK(item.external_attr >> 16):
                raise InstallError('安装包中不允许包含文件链接。')
        manifest = json.loads(zipped.read(prefix + MANIFEST))
        version, rows = read_manifest(manifest)
        if set(names) != {prefix + name for name in rows} | {prefix + MANIFEST}:
            raise InstallError('安装包文件与清单不一致。')
    return version, rows


def no_links(root, path):
    root = Path(root).absolute()
    path = Path(path).absolute()
    if path != root and not path.is_relative_to(root):
        raise InstallError('程序操作路径超出安装目录。')
    for candidate in (path, *path.parents):
        if candidate.exists() or candidate.is_symlink():
            attributes = candidate.lstat()
            if candidate.is_symlink() or getattr(attributes, 'st_file_attributes', 0) & 0x400:
                raise InstallError('安装路径包含链接或未落地的云文件，请选择普通本地目录。')
        if candidate == root:
            break
    if not path.resolve().is_relative_to(root.resolve()):
        raise InstallError('程序操作路径超出安装目录。')
    return path


def user_data_directory(environment=None):
    environment = os.environ if environment is None else environment
    return Path(environment.get('LONGVIDEO_DATA') or
                Path(environment.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'LongVideoEditor')


def validate_target(target, user_data):
    target = Path(target).expanduser()
    if not target.is_absolute():
        raise InstallError('请选择绝对路径。')
    no_links(target, target)
    target = target.resolve()
    protected = {Path(target.anchor), Path.home().resolve(), Path(user_data).resolve()}
    protected.update((Path.home() / name).resolve() for name in ('Desktop', 'Documents', 'Videos', 'Downloads'))
    if (target in protected or Path(user_data).resolve().is_relative_to(target)
            or target.is_relative_to(Path(user_data).resolve())):
        raise InstallError('请选择独立的程序文件夹，不能使用磁盘根目录或用户数据目录。')
    if target.exists() and not target.is_dir():
        raise InstallError('安装位置不是文件夹。')
    if target.is_dir() and any(target.iterdir()):
        if not (target / MANIFEST).is_file() or not (target / 'LongVideoEditor.exe').is_file():
            raise InstallError('该目录不是可识别的旧版程序，请选择旧版工作台目录或空文件夹。')
    return target


def existing_installation(user_data, service=None, *, environment=None):
    environment = os.environ if environment is None else environment
    record = Path(user_data) / 'installation.json'
    candidates = []
    try:
        saved = json.loads(record.read_text(encoding='utf-8'))
        if saved.get('app_id') == APP_ID:
            candidates.append(Path(saved['directory']))
    except (OSError, ValueError, KeyError):
        pass
    if service and service.get('app_id') == APP_ID:
        candidates.append(Path(service.get('app_root', '')).parent)
    candidates.append(Path(environment.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Programs/LongVideoEditor')
    for candidate in candidates:
        try:
            if candidate.is_absolute() and (candidate / 'LongVideoEditor.exe').is_file():
                read_manifest(json.loads((candidate / MANIFEST).read_text(encoding='utf-8')))
                return candidate.resolve()
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def detected_directory(user_data, service=None):
    return existing_installation(user_data, service) or (
        Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Programs/LongVideoEditor')


def installation_plan(user_data, service=None, *, environment=None):
    environment = os.environ if environment is None else environment
    found = existing_installation(user_data, service, environment=environment)
    if found:
        return {'mode': 'update', 'directory': found}
    default = Path(environment.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Programs/LongVideoEditor'
    # Old ZIPs may have user data but no installation record. Never guess which folder to clean.
    mode = 'locate' if (Path(user_data) / 'editor.sqlite3').is_file() else 'new'
    return {'mode': mode, 'directory': default}


def new_install_directory(parent):
    parent = Path(parent).expanduser()
    if not parent.is_absolute():
        raise InstallError('请选择本机磁盘或文件夹。')
    return parent if parent.name.casefold() == 'longvideoeditor' else parent / 'LongVideoEditor'


def service_request(method, path, port=SERVICE_PORT):
    request = Request(f'http://127.0.0.1:{port}/api/{path}', method=method,
                      headers={'X-Longvideo-Client': '1', 'Content-Type': 'application/json'},
                      data=b'{}' if method == 'POST' else None)
    with urlopen(request, timeout=3) as response:
        return json.load(response)


def service_info(port=SERVICE_PORT):
    try:
        return service_request('GET', 'info', port)
    except (URLError, OSError, ValueError):
        return None


def process_image(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        text, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        return Path(text.value) if kernel.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(size)) else None
    finally:
        kernel.CloseHandle(handle)


def target_processes(target):
    table = subprocess.run(['tasklist.exe', '/FI', 'IMAGENAME eq LongVideoEditor.exe', '/FO', 'CSV', '/NH'],
                           stdin=subprocess.DEVNULL, capture_output=True, encoding='utf-8', errors='replace', timeout=10,
                           creationflags=NO_WINDOW)
    return [int(row[1]) for row in csv.reader(table.stdout.splitlines())
            if len(row) > 1 and row[0].casefold() == 'longvideoeditor.exe' and row[1].isdigit()
            and process_image(int(row[1])) == target / 'LongVideoEditor.exe']


def stop_installed_service(target, port=SERVICE_PORT):
    info = service_info(port)
    if not info or info.get('app_id') != APP_ID:
        if (target / 'LongVideoEditor.exe').exists() and target_processes(target):
            raise InstallError('该目录中的工作台仍在运行，无法核对后台任务，请从设置退出后重试。')
        return
    if Path(info.get('app_root', '')).resolve() != (target / '_internal').resolve():
        if (target / 'LongVideoEditor.exe').exists() and target_processes(target):
            raise InstallError('该目录中的工作台仍在运行，请先退出后重试。')
        return
    jobs = service_request('GET', 'jobs', port)
    if any(job.get('status') in ('queued', 'running') for job in jobs):
        raise InstallError('旧版仍有后台任务，请等待完成后再安装。')
    try:
        service_request('POST', 'shutdown', port)
    except HTTPError as error:
        if error.code == 409:
            raise InstallError('旧版暂不能退出，请先关闭选择窗口或等待后台任务完成。') from None
        if error.code not in (404, 405):
            raise InstallError('无法正常关闭旧版，请从旧版界面退出后重试。') from None
        # Legacy console editions have no shutdown endpoint: verify the exact
        # executable path before stopping its idle process.
        table = subprocess.run(['netstat.exe', '-ano', '-p', 'tcp'], stdin=subprocess.DEVNULL, capture_output=True,
                               encoding='utf-8', errors='replace', timeout=8, creationflags=NO_WINDOW)
        pids = {int(parts[-1]) for line in table.stdout.splitlines()
                if len(parts := line.split()) >= 5 and parts[0] == 'TCP'
                and parts[1] == f'127.0.0.1:{port}' and parts[2] == '0.0.0.0:0' and parts[-1].isdigit()}
        if len(pids) != 1 or process_image(next(iter(pids))) != target / 'LongVideoEditor.exe':
            raise InstallError('无法确认旧版进程身份，请先关闭旧版工作台。')
        subprocess.run(['taskkill.exe', '/PID', str(next(iter(pids))), '/T', '/F'], check=True,
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=10, creationflags=NO_WINDOW)
    for _ in range(40):
        remaining = service_info(port)
        if not remaining or Path(remaining.get('app_root', '')).resolve() != (target / '_internal').resolve():
            if not target_processes(target):
                return
        time.sleep(.2)
    raise InstallError('旧版尚未退出，请稍后重试。')


def validate_installed(target, version, stage):
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('DASHSCOPE_API_KEY', 'PYTHONHOME', 'PYTHONPATH', '_MEIPASS2')
                   and not key.startswith('_PYI_')}
    environment.update(PYINSTALLER_RESET_ENVIRONMENT='1',
                       LONGVIDEO_DATA=str(stage / 'check-data'), LONGVIDEO_EXPORTS=str(stage / 'check-video'),
                       LONGVIDEO_TRANSCRIPTS=str(stage / 'check-text'),
                       PATH=os.environ.get('SystemRoot', 'C:/Windows') + '/System32')
    checked = subprocess.run([str(target / 'LongVideoEditor.exe'), '--self-check'], env=environment,
                             cwd=stage, stdin=subprocess.DEVNULL, capture_output=True, timeout=60, creationflags=NO_WINDOW)
    try:
        report = json.loads(checked.stdout.decode('utf-8'))
    except (ValueError, UnicodeError):
        report = {}
    if checked.returncode or report.get('status') != 'passed' or report.get('version') != version:
        raise InstallError('新版运行环境验证失败，正在恢复旧程序。')


@contextmanager
def install_lock(data):
    import msvcrt
    data.mkdir(parents=True, exist_ok=True)
    with (data / 'installer.lock').open('a+b') as handle:
        if not handle.tell():
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise InstallError('另一个安装或升级过程正在运行。') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def empty_directories(root, names):
    folders = {parent for name in names for parent in (root / name).parents if parent != root
               and parent.is_relative_to(root)}
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        no_links(root, folder)
        try:
            folder.rmdir()
        except OSError:
            pass


def remove_stage(stage, target):
    stage = Path(stage).absolute()
    if stage.parent.resolve() != target.parent.resolve() or not stage.name.startswith(f'.{target.name}.upgrade-'):
        raise InstallError('拒绝清理未确认的临时目录。')
    for folder, directories, files in os.walk(stage, followlinks=False):
        for name in [*directories, *files]:
            no_links(stage, Path(folder) / name)
    def clear_readonly(operation, name, error):
        path = no_links(stage, Path(name))
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
        operation(name)
    shutil.rmtree(stage, onexc=clear_readonly)


def install_package(archive, target, expected_digest, *, user_data=None, progress=None,
                    stop_service=stop_installed_service, validator=validate_installed):
    data = Path(user_data) if user_data is not None else user_data_directory()
    notify = progress or (lambda fraction, message: None)
    with install_lock(data):
        notify(.02, '正在校验安装包')
        version, rows = check_package(archive, expected_digest)
        target = validate_target(target, data)
        previous, old = None, {}
        if (target / MANIFEST).exists():
            no_links(target, target / MANIFEST)
            previous, old = read_manifest(json.loads((target / MANIFEST).read_text(encoding='utf-8')))
            if version_key(version) < version_key(previous):
                raise InstallError('安装包版本低于已安装版本，请使用更新的安装包。')
        new_names = [*rows, MANIFEST]
        old_names = set(old)
        if previous:
            old_names.add(MANIFEST)
            if (target / '启动工作台.cmd').is_file():
                old_names.add('启动工作台.cmd')
        for name in old_names | set(new_names):
            candidate = no_links(target, target / name)
            if candidate.exists() and not candidate.is_file():
                raise InstallError(f'程序文件位置被文件夹占用：{name}')
            if candidate.exists() and name in new_names and name not in old_names:
                raise InstallError(f'新程序文件与个人文件冲突，已停止安装：{name}')
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f'.{target.name}.upgrade-', dir=target.parent)).resolve()
        fresh, backup = stage / 'new', stage / 'previous'
        fresh.mkdir()
        moved, installed, modified = [], [], []
        committed = False
        rollback_complete = False
        backup_path = None
        cleanup_warning = None
        try:
            with zipfile.ZipFile(archive) as zipped:
                for index, name in enumerate(new_names):
                    destination = no_links(fresh, fresh / name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open('LongVideoEditor/' + name) as source, destination.open('wb') as output:
                        shutil.copyfileobj(source, output)
                    if name in rows and (destination.stat().st_size != rows[name]['bytes']
                                         or sha256(destination) != rows[name]['sha256']):
                        raise InstallError('解压后的程序文件校验失败。')
                    notify(.05 + .45 * (index + 1) / len(new_names), '正在准备新版程序')
            notify(.52, '正在检查旧版后台任务')
            stop_service(target)
            target.mkdir(exist_ok=True)
            for name in sorted(old_names):
                current = no_links(target, target / name)
                if not current.exists():
                    continue
                if name != MANIFEST and (name not in old or sha256(current) != old[name]['sha256']):
                    modified.append(name)
                stored = no_links(backup, backup / name)
                stored.parent.mkdir(parents=True, exist_ok=True)
                current.rename(stored)
                moved.append(name)
            empty_directories(target, old_names)
            for index, name in enumerate(new_names):
                destination = no_links(target, target / name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                (fresh / name).rename(destination)
                installed.append(name)
                notify(.55 + .25 * (index + 1) / len(new_names), '正在替换程序并清理旧文件')
            notify(.84, '正在验证新版运行环境')
            validator(target, version, stage)
            if modified:
                backup_path = target.parent / f'{target.name}.modified-backup-{uuid.uuid4().hex[:8]}'
                backup_path.mkdir()
                for name in modified:
                    destination = no_links(backup_path, backup_path / name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup / name, destination)
            record = {'app_id': APP_ID, 'version': version, 'directory': str(target), 'updated_at': time.time()}
            temporary = data / f'installation-{uuid.uuid4().hex}.tmp'
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, data / 'installation.json')
            committed = True
        except Exception as error:
            for name in reversed(installed):
                destination = no_links(target, target / name)
                if destination.is_file():
                    destination.unlink()
            empty_directories(target, installed)
            try:
                for name in moved:
                    destination = no_links(target, target / name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    (backup / name).rename(destination)
                rollback_complete = True
            except OSError as rollback_error:
                raise InstallError(f'安装中断，旧程序备份保留在：{backup}') from rollback_error
            prefix = '安装未完成，原有程序文件已恢复。' if moved else '原程序尚未替换。'
            raise InstallError(prefix + str(error)) from error
        finally:
            if committed or rollback_complete:
                try:
                    remove_stage(stage, target)
                except OSError:
                    if not committed:
                        raise
                    cleanup_warning = f'新版已安装，旧程序临时备份未能自动清理：{stage}'
        notify(1, '安装完成，已保留原有用户数据')
        return {'version': version, 'directory': str(target), 'previous_version': previous,
                'replaced_files': len(installed), 'removed_obsolete_files': len(old_names - set(new_names)),
                'modified_files_backup': str(backup_path) if backup_path else None,
                'user_data_directory': str(data), 'cleanup_warning': cleanup_warning}
