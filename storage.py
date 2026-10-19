import json
import os
from pathlib import Path
import sqlite3
import threading
import time

APP = Path(__file__).resolve().parent
ROOT = APP.parents[1]
DATA = Path(os.environ.get('LONGVIDEO_DATA', ROOT / 'work' / 'longvideo-editor-data'))
EXPORTS = Path(os.environ.get('LONGVIDEO_EXPORTS', ROOT / 'outputs' / 'short-clips'))
TOOLS = ROOT / 'work' / 'longvideo-tools'
for folder in (DATA, EXPORTS, DATA / 'audio', DATA / 'asr', DATA / 'jobs'):
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
