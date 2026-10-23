"""Build a data-free Windows onedir ZIP from explicitly selected resources."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

APP = Path(__file__).resolve().parents[1]
WORKSPACE = APP.parents[1]
sys.path.insert(0, str(APP))
from version import APP_VERSION

REPORT = WORKSPACE / 'work' / 'portable-build-result.json'
STATIC = ('index.html', 'app.js', 'style.css', 'personal-logo.png')
RUNTIME_DISTRIBUTIONS = (
    'fastapi', 'starlette', 'uvicorn', 'pydantic', 'pydantic-core', 'requests',
    'urllib3', 'certifi', 'charset-normalizer', 'idna', 'anyio', 'click', 'colorama',
    'h11', 'typing-extensions', 'typing-inspection', 'annotated-types', 'annotated-doc')


def write_report(**data):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def collect_licenses(destination, ffmpeg):
    destination.mkdir(parents=True)
    vendor = ffmpeg.parent.parent
    for name in ('LICENSE', 'README.txt'):
        source = vendor / name
        if not source.is_file():
            raise ValueError(f'FFmpeg 缺少发行许可材料：{name}')
        target = destination / 'FFmpeg' / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
    for name in RUNTIME_DISTRIBUTIONS:
        distribution = importlib.metadata.distribution(name)
        for entry in distribution.files or ():
            lower = entry.name.lower()
            if not (lower.startswith(('license', 'copying', 'notice')) or
                    '.dist-info/licenses/' in entry.as_posix()):
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file():
                continue
            # Names originate in installed wheel metadata, not in user data.
            target = destination / name / entry.name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copyfile(python_license, destination / 'Python-LICENSE.txt')
    tcl = Path(sys.base_prefix) / 'tcl'
    for source in tcl.glob('*/license.terms'):
        shutil.copyfile(source, destination / (source.parent.name + '-license.terms'))


def build(args):
    if os.name != 'nt' or sys.maxsize <= 2**32:
        raise ValueError('请在 Windows x64 Python 中构建。')
    ffmpeg, ffprobe = Path(args.ffmpeg).resolve(), Path(args.ffprobe).resolve()
    if not ffmpeg.is_file() or not ffprobe.is_file():
        raise ValueError('请提供 FFmpeg 和 FFprobe 可执行文件。')
    run = WORKSPACE / 'work' / 'portable-build' / uuid.uuid4().hex[:12]
    run.mkdir(parents=True)
    dist = WORKSPACE / 'outputs' / ('portable-build-' + run.name)
    # Both paths are fresh; PyInstaller will never recursively replace another build.
    if dist.exists():
        raise ValueError('构建目录已存在，请重新运行以生成新目录。')
    staging = run / 'staging'
    static = staging / 'static'
    static.mkdir(parents=True)
    for name in STATIC:
        shutil.copyfile(APP / 'static' / name, static / name)
    licenses = staging / 'THIRD_PARTY'
    collect_licenses(licenses, ffmpeg)
    log_path = run / 'pyinstaller.log'
    command = [sys.executable, '-m', 'PyInstaller', '--onedir', '--console', '--noupx',
               '--name', 'LongVideoEditor', '--distpath', str(dist),
               '--workpath', str(run / 'build'), '--specpath', str(run),
               '--paths', str(APP), '--hidden-import', 'app',
               '--collect-submodules', 'uvicorn',
               '--add-data', f'{static};static', '--add-data', f'{licenses};THIRD_PARTY',
               '--add-binary', f'{ffmpeg};tools', '--add-binary', f'{ffprobe};tools']
    for name in ('imageio_ffmpeg', 'pytest', '_pytest', 'pip', 'PyInstaller',
                 'IPython', 'matplotlib', 'numpy', 'pandas'):
        command.extend(('--exclude-module', name))
    command.append(str(APP / 'desktop_start.py'))
    write_report(status='running', phase='bundling', log=str(log_path))
    with log_path.open('w', encoding='utf-8') as log:
        subprocess.run(command, cwd=APP, check=True, stdout=log, stderr=subprocess.STDOUT)
    package = dist / 'LongVideoEditor'
    executable = package / 'LongVideoEditor.exe'
    shutil.copyfile(APP / 'WINDOWS-QUICKSTART.md', package / '使用说明.md')
    (package / '启动工作台.cmd').write_bytes(
        b'@echo off\r\nsetlocal\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\n'
        b'"%~dp0LongVideoEditor.exe"\r\nif errorlevel 1 pause\r\n')
    # Check without an installed Python on PATH or any inherited cloud credential.
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('PYTHONPATH', 'PYTHONHOME', 'DASHSCOPE_API_KEY')}
    environment.update(LONGVIDEO_DATA=str(run / 'self-check-data'),
                       LONGVIDEO_EXPORTS=str(run / 'self-check-videos'),
                       LONGVIDEO_TRANSCRIPTS=str(run / 'self-check-text'),
                       PATH=os.environ.get('SystemRoot', 'C:/Windows') + '/System32')
    write_report(status='running', phase='self-check', log=str(log_path))
    checked = subprocess.run([str(executable), '--self-check'], cwd=run, env=environment,
                             capture_output=True, timeout=60,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (run / 'self-check.log').write_bytes(checked.stdout + checked.stderr)
    if checked.returncode:
        raise RuntimeError(f'内置环境自检未通过，详情见 {run / "self-check.log"}')
    self_check = json.loads(checked.stdout.decode('utf-8'))
    if not self_check['frozen'] or self_check['status'] != 'passed':
        raise RuntimeError('自检未使用内置运行环境')
    if any(package.rglob('imageio_ffmpeg')):
        raise RuntimeError('包内出现重复 FFmpeg 依赖')
    forbidden = {'.mp4', '.mov', '.mkv', '.mp3', '.wav', '.srt', '.sqlite3', '.db', '.dpapi', '.log'}
    files = sorted(path for path in package.rglob('*') if path.is_file())
    if any(path.suffix.lower() in forbidden or path.name.startswith('.env') for path in files):
        raise RuntimeError('程序包出现运行数据或素材类型文件，停止压缩')
    manifest = {'version': APP_VERSION, 'platform': 'windows-x64',
                'files': [{'path': path.relative_to(package).as_posix(),
                           'bytes': path.stat().st_size, 'sha256': sha256(path)} for path in files]}
    (package / 'package-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = WORKSPACE / 'outputs' / f'LongVideoEditor-{APP_VERSION}-windows-x64.zip'
    temporary = archive.with_suffix('.zip.tmp')
    write_report(status='running', phase='compressing', log=str(log_path))
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, strict_timestamps=False) as zip_file:
        for path in sorted(path for path in package.rglob('*') if path.is_file()):
            zip_file.write(path, 'LongVideoEditor/' + path.relative_to(package).as_posix())
    os.replace(temporary, archive)
    checksum = sha256(archive)
    archive.with_suffix('.zip.sha256').write_text(f'{checksum}  {archive.name}\n', encoding='ascii')
    return {'status': 'built', 'version': APP_VERSION, 'archive': str(archive),
            'package_directory': str(package), 'archive_bytes': archive.stat().st_size,
            'extracted_bytes': sum(p.stat().st_size for p in package.rglob('*') if p.is_file()),
            'sha256': checksum, 'file_count': len(files) + 1,
            'self_check': self_check, 'log': str(log_path),
            'resources': {name: {'bytes': path.stat().st_size, 'sha256': sha256(path)}
                          for name, path in (('ffmpeg', ffmpeg), ('ffprobe', ffprobe))}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--ffprobe', required=True)
    args = parser.parse_args()
    try:
        result = build(args)
    except Exception as error:
        write_report(status='failed', error=str(error))
        raise
    write_report(**result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
