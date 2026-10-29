import copy
import hashlib
from pathlib import Path
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import asr
from credentials import get_key, set_key, key_storage
from file_picker import select_video_files, select_directory, desktop_picker_ready
from domain import composition, normalize_transcript, choose_names
from segmentation import refine_project, SEGMENTATION_REVISION
from media import probe, extract_audio, render, verify_source
from storage import (APP, LOCK, all_objects, get, save, update_job,
                     export_directory, set_export_directory, transcript_directory,
                     set_transcript_directory)
from transcript_export import export_transcript
from version import APP_VERSION

app = FastAPI(title='长素材选句工作台', docs_url=None, redoc_url=None)
pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='longvideo')
cancellations = {}
BUDGET = 10.0
picker_lock = threading.Lock()


@app.middleware('http')
async def local_only(request: Request, call_next):
    if request.url.hostname not in ('localhost', '127.0.0.1'):
        return JSONResponse({'detail': '仅允许本机访问'}, status_code=403)
    if request.method not in ('GET', 'HEAD'):
        origin = request.headers.get('origin')
        expected = f'{request.url.scheme}://{request.url.netloc}'
        if request.headers.get('x-longvideo-client') != '1' or (origin and origin != expected):
            return JSONResponse({'detail': '请求来源无效'}, status_code=403)
        if getattr(app.state, 'stopping', False) and request.url.path != '/api/shutdown':
            return JSONResponse({'detail': '工作台正在退出，请重新启动后操作。'}, status_code=409)
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
    return {'app_id': 'longvideo-editor', 'app_root': str(APP), 'app_version': APP_VERSION,
            'model': asr.MODEL, 'credential_configured': bool(get_key()),
            'credential_storage': key_storage(),
            'desktop_picker_ready': desktop_picker_ready(),
            'background_running': callable(getattr(app.state, 'request_shutdown', None)),
            **output_settings()}


def output_settings():
    return {'exports_path': str(export_directory(check=False)),
            'transcripts_path': str(transcript_directory(check=False))}


def reserved_asr_cost():
    jobs = all_objects('job')
    used = {}
    for j in jobs:
        if j.get('kind') == 'asr' and (j.get('cloud_task_id') or j.get('status') in ('queued', 'running')):
            used[j.get('cloud_task_id') or j['cache_key']] = j.get('estimated_yuan', 0)
    return round(sum(used.values()), 4)


@app.put('/api/settings')
async def settings(request: Request):
    data = await request.json()
    path = await run_in_threadpool(set_export_directory, data.get('exports_path'))
    return {'exports_path': str(path)}


@app.post('/api/settings/folder/pick')
async def choose_output_folder(request: Request):
    data = await request.json()
    kind = data.get('kind')
    if kind not in ('video', 'transcript'):
        raise ValueError('导出位置类型无效。')
    current = export_directory(check=False) if kind == 'video' else transcript_directory(check=False)
    initial_directory = next((path for path in (current, *current.parents) if path.is_dir()), Path.home())

    def choose_and_save():
        if not picker_lock.acquire(blocking=False):
            raise HTTPException(409, '选择窗口已经打开，请先完成选择。')
        try:
            selected = select_directory(initial_directory)
            if not selected:
                return {'canceled': True, **output_settings()}
            setter = set_export_directory if kind == 'video' else set_transcript_directory
            setter(selected)
            return {'canceled': False, **output_settings()}
        finally:
            picker_lock.release()

    return await run_in_threadpool(choose_and_save)


@app.post('/api/credentials')
async def credentials(request: Request):
    data = await request.json()
    try:
        store = set_key(str(data.get('key', '')))
    except OSError as exc:
        raise HTTPException(400, f'本机加密保存不可用（Windows错误{exc.errno}），密钥未保存。')
    pending = [p for p in all_objects('project') if p.get('auto_transcribe_pending')]
    results = await run_in_threadpool(
        lambda: [auto_transcribe_project(p['id']) for p in pending])
    return {'configured': True, 'storage': store, 'auto_transcriptions': results}


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


