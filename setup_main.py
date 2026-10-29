"""Single-file installation and upgrade UI; user data stays outside program files."""
import argparse
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import zipfile

from installer_core import (InstallError, check_package, detected_directory, install_package,
                            installation_plan, new_install_directory,
                            service_info, sha256, user_data_directory)
from runtime_paths import configure_tk_resources
from version import APP_VERSION


def payload(arguments):
    if getattr(sys, 'frozen', False):
        import installer_payload
        if arguments.payload:
            raise InstallError('发布安装程序不接受外部载荷。')
        return Path(sys._MEIPASS) / 'application.zip', installer_payload.SHA256, installer_payload.VERSION
    if not arguments.payload:
        raise InstallError('开发模式请指定 --payload；用户请直接运行安装 EXE。')
    archive = Path(arguments.payload).resolve()
    return archive, sha256(archive), APP_VERSION


def create_shortcut(directory):
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('DASHSCOPE_API_KEY', 'GITHUB_TOKEN', 'GH_TOKEN')}
    environment.update(LVE_PROGRAM_DIRECTORY=str(directory),
                       LVE_SCRIPT_ARGUMENTS='//nologo "' + str(directory / '启动工作台.vbs') + '"')
    script = (
        '$shell = New-Object -ComObject WScript.Shell; '
        '$desktop = [Environment]::GetFolderPath("DesktopDirectory"); '
        '$shortcut = $shell.CreateShortcut((Join-Path $desktop "长素材选句工作台.lnk")); '
        '$shortcut.TargetPath = Join-Path $env:SystemRoot "System32\\wscript.exe"; '
        '$shortcut.Arguments = $env:LVE_SCRIPT_ARGUMENTS; '
        '$shortcut.WorkingDirectory = $env:LVE_PROGRAM_DIRECTORY; '
        '$shortcut.IconLocation = Join-Path $env:LVE_PROGRAM_DIRECTORY "LongVideoEditor.exe"; '
        '$shortcut.Save()')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                             '-Command', script], env=environment, stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise InstallError('快捷方式未能创建，可从程序目录中的启动入口打开工作台。')


