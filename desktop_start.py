"""User-launched desktop entry point; do not relocate sandbox processes."""
import argparse
from contextlib import contextmanager
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from file_picker import require_interactive_desktop, show_dialog
from runtime_paths import configure_tk_resources
from version import APP_VERSION

PORT = int(os.environ.get('LONGVIDEO_PORT', '8765'))
URL = f'http://127.0.0.1:{PORT}'
APP = Path(__file__).resolve().parent


def running_service():
    try:
        with urllib.request.urlopen(URL + '/api/info', timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None


@contextmanager
def instance_lock(path):
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ValueError('工作台正在启动，请稍后再次打开。') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def report_error(error):
    if os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(error), '长素材选句工作台', 0x10 | 0x10000)
    else:
        print(error, file=sys.stderr)


def launch_background():
    from storage import DATA
    log = DATA / 'logs' / 'workbench.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    command = ([sys.executable, '--background-worker'] if getattr(sys, 'frozen', False)
               else [sys.executable, '-X', 'utf8', str(APP / 'desktop_start.py'), '--background-worker'])
    process = subprocess.Popen(
        command, cwd=APP, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
        creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0) |
                       getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        existing = running_service()
        if (existing and existing.get('app_id') == 'longvideo-editor' and
                existing.get('app_root') == str(APP) and
                existing.get('app_version') == APP_VERSION and existing.get('background_running')):
            webbrowser.open(URL)
            return 0
        if process.poll() is not None:
            raise ValueError(f'后台启动失败，请查看日志：{log}')
        time.sleep(0.2)
    raise ValueError(f'后台启动尚未完成，请稍后重新打开工作台。日志：{log}')


def background_worker():
    from storage import DATA
    log = DATA / 'logs' / 'workbench.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log, maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    try:
        with instance_lock(DATA / f'worker-{PORT}.lock'):
            import app as application
            import uvicorn
            server = uvicorn.Server(uvicorn.Config(
                application.app, host='127.0.0.1', port=PORT, access_log=False, log_config=None))
            application.app.state.request_shutdown = lambda: setattr(server, 'should_exit', True)
            try:
                server.run()
            finally:
                application.app.state.request_shutdown = None
    except Exception:
        logging.exception('工作台后台运行失败')
        return 1
    finally:
        handler.close()
    return 0


def listener_pids(output):
    pids = set()
    for line in output.splitlines():
        fields = line.split()
        if (len(fields) >= 5 and fields[0].upper() == 'TCP' and
                fields[1] == f'127.0.0.1:{PORT}' and fields[2] == '0.0.0.0:0' and
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
        raise ValueError(f'{PORT}端口由其他服务占用，未关闭该服务。')
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


def self_check():
    # No credential reads or cloud requests. Check the shipped runtime itself.
    import sqlite3
    import ssl
    import tkinter
    import fastapi
    from media import binaries
    from storage import APP, DATA, TOOLS
    tools = binaries()
    versions = {}
    for name, path in tools.items():
        result = subprocess.run([path, '-version'], capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError(f'{name} 无法运行')
        versions[name] = result.stdout.decode('utf-8', errors='replace').splitlines()[0]
    if not all((APP / 'static' / name).is_file()
               for name in ('index.html', 'app.js', 'style.css', 'personal-logo.png')):
        raise ValueError('界面资源不完整')
    report = {'status': 'passed', 'version': APP_VERSION,
              'frozen': bool(getattr(sys, 'frozen', False)), 'tools': versions,
              'tcl': tkinter.Tcl().eval('info patchlevel'),
              'sqlite': sqlite3.sqlite_version, 'ssl': ssl.OPENSSL_VERSION,
              'resource_root': str(APP), 'data_directory': str(DATA),
              'tools_directory': str(TOOLS), 'fastapi': fastapi.__version__}
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main(argv=None):
    configure_tk_resources()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='长素材选句工作台')
    parser.add_argument('--self-check', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--serve-only', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--background-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--picker-mode', choices=('video', 'directory'), help=argparse.SUPPRESS)
    parser.add_argument('--initial-directory', default=str(Path.home()), help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    if arguments.self_check:
        return self_check()
    if arguments.picker_mode:
        require_interactive_desktop()
        print(json.dumps(show_dialog(arguments.initial_directory,
                                    arguments.picker_mode == 'directory'), ensure_ascii=False))
        return 0
    if arguments.serve_only:
        import uvicorn
        uvicorn.run('app:app', host='127.0.0.1', port=PORT)
        return 0
    if arguments.background_worker:
        return background_worker()
    try:
        require_interactive_desktop()
        from storage import DATA
        with instance_lock(DATA / f'start-{PORT}.lock'):
            existing = running_service()
            if existing:
                if (existing.get('app_id') == 'longvideo-editor' and
                        existing.get('app_root') == str(APP) and
                        existing.get('desktop_picker_ready') and existing.get('app_version') == APP_VERSION
                        and existing.get('background_running')):
                    webbrowser.open(URL)
                    return 0
                retire_isolated_service(existing)
            return launch_background()
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        report_error(error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