def import_paths(paths):
    files, seen = [], set()
    for value in paths:
        path = Path(str(value)).expanduser().resolve()
        if not path.exists():
            raise ValueError('文件或目录不存在')
        candidates = sorted(path.iterdir()) if path.is_dir() else [path]
        for candidate in candidates:
            identity = str(candidate).casefold()
            if (candidate.is_file() and identity not in seen and
                    candidate.suffix.lower() in ('.mp4', '.mov', '.mkv', '.m4v', '.webm', '.avi')):
                seen.add(identity)
                files.append(candidate)
    if not files:
        raise ValueError('没有找到支持的视频文件')
    results, pending = [], []
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
            pending.append(project)
        results.append(project)
    for project in pending:
        save('project', project)
    return [{**project, 'auto_transcription': auto_transcribe_project(project['id'])}
            for project in results]


@app.post('/api/import')
async def import_source(request: Request):
    data = await request.json()
    return await run_in_threadpool(import_paths, [data.get('path', '')])


@app.post('/api/import/pick')
async def pick_and_import_source(request: Request):
    data = await request.json()
    initial_directory = Path('D:/test_longvideo')
    if data.get('project_id'):
        initial_directory = Path(get('project', str(data['project_id']))['path']).parent
    if not initial_directory.is_dir():
        initial_directory = Path.home()

    def choose_and_import():
        if not picker_lock.acquire(blocking=False):
            raise HTTPException(409, '文件选择窗口已经打开，请先完成选择。')
        try:
            paths = select_video_files(initial_directory)
            return import_paths(paths) if paths else []
        finally:
            picker_lock.release()

    return await run_in_threadpool(choose_and_import)


@app.get('/api/projects/{pid}')
def project(pid: str):
    return get('project', pid)


@app.post('/api/projects/{pid}/transcript/export')
async def export_project_transcript(pid: str):
    project = get('project', pid)
    return await run_in_threadpool(export_transcript, project, transcript_directory())


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
        finish_asr_error(jid, '转写已取消', e)
    except Exception as e:
        update_job(jid, status='failed', message=asr.clean(e), finished_at=time.time())
        finish_asr_error(jid, '转写失败', e)
    finally:
        cancellations.pop(jid, None)


def finish_asr_error(jid, status, error):
    job = get('job', jid)
    if job['kind'] == 'asr':
        with LOCK:
            p = get('project', job['project_id'])
            p.update(asr_status=status, asr_error=asr.clean(error))
            save('project', p)


def enqueue(kind, pid, payload, operation, **extra):
    with LOCK:
        if getattr(app.state, 'stopping', False):
            raise HTTPException(409, '工作台正在退出，请重新启动后操作。')
        jid = uuid.uuid4().hex[:16]
        job = {'id': jid, 'kind': kind, 'project_id': pid, 'payload': payload,
               'status': 'queued', 'message': '等待执行', 'progress': 0, 'created_at': time.time(),
               'results': [], **extra}
        save('job', job)
        cancellations[jid] = threading.Event()
        pool.submit(job_worker, jid, lambda event: operation(jid, event))
    return job


def auto_transcribe_project(pid):
    with LOCK:
        p = get('project', pid)
        if p['sentences']:
            return {'status': 'ready'}
        active = next((j for j in all_objects('job') if j['kind'] == 'asr' and
                       j['project_id'] == pid and j['status'] in ('queued', 'running')), None)
        if active:
            return {'status': active['status'], 'job_id': active['id']}
        if not get_key():
            p.update(auto_transcribe_pending=True, asr_status='等待密钥')
            save('project', p)
            return {'status': 'needs_key'}
        try:
            job = queue_transcription(pid)
            return {'status': job['status'], 'job_id': job['id']}
        except Exception as error:
            p.update(auto_transcribe_pending=False, asr_status='转写未启动', asr_error=asr.clean(error))
            save('project', p)
            return {'status': 'failed', 'message': asr.clean(error)}


