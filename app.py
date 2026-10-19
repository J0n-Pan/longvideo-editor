import copy
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import asr
from credentials import get_key, set_key, key_storage
from domain import composition, make_item, normalize_transcript, choose_names
from media import probe, extract_audio, render, binaries, verify_source
from storage import APP, DATA, EXPORTS, LOCK, all_objects, get, save, update_job, json_write

app = FastAPI(title='长素材选句工作台', docs_url=None, redoc_url=None)
pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='longvideo')
cancellations = {}
BUDGET = 10.0


@app.middleware('http')
async def local_only(request: Request, call_next):
    if request.url.hostname not in ('localhost', '127.0.0.1'):
        return JSONResponse({'detail': '仅允许本机访问'}, status_code=403)
    if request.method not in ('GET', 'HEAD'):
        origin = request.headers.get('origin')
        expected = f'{request.url.scheme}://{request.url.netloc}'
        if request.headers.get('x-longvideo-client') != '1' or (origin and origin != expected):
            return JSONResponse({'detail': '请求来源无效'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(KeyError)
async def missing(request, exc):
    return JSONResponse({'detail': '项目或任务不存在'}, status_code=404)


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({'detail': asr.clean(exc)}, status_code=400)


@app.get('/')
def index():
    return FileResponse(APP / 'static' / 'index.html')


@app.get('/api/info')
def info():
    jobs = all_objects('job')
    used = {}
    for j in jobs:
        if j.get('kind') == 'asr' and (j.get('cloud_task_id') or j.get('status') in ('queued', 'running')):
            used[j.get('cloud_task_id') or j['cache_key']] = j.get('estimated_yuan', 0)
    return {'model': asr.MODEL, 'credential_configured': bool(get_key()),
            'credential_storage': key_storage(),
            'budget_yuan': BUDGET, 'estimated_reserved_yuan': round(sum(used.values()), 4),
            'exports_path': str(EXPORTS), 'source_folder': 'D:\test_longvideo', 'temporary_storage': True}


@app.post('/api/credentials')
async def credentials(request: Request):
    data = await request.json()
    try:
        store = set_key(str(data.get('key', '')))
    except OSError as exc:
        raise HTTPException(400, f'本机加密保存不可用（Windows错误{exc.errno}），密钥未保存。')
    return {'configured': True, 'storage': store}


@app.post('/api/credentials/check')
def credentials_check():
    try:
        return asr.check_access()
    except Exception as e:
        raise HTTPException(400, asr.clean(e))


@app.get('/api/projects')
def projects():
    return [{k: p.get(k) for k in ('id', 'name', 'path', 'media', 'asr_status', 'updated_at')}
            for p in all_objects('project')]


@app.post('/api/import')
async def import_source(request: Request):
    data = await request.json()
    path = Path(str(data.get('path', ''))).expanduser().resolve()
    if not path.exists():
        raise ValueError('文件或目录不存在')
    files = sorted(path.iterdir()) if path.is_dir() else [path]
    files = [p for p in files if p.is_file() and p.suffix.lower() in ('.mp4', '.mov', '.mkv', '.m4v', '.webm', '.avi')]
    if not files:
        raise ValueError('没有找到支持的视频文件')
    results = []
    for source in files:
        stat = source.stat()
        identity = hashlib.sha256(f'{source}|{stat.st_size}|{stat.st_mtime_ns}'.encode()).hexdigest()[:20]
        try:
            project = get('project', identity)
        except KeyError:
            media = probe(source)
            if not media.get('audio_codec'):
                raise ValueError(f'{source.name}没有可识别音轨')
            project = {'id': identity, 'name': source.stem, 'path': str(source), 'media': media,
                       'source_size': stat.st_size, 'source_mtime_ns': stat.st_mtime_ns,
                       'sentences': [], 'groups': [{'id': uuid.uuid4().hex[:12], 'name': '切片001', 'items': []}],
                       'asr_status': '未转写', 'asr_version': '', 'updated_at': time.time()}
            save('project', project)
        results.append(project)
    return results


@app.get('/api/projects/{pid}')
def project(pid: str):
    return get('project', pid)


@app.put('/api/projects/{pid}/groups')
async def save_groups(pid: str, request: Request):
    data = await request.json()
    groups = data.get('groups')
    if not isinstance(groups, list) or len(groups) > 200:
        raise ValueError('分组数据无效')
    with LOCK:
        p = get('project', pid)
        group_ids = set()
        for group in groups:
            if not group.get('id') or group['id'] in group_ids:
                raise ValueError('分组编号无效或重复')
            group_ids.add(group['id'])
            if not str(group.get('name', '')).strip():
                raise ValueError('组名称不能为空')
            for item in group.get('items', []):
                if not 0 <= float(item['start_ms']) < float(item['end_ms']) <= p['media']['duration_ms']:
                    raise ValueError('片段时间超出素材范围')
        p.update(groups=groups, updated_at=time.time())
        save('project', p)
    return {'saved': True, 'updated_at': p['updated_at']}


@app.put('/api/projects/{pid}/sentence/{sid}')
async def correct_sentence(pid: str, sid: str, request: Request):
    data = await request.json()
    text = str(data.get('text', '')).strip()
    if not text:
        raise ValueError('文本不能为空')
    with LOCK:
        p = get('project', pid)
        for s in p['sentences']:
            if s['id'] == sid:
                s['text'] = text
        for g in p['groups']:
            for item in g['items']:
                if item['id'] == sid:
                    item['text'] = text
        save('project', p)
    return {'saved': True}


@app.get('/api/projects/{pid}/media')
def video(pid: str):
    p = get('project', pid)
    path = Path(p['path'])
    if not path.is_file():
        raise HTTPException(404, '原素材已移动，请重新导入')
    return FileResponse(path, media_type='video/mp4')


def job_worker(jid, operation):
    update_job(jid, status='running', message='准备中', started_at=time.time())
    try:
        if cancellations[jid].is_set():
            raise InterruptedError('任务已取消，未开始执行')
        results = operation(cancellations[jid])
        update_job(jid, status='succeeded', progress=1, message='已完成', results=results, finished_at=time.time())
    except InterruptedError as e:
        update_job(jid, status='canceled', message=asr.clean(e), finished_at=time.time())
    except Exception as e:
        update_job(jid, status='failed', message=asr.clean(e), finished_at=time.time())
    finally:
        cancellations.pop(jid, None)


def enqueue(kind, pid, payload, operation, **extra):
    jid = uuid.uuid4().hex[:16]
    job = {'id': jid, 'kind': kind, 'project_id': pid, 'payload': payload,
           'status': 'queued', 'message': '等待执行', 'progress': 0, 'created_at': time.time(),
           'results': [], **extra}
    save('job', job)
    cancellations[jid] = threading.Event()
    pool.submit(job_worker, jid, lambda event: operation(jid, event))
    return job


@app.post('/api/projects/{pid}/transcribe')
async def start_asr(pid: str, request: Request):
    data = await request.json()
    p = get('project', pid)
    verify_source(p)
    if not get_key():
        raise ValueError('请先在本机设置页保存百炼API Key')
    start, end = int(data.get('start_ms', 0)), data.get('end_ms')
    end = int(end) if end is not None else p['media']['duration_ms']
    if not 0 <= start < end <= p['media']['duration_ms']:
        raise ValueError('识别范围无效')
    running = [j for j in all_objects('job') if j['kind'] == 'asr' and j['project_id'] == pid and j['status'] in ('queued', 'running')]
    if running:
        return running[0]
    estimated = (end - start) / 1000 * 0.00008
    if info()['estimated_reserved_yuan'] + estimated > BUDGET:
        raise ValueError('本轮ASR估算将超过10元预算，请先核对调用记录')
    def operation(jid, cancel):
        t0 = time.monotonic()
        audio, seconds = extract_audio(p, start, end,
              lambda x: update_job(jid, progress=x * 0.15, message='提取单声道音频'), cancel)
        extracted_at = time.monotonic()
        raw, version, cached = asr.transcribe(audio,
              lambda x, text: update_job(jid, progress=0.15 + x * 0.75, message=text), cancel,
              lambda task: update_job(jid, cloud_task_id=task))
        sentences = normalize_transcript(raw, pid, start, version)
        with LOCK:
            current = get('project', pid)
            current.update(sentences=sentences, asr_version=version, asr_status='已转写',
                           recognized_range={'start_ms': start, 'end_ms': end}, updated_at=time.time())
            save('project', current)
        timing = {'extract_seconds': round(extracted_at - t0, 3),
                  'cloud_seconds': round(time.monotonic() - extracted_at, 3), 'cached': cached,
                  'audio_bytes': audio.stat().st_size, 'sentences': len(sentences),
                  'words': sum(len(s['words']) for s in sentences), 'estimated_yuan': estimated}
        update_job(jid, timing=timing)
        return timing
    return enqueue('asr', pid, {'start_ms': start, 'end_ms': end}, operation,
                   estimated_yuan=estimated, cache_key=f'{pid}:{start}:{end}')


@app.post('/api/projects/{pid}/export-plan')
async def export_plan(pid: str, request: Request):
    data = await request.json()
    p = get('project', pid)
    verify_source(p)
    ids = data.get('group_ids')
    groups = [g for g in p['groups'] if g['items'] and (not ids or g['id'] in ids)]
    plans = choose_names(groups, [f.name for f in EXPORTS.iterdir()])
    return [{**plan, 'duration_ms': sum(s['duration_ms'] for s in composition(group, p['media']['duration_ms'], p['media']['fps'])),
             'sentence_count': len(group['items'])} for group, plan in zip(groups, plans)]


@app.post('/api/projects/{pid}/export')
async def start_export(pid: str, request: Request):
    data = await request.json()
    p = get('project', pid)
    ids = data.get('group_ids')
    groups = [g for g in p['groups'] if g['items'] and (not ids or g['id'] in ids)]
    if not groups:
        raise ValueError('没有可导出的非空分组')
    snapshot = copy.deepcopy(p)
    groups = copy.deepcopy(groups)
    def operation(jid, cancel):
        def progress(value, text, results=None):
            changes = {'progress': value, 'message': text}
            if results is not None:
                changes['results'] = results
            update_job(jid, **changes)
        return render(snapshot, groups, jid, progress, cancel)
    return enqueue('export', pid, {'group_ids': [g['id'] for g in groups]}, operation)


@app.get('/api/jobs')
def jobs():
    return all_objects('job')[:60]


@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid: str):
    if jid in cancellations:
        cancellations[jid].set()
    return {'cancel_requested': True}


@app.get('/api/exports')
def exports():
    return [{'file_name': f.name, 'bytes': f.stat().st_size} for f in sorted(EXPORTS.glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True)]


@app.get('/api/exports/{name}')
def exported_file(name: str):
    path = (EXPORTS / name).resolve()
    if path.parent != EXPORTS.resolve() or path.suffix.lower() not in ('.mp4', '.srt', '.json') or not path.is_file():
        raise HTTPException(404, '导出文件不存在')
    return FileResponse(path, filename=path.name,
                        content_disposition_type='inline' if path.suffix.lower() == '.mp4' else 'attachment')


@app.on_event('startup')
def recover():
    for job in all_objects('job'):
        if job['status'] in ('queued', 'running'):
            update_job(job['id'], status='interrupted', message='服务曾中断；识别任务可从缓存/云任务记录恢复')


app.mount('/static', StaticFiles(directory=APP / 'static'), name='static')
