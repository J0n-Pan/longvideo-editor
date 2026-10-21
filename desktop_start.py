"""User-launched desktop entry point; do not relocate sandbox processes."""
import json
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from file_picker import require_interactive_desktop
from version import APP_VERSION

URL = 'http://127.0.0.1:8765'
APP = Path(__file__).resolve().parent


def running_service():
    try:
        with urllib.request.urlopen(URL + '/api/info', timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None


def open_when_ready():
    for _ in range(50):
        if running_service():
            webbrowser.open(URL)
            return
        time.sleep(0.2)


def listener_pids(output):
    pids = set()
    for line in output.splitlines():
        fields = line.split()
        if (len(fields) >= 5 and fields[0].upper() == 'TCP' and
                fields[1] == '127.0.0.1:8765' and fields[2] == '0.0.0.0:0' and
                fields[-1].isdigit()):
            pids.add(int(fields[-1]))
    return pids


def retire_isolated_service(existing):
    expected_exports = APP.parents[1] / 'outputs' / 'short-clips'
    known_app = (existing.get('app_id') == 'longvideo-editor' and
                 Path(existing.get('app_root', '')).resolve() == APP)
    legacy_app = (existing.get('model') == 'paraformer-v2' and
                  Path(existing.get('exports_path', '')).resolve() == expected_exports.resolve())
    if not (known_app or legacy_app):
        raise ValueError('8765端口由其他服务占用，未关闭该服务。')
    with urllib.request.urlopen(URL + '/api/jobs', timeout=3) as response:
        jobs = json.load(response)
    if any(job.get('status') in ('queued', 'running') for job in jobs):
        raise ValueError('旧工作台仍有后台任务，请等待任务完成后重新启动。')
    options = {'capture_output': True, 'encoding': 'utf-8', 'errors': 'replace',
               'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    table = subprocess.run(['netstat.exe', '-ano', '-p', 'tcp'], timeout=8, **options)
    pids = listener_pids(table.stdout) if table.returncode == 0 else set()
    if len(pids) != 1:
        raise ValueError('无法确认旧服务进程，未关闭任何进程。')
    pid = pids.pop()
    print('正在关闭旧的空闲工作台服务，保留已保存的文稿和分组。')
    stopped = subprocess.run(['taskkill.exe', '/PID', str(pid), '/T', '/F'],
                             timeout=10, **options)
    if stopped.returncode:
        raise ValueError(f'无法关闭旧工作台（进程{pid}）。请在任务管理器中结束该旧工作台进程，再双击启动入口。')
    for _ in range(30):
        if not running_service():
            return
        time.sleep(0.2)
    raise ValueError('旧服务尚未退出，请稍后重新启动。')


def main():
    try:
        require_interactive_desktop()
    except ValueError as error:
        print(error)
        return 1
    existing = running_service()
    if existing:
        if existing.get('desktop_picker_ready') and existing.get('app_version') == APP_VERSION:
            webbrowser.open(URL)
            return 0
        try:
            retire_isolated_service(existing)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            print(error)
            return 1
    print('工作台正在启动，稍后会打开浏览器。关闭本窗口将停止服务。')
    threading.Thread(target=open_when_ready, daemon=True).start()
    import uvicorn
    uvicorn.run('app:app', host='127.0.0.1', port=8765)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