def queue_transcription(pid):
    p = get('project', pid)
    verify_source(p)
    if not get_key():
        raise ValueError('请先在本机设置页保存百炼API Key')
    start, end = 0, p['media']['duration_ms']
    running = [j for j in all_objects('job') if j['kind'] == 'asr' and j['project_id'] == pid and j['status'] in ('queued', 'running')]
    if running:
        return running[0]
    estimated = (end - start) / 1000 * 0.00008
    if reserved_asr_cost() + estimated > BUDGET:
        raise ValueError('本轮ASR估算将超过10元预算，请先核对调用记录')
    p.update(auto_transcribe_pending=False, asr_status='转写中')
    p.pop('asr_error', None)
    save('project', p)
    def operation(jid, cancel):
        t0 = time.monotonic()
        audio, _ = extract_audio(p, start, end,
              lambda x: update_job(jid, progress=x * 0.15, message='提取单声道音频'), cancel)
        extracted_at = time.monotonic()
        raw, version, cached = asr.transcribe(audio,
              lambda x, text: update_job(jid, progress=0.15 + x * 0.75, message=text), cancel,
              lambda task: update_job(jid, cloud_task_id=task))
        sentences = normalize_transcript(raw, pid, start, version)
        with LOCK:
            current = get('project', pid)
            current.update(sentences=sentences, asr_version=version, asr_status='已转写',
                           segmentation_revision=SEGMENTATION_REVISION,
                           recognized_range={'start_ms': start, 'end_ms': end}, updated_at=time.time())
            current.pop('asr_error', None)
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
    plans = choose_names(groups, [f.name for f in export_directory().iterdir()])
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
    destination = export_directory()
    def operation(jid, cancel):
        def progress(value, text, results=None):
            changes = {'progress': value, 'message': text}
            if results is not None:
                changes['results'] = results
            update_job(jid, **changes)
        return render(snapshot, groups, jid, progress, cancel, output_directory=destination)
    return enqueue('export', pid, {'group_ids': [g['id'] for g in groups],
                                   'exports_path': str(destination)}, operation)


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
    return [{'file_name': f.name, 'bytes': f.stat().st_size} for f in sorted(export_directory(check=False).glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True)]


@app.get('/api/exports/{name}')
def exported_file(name: str):
    destination = export_directory(check=False)
    path = (destination / name).resolve()
    if path.parent != destination or path.suffix.lower() not in ('.mp4', '.srt', '.json') or not path.is_file():
        raise HTTPException(404, '导出文件不存在')
    return FileResponse(path, filename=path.name,
                        content_disposition_type='inline' if path.suffix.lower() == '.mp4' else 'attachment')


@app.on_event('startup')
def recover():
    app.state.stopping = False
    for job in all_objects('job'):
        if job['status'] in ('queued', 'running'):
            update_job(job['id'], status='interrupted', message='服务曾中断；识别任务可从缓存/云任务记录恢复')
    for project in all_objects('project'):
        upgraded = refine_project(project)
        if upgraded is not None:
            with LOCK:
                backup_id = project['id'] + ':before-segmentation-v2'
                try:
                    get('transcript_backup', backup_id)
                except KeyError:
                    save('transcript_backup', {'id': backup_id, 'project': project,
                                              'created_at': time.time()})
                save('project', upgraded)


@app.post('/api/shutdown')
def shutdown():
    callback = getattr(app.state, 'request_shutdown', None)
    if not callable(callback):
        raise HTTPException(409, '当前服务由调试窗口管理，请从桌面启动入口使用后台模式。')
    with LOCK:
        if any(j['status'] in ('queued', 'running') for j in all_objects('job')):
            raise HTTPException(409, '后台任务尚未完成，请等待完成后再退出工作台。')
        if picker_lock.locked():
            raise HTTPException(409, '请先关闭文件或文件夹选择窗口，再退出工作台。')
        if not getattr(app.state, 'stopping', False):
            app.state.stopping = True
            timer = threading.Timer(0.5, callback)
            timer.daemon = True
            timer.start()
    return {'stopping': True}


app.mount('/static', StaticFiles(directory=APP / 'static'), name='static')
