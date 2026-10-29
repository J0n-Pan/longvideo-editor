import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from runtime_paths import default_layout

_layout = default_layout()
APP, ROOT = _layout['app'], _layout['root']
DATA, EXPORTS = _layout['data'], _layout['exports']
TRANSCRIPTS, TOOLS = _layout['transcripts'], _layout['tools']
for folder in (DATA, EXPORTS, TRANSCRIPTS, DATA / 'audio', DATA / 'asr', DATA / 'jobs'):
    folder.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()


def connection():
    c = sqlite3.connect(DATA / 'editor.sqlite3', timeout=30)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS objects(kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))')
    return c


def save(kind, value):
    with LOCK, connection() as c:
        c.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?)',
                  (kind, value['id'], json.dumps(value, ensure_ascii=False)))
    return value


def get(kind, identifier):
    with connection() as c:
        row = c.execute('SELECT data FROM objects WHERE kind=? AND id=?', (kind, identifier)).fetchone()
    if not row:
        raise KeyError(identifier)
    return json.loads(row[0])


def all_objects(kind):
    with connection() as c:
        rows = c.execute('SELECT data FROM objects WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def asset_summaries():
    # Project bodies contain word timestamps; list metadata without loading every transcript.
    fields = ('id', 'name', 'path', 'media', 'asr_status', 'updated_at')
    columns = ','.join(f"json_extract(data,'$.{field}')" for field in fields)
    with connection() as c:
        rows = c.execute(f"SELECT {columns} FROM objects WHERE kind='project' ORDER BY rowid DESC").fetchall()
    return [{**dict(zip(fields, row)), 'media': json.loads(row[3]) if row[3] else None} for row in rows]


def export_directory(check=True):
    return output_directory('exports_path', EXPORTS, check)


def transcript_directory(check=True):
    return output_directory('transcripts_path', TRANSCRIPTS, check)


def output_directory(key, default, check):
    try:
        settings = get('settings', 'workspace')
    except KeyError:
        settings = {}
    path = Path(settings.get(key) or default).resolve()
    if check and not path.is_dir():
        raise ValueError('导出目录不存在，请在设置中重新选择文件夹。')
    return path


def set_export_directory(value):
    return set_output_directory('exports_path', value)


def set_transcript_directory(value):
    return set_output_directory('transcripts_path', value)


def set_output_directory(key, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('请输入完整的导出文件夹路径。')
    value = value.strip().strip('"')
    if any(char in value for char in ('\x00', '\r', '\n')):
        raise ValueError('导出路径包含无效字符。')
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('请输入完整路径，例如 D:\\短视频导出。')
    try:
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path) as probe:
            probe.write(b'longvideo-export-check')
            probe.flush()
    except OSError:
        raise ValueError('无法写入此文件夹，请检查路径和文件夹权限。') from None
    with LOCK:
        try:
            settings = get('settings', 'workspace')
        except KeyError:
            settings = {'id': 'workspace'}
        settings[key] = str(path)
        save('settings', settings)
    return path


def json_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def update_job(identifier, **changes):
    with LOCK:
        job = get('job', identifier)
        job.update(changes, updated_at=time.time())
        return save('job', job)
