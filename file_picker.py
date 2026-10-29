"""Choose local source paths in a desktop dialog without uploading video bytes."""
import json
import os
from pathlib import Path
import subprocess
import sys


def current_desktop_name():
    if os.name != 'nt':
        return ''
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    user.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user.GetThreadDesktop.restype = wintypes.HANDLE
    user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    name, needed = ctypes.create_unicode_buffer(256), wintypes.DWORD()
    desktop = user.GetThreadDesktop(kernel.GetCurrentThreadId())
    if not desktop or not user.GetUserObjectInformationW(
            desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
        return ''
    return name.value


def desktop_picker_ready():
    name = current_desktop_name()
    return bool(name) and not name.startswith('CodexSandboxDesktop-')


def require_interactive_desktop():
    if not desktop_picker_ready():
        raise ValueError('当前服务运行在隔离桌面，无法显示选择窗口。请从资源管理器双击“启动工作台”入口后重试。')


def select_video_files(initial_directory):
    return select_paths(initial_directory, '--choose')


def select_directory(initial_directory):
    paths = select_paths(initial_directory, '--directory')
    if len(paths) > 1:
        raise ValueError('每次只能选择一个导出文件夹，请重试。')
    return paths[0] if paths else None


def select_paths(initial_directory, mode):
    require_interactive_desktop()
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--picker-mode', 'directory' if mode == '--directory' else 'video',
                   '--initial-directory', str(initial_directory)]
    else:
        command = [getattr(sys, '_base_executable', sys.executable), '-X', 'utf8',
                   str(Path(__file__).resolve()), mode, str(initial_directory)]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL, capture_output=True, encoding='utf-8',
            errors='replace', timeout=600,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise ValueError('选择窗口等待超时，请重新点击对应的选择按钮。') from None
    except OSError:
        raise ValueError('无法启动系统文件选择窗口，请从 Windows 桌面启动工作台后重试。') from None
    if result.returncode:
        raise ValueError('无法打开本机文件选择窗口，请从 Windows 桌面启动工作台后重试。')
    try:
        paths = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError('未能读取文件选择结果，请重试。') from None
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise ValueError('文件选择结果无效，请重试。')
    return paths


def show_dialog(initial_directory, directory=False):
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        if directory:
            selected = filedialog.askdirectory(
                parent=root, title='选择导出文件夹', initialdir=initial_directory, mustexist=True)
            return [selected] if selected else []
        paths = filedialog.askopenfilenames(
            parent=root, title='选择要导入的视频素材',
            initialdir=initial_directory,
            filetypes=[('视频素材', '*.mp4 *.mov *.mkv *.m4v *.webm *.avi'),
                       ('所有文件', '*.*')])
        return list(paths)
    finally:
        root.destroy()


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in ('--choose', '--directory'):
        raise SystemExit(2)
    print(json.dumps(show_dialog(sys.argv[2], sys.argv[1] == '--directory'), ensure_ascii=False))
