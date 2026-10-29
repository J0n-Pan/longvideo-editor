"""Wrap the verified application ZIP in one Windows install/upgrade EXE."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
sys.path.insert(0, str(APP))
from version import APP_VERSION

REPORT = ROOT / 'work/installer-build-result.json'


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def write_report(**value):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def build():
    application = json.loads((ROOT / 'work/portable-build-result.json').read_text(encoding='utf-8'))
    if application.get('status') != 'built' or application.get('version') != APP_VERSION:
        raise ValueError('请先构建并验证同版本的程序 ZIP。')
    archive = Path(application['archive'])
    if digest(archive) != application['sha256']:
        raise ValueError('程序 ZIP 与构建记录不一致。')
    run = ROOT / 'work/installer-build' / uuid.uuid4().hex[:12]
    run.mkdir(parents=True)
    staging, output = run / 'payload', run / 'dist'
    staging.mkdir()
    shutil.copyfile(archive, staging / 'application.zip')
    shutil.copytree(Path(application['package_directory']) / '_internal/THIRD_PARTY', staging / 'THIRD_PARTY')
    (staging / 'installer_payload.py').write_text(
        f"VERSION = {APP_VERSION!r}\nSHA256 = {application['sha256']!r}\n", encoding='utf-8')
    name = f'LongVideoEditor-{APP_VERSION}-windows-x64-setup'
    command = [sys.executable, '-m', 'PyInstaller', '--onefile', '--windowed', '--noupx',
               '--name', name, '--distpath', str(output), '--workpath', str(run / 'build'),
               '--specpath', str(run), '--paths', str(APP), '--paths', str(staging),
               '--hidden-import', 'installer_payload', '--add-data', f'{staging / "application.zip"};.',
               '--add-data', f'{staging / "THIRD_PARTY"};THIRD_PARTY']
    for module in ('fastapi', 'uvicorn', 'pydantic', 'requests', 'imageio_ffmpeg', 'pytest',
                   '_pytest', 'pip', 'PyInstaller', 'numpy', 'pandas', 'matplotlib', 'PIL'):
        command.extend(('--exclude-module', module))
    command.append(str(APP / 'setup_main.py'))
    log = run / 'build.log'
    write_report(status='running', phase='bundling', version=APP_VERSION, log=str(log))
    with log.open('w', encoding='utf-8') as stream:
        subprocess.run(command, cwd=APP, check=True, stdout=stream, stderr=subprocess.STDOUT)
    built = output / (name + '.exe')
    checked_report = run / 'self-check.json'
    environment = {k:v for k,v in os.environ.items() if k not in ('DASHSCOPE_API_KEY','PYTHONHOME','PYTHONPATH')}
    environment.update(LOCALAPPDATA=str(run / 'user-local'), LONGVIDEO_DATA=str(run / 'user-data'))
    write_report(status='running', phase='self-check', version=APP_VERSION, log=str(log))
    completed = subprocess.run([str(built), '--self-check', '--report', str(checked_report)],
                               env=environment, cwd=run, timeout=120,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    result = json.loads(checked_report.read_text(encoding='utf-8')) if checked_report.is_file() else {}
    if completed.returncode or result.get('status') != 'passed' or result.get('version') != APP_VERSION:
        raise RuntimeError('安装程序自检未通过，停止分发。')
    installer = ROOT / 'outputs' / built.name
    temporary = installer.with_suffix('.exe.tmp')
    shutil.copyfile(built, temporary)
    os.replace(temporary, installer)
    checksum = digest(installer)
    installer.with_name(installer.name + '.sha256').write_text(
        f'{checksum}  {installer.name}\n', encoding='ascii')
    return {'status':'built', 'version':APP_VERSION, 'installer':str(installer),
            'installer_bytes':installer.stat().st_size, 'sha256':checksum,
            'payload_archive':str(archive), 'payload_sha256':application['sha256'],
            'self_check':result, 'log':str(log)}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        result = build()
    except Exception as error:
        write_report(status='failed', version=APP_VERSION, error=str(error))
        raise
    write_report(**result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
