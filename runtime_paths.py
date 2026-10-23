"""Keep bundled resources separate from each Windows user's writable data."""
import os
from pathlib import Path
import sys


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def configure_tk_resources(app=None):
    if not is_frozen():
        return
    app = Path(app) if app is not None else Path(__file__).resolve().parent
    for key, name in (('TCL_LIBRARY', '_tcl_data'), ('TK_LIBRARY', '_tk_data')):
        path = app / name
        if not path.is_dir():
            continue
        value = str(path)
        # Tcl 8.6's path normalization can lose a Windows known-folder segment.
        # An extended absolute path preserves the supplied directory verbatim.
        if os.name == 'nt' and not value.startswith('\\\\?\\'):
            value = ('\\\\?\\UNC\\' + value[2:]) if value.startswith('\\\\') else '\\\\?\\' + value
        os.environ[key] = value


def default_layout(*, app=None, environment=None, home=None, frozen=None):
    app = Path(app) if app is not None else Path(__file__).resolve().parent
    environment = os.environ if environment is None else environment
    home = Path.home() if home is None else Path(home)
    frozen = is_frozen() if frozen is None else frozen
    root = app.parents[1]
    if frozen:
        local = Path(environment.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
        data = local / 'LongVideoEditor'
        exports = home / 'Videos' / 'LongVideoEditor'
        transcripts = home / 'Documents' / 'LongVideoEditor'
        tools = app / 'tools'
    else:
        data = root / 'work' / 'longvideo-editor-data'
        exports = root / 'outputs' / 'short-clips'
        transcripts = root / 'outputs' / 'transcripts'
        tools = root / 'work' / 'longvideo-tools'
    return {'app': app, 'root': root,
            'data': Path(environment.get('LONGVIDEO_DATA') or data),
            'exports': Path(environment.get('LONGVIDEO_EXPORTS') or exports),
            'transcripts': Path(environment.get('LONGVIDEO_TRANSCRIPTS') or transcripts),
            'tools': tools}