def launch(directory):
    existing = service_info()
    if existing and Path(existing.get('app_root', '')).resolve() != (directory / '_internal').resolve():
        raise InstallError('安装已完成；另一个目录的工作台仍在运行，请先退出旧工作台，再打开新版。')
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('_PYI_') and key not in ('PYTHONHOME', 'PYTHONPATH', '_MEIPASS2')}
    environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    subprocess.Popen(['wscript.exe', '//nologo', str(directory / '启动工作台.vbs')],
                     cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


class InstallerWindow:
    def __init__(self, root, archive, digest, version, *, target=None, shortcut=True, open_after=True):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.archive, self.digest, self.version = root, archive, digest, version
        self.events, self.busy = queue.Queue(), False
        self.plan = installation_plan(user_data_directory(), service_info())
        if target:
            self.plan['directory'] = Path(target)
        self.directory = tk.StringVar(value=str(self.plan['directory']))
        self.shortcut, self.open_after = tk.BooleanVar(value=shortcut), tk.BooleanVar(value=open_after)
        self.status = tk.StringVar()
        root.title('长素材选句工作台 · 安装与升级')
        root.configure(background='#f4f6f9')
        root.resizable(False, False)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TFrame', background='#f4f6f9')
        style.configure('TLabel', background='#f4f6f9', foreground='#344456', font=('Microsoft YaHei', 10))
        style.configure('TCheckbutton', background='#f4f6f9', font=('Microsoft YaHei', 10))
        style.configure('TButton', font=('Microsoft YaHei', 10), padding=(12, 7))
        style.configure('Accent.TButton', background='#176a68', foreground='white')
        style.map('Accent.TButton', background=[('active', '#125b59'), ('disabled', '#8caaa9')])
        style.configure('TProgressbar', background='#176a68', troughcolor='#e4ecea')
        container = ttk.Frame(root, padding=26)
        container.pack(fill='both', expand=True)
        heading = ttk.Frame(container)
        heading.pack(fill='x', pady=(0, 16))
        try:
            with zipfile.ZipFile(archive) as zipped:
                data = zipped.read('LongVideoEditor/_internal/static/personal-logo.png')
            logo = tk.PhotoImage(data=base64.b64encode(data).decode())
            self.logo = logo.subsample(max(1, logo.width() // 48))
            ttk.Label(heading, image=self.logo).pack(side='left', padx=(0, 12))
        except (OSError, KeyError, ValueError, tk.TclError):
            self.logo = None
        titles = ttk.Frame(heading)
        titles.pack(side='left')
        ttk.Label(titles, text='长素材选句工作台', font=('Microsoft YaHei', 18, 'bold')).pack(anchor='w')
        ttk.Label(titles, text=f'安装与升级 · {version}', foreground='#6e7b89').pack(anchor='w', pady=(4, 0))
        ttk.Label(container, text='更新程序、清理过时文件，继续沿用项目、分组和已保存的密钥。').pack(anchor='w')
        self.location_label = ttk.Label(container, text='程序位置')
        self.location_label.pack(anchor='w', pady=(20, 7))
        location = ttk.Frame(container)
        location.pack(fill='x')
        self.entry = ttk.Entry(location, textvariable=self.directory, width=57, state='readonly', font=('Microsoft YaHei', 10))
        self.entry.pack(side='left', fill='x', expand=True)
        self.browse_button = ttk.Button(location, text='更改', command=self.browse)
        self.browse_button.pack(side='right', padx=(10, 0))
        options = ttk.Frame(container)
        options.pack(fill='x', pady=(16, 12))
        self.checks = [
            ttk.Checkbutton(options, text='创建桌面快捷方式', variable=self.shortcut),
            ttk.Checkbutton(options, text='完成后打开工作台', variable=self.open_after)]
        for check in self.checks:
            check.pack(side='left', padx=(0, 24))
        self.bar = ttk.Progressbar(container, maximum=1, length=580)
        self.bar.pack(fill='x', pady=(6, 10))
        ttk.Label(container, textvariable=self.status, wraplength=590, foreground='#6e7b89').pack(anchor='w')
        footer = ttk.Frame(container)
        footer.pack(fill='x', pady=(22, 0))
        self.install_button = ttk.Button(footer, text='安装／升级', style='Accent.TButton', command=self.start)
        self.install_button.pack(side='right')
        self.close_button = ttk.Button(footer, text='取消', command=self.close)
        self.close_button.pack(side='right', padx=(0, 10))
        self.apply_plan()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.update_idletasks()
        width, height = max(680, root.winfo_reqwidth()), max(420, root.winfo_reqheight())
        root.geometry(f'{width}x{height}+{max(0,(root.winfo_screenwidth()-width)//2)}+'
                      f'{max(0,(root.winfo_screenheight()-height)//2)}')
        root.after(80, self.poll)

    def browse(self):
        if self.plan['mode'] == 'locate':
            self.plan = installation_plan(user_data_directory(), service_info())
            self.directory.set(str(self.plan['directory']))
            self.apply_plan()
            return
        if self.plan['mode'] != 'new':
            return
        from tkinter import filedialog
        current = Path(self.directory.get())
        initial = next((p for p in (current, *current.parents) if p.is_dir()), Path.home())
        selected = filedialog.askdirectory(parent=self.root, title='选择安装磁盘或父文件夹',
                                           initialdir=str(initial), mustexist=True)
        if selected:
            self.directory.set(str(new_install_directory(selected)))

    def apply_plan(self):
        mode = self.plan['mode']
        if mode == 'update':
            self.location_label.configure(text='已安装位置（自动原位更新）')
            self.browse_button.pack_forget()
            self.install_button.configure(text='更新到 ' + self.version)
            self.install_button.state(['!disabled'])
            self.status.set('已识别旧版，点击更新即可。项目、缓存、导出位置和已保存密钥继续使用。')
        elif mode == 'locate':
            self.location_label.configure(text='检测到已有用户数据，正在等待识别旧版程序')
            self.browse_button.configure(text='重新检测')
            self.install_button.state(['disabled'])
            self.status.set('旧 ZIP 版本未记录安装位置。请先打开旧工作台，再点击“重新检测”，即可在原位置更新。')
        else:
            self.location_label.configure(text='首次安装位置')
            self.browse_button.configure(text='更改')
            self.install_button.configure(text='安装')
            self.install_button.state(['!disabled'])
            self.status.set('选择磁盘或父文件夹后，会安装到其中的 LongVideoEditor 子目录；可保留默认位置。')

    def close(self):
        if self.busy:
            from tkinter import messagebox
            messagebox.showinfo('正在安装', '程序文件正在更新，请等待安装完成。', parent=self.root)
        else:
            self.root.destroy()

    def start(self):
        if self.plan['mode'] == 'locate':
            return
        target = self.directory.get()
        self.busy = True
        for widget in [self.entry, self.browse_button, self.install_button, self.close_button, *self.checks]:
            widget.state(['disabled'])
        def operation():
            try:
                result = install_package(self.archive, target, self.digest,
                    progress=lambda value, message: self.events.put(('progress', (value, message))))
                self.events.put(('done', result))
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=operation, daemon=False).start()

    def poll(self):
        from tkinter import messagebox
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == 'progress':
                    fraction, message = value
                    self.bar['value'] = fraction
                    self.status.set(message)
                else:
                    self.busy = False
                    for widget in [self.entry, self.browse_button, self.install_button, self.close_button, *self.checks]:
                        widget.state(['!disabled'])
                    if event == 'error':
                        self.status.set('安装未完成，原有数据保留。可处理提示后重试。')
                        messagebox.showerror('安装未完成', value, parent=self.root)
                    else:
                        self.status.set('安装完成，原有项目、分组和设置已保留。')
                        self.close_button.configure(text='完成')
                        self.install_button.state(['disabled'])
                        directory = Path(value['directory'])
                        notices = []
                        if value.get('cleanup_warning'):
                            notices.append(value['cleanup_warning'])
                        if value.get('modified_files_backup'):
                            notices.append('修改过的旧程序文件已备份到：' + value['modified_files_backup'])
                        for enabled, operation in ((self.shortcut.get(), create_shortcut),
                                                   (self.open_after.get(), launch)):
                            if enabled:
                                try:
                                    operation(directory)
                                except (OSError, InstallError, subprocess.SubprocessError) as error:
                                    notices.append(str(error))
                        if notices:
                            messagebox.showinfo('安装完成', '\n'.join(notices), parent=self.root)
        except queue.Empty:
            pass
        self.root.after(80, self.poll)


def main(argv=None):
    configure_tk_resources()
    parser = argparse.ArgumentParser(description='长素材选句工作台安装与升级')
    parser.add_argument('--payload', help=argparse.SUPPRESS)
    parser.add_argument('--self-check', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--install', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--target', help=argparse.SUPPRESS)
    parser.add_argument('--report', help=argparse.SUPPRESS)
    parser.add_argument('--no-shortcut', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--no-launch', action='store_true', help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    try:
        archive, digest, version = payload(arguments)
        if arguments.self_check:
            import tkinter
            actual_version, rows = check_package(archive, digest)
            if actual_version != version or version != APP_VERSION:
                raise InstallError('安装器版本与程序版本不一致。')
            report = {'status': 'passed', 'version': version, 'payload_sha256': digest,
                      'files': len(rows) + 1, 'tcl': tkinter.Tcl().eval('info patchlevel')}
        elif arguments.install:
            if not arguments.target:
                raise InstallError('静默安装必须指定 --target。')
            report = {'status': 'installed', **install_package(archive, arguments.target, digest)}
        else:
            import tkinter as tk
            root = tk.Tk()
            InstallerWindow(root, archive, digest, version, target=arguments.target,
                            shortcut=not arguments.no_shortcut, open_after=not arguments.no_launch)
            root.mainloop()
            return 0
        if arguments.report:
            Path(arguments.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout is not None:
            print(json.dumps(report, ensure_ascii=False))
        return 0
    except Exception as error:
        if arguments.report:
            Path(arguments.report).write_text(json.dumps({'status': 'failed', 'message': str(error)},
                                                         ensure_ascii=False), encoding='utf-8')
        elif arguments.install or arguments.self_check:
            if sys.stderr is not None:
                print(error, file=sys.stderr)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(error), '安装未完成', 0x10 | 0x10000)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
