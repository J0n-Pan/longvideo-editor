"""Official DashScope HTTP APIs. Never logs authorization or upload policy."""
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse
import uuid

import requests

from credentials import get_key
from storage import DATA, json_write
from media import sha256

MODEL = 'paraformer-v2'
BASE = 'https://dashscope.aliyuncs.com/api/v1'
PARAMETERS = {'channel_id': [0], 'language_hints': ['zh', 'en'],
              'disfluency_removal_enabled': False, 'timestamp_alignment_enabled': True,
              'diarization_enabled': False}


def clean(value):
    value = re.sub(r'sk-[A-Za-z0-9_.-]+', '[REDACTED]', str(value))
    return re.sub(r'https?://\S+', '[remote URL]', value)[:400]


def headers(resolve=False):
    key = get_key()
    if not key:
        raise ValueError('尚未配置百炼API Key，请在设置中保存')
    h = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    if resolve:
        h['X-DashScope-OssResourceResolve'] = 'enable'
    return h


def response_json(response, action):
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        raise RuntimeError(f'{action}失败 HTTP {response.status_code}：' +
                           clean(data.get('code', '')) + ' ' + clean(data.get('message', '')))
    return data


def check_access():
    r = requests.get(BASE + '/uploads', params={'action': 'getPolicy', 'model': MODEL},
                     headers=headers(), timeout=(15, 60))
    data = response_json(r, '获取上传授权')
    if 'data' not in data:
        raise RuntimeError('上传授权响应不完整')
    return {'ok': True, 'model': MODEL, 'upload_available': True}


def upload(audio):
    data = response_json(requests.get(BASE + '/uploads', params={'action': 'getPolicy', 'model': MODEL},
                                     headers=headers(), timeout=(15, 60)), '获取上传授权')['data']
    host = data['upload_host']
    if urlparse(host).scheme != 'https' or not (urlparse(host).hostname or '').endswith('.aliyuncs.com'):
        raise RuntimeError('云端返回了不受信任的上传地址')
    name = uuid.uuid4().hex + audio.suffix
    obj = data['upload_dir'] + '/' + name
    fields = {'OSSAccessKeyId': (None, data['oss_access_key_id']),
              'Signature': (None, data['signature']), 'policy': (None, data['policy']),
              'x-oss-object-acl': (None, data['x_oss_object_acl']),
              'x-oss-forbid-overwrite': (None, data['x_oss_forbid_overwrite']),
              'key': (None, obj), 'success_action_status': (None, '200')}
    with open(audio, 'rb') as f:
        # File must be the last multipart field.
        fields['file'] = (name, f, 'audio/mpeg')
        r = requests.post(host, files=fields, timeout=(20, 300))
    if r.status_code not in (200, 204):
        raise RuntimeError(f'音频临时上传失败 HTTP {r.status_code}')
    return 'oss://' + obj


def transcribe(audio, progress=None, cancel=None, accepted=None):
    if cancel and cancel.is_set():
        raise InterruptedError('任务已取消，未上传音频')
    version = sha256(audio) + '_paraformer_v2_alignment_zh_en'
    cache = DATA / 'asr' / (version + '.json')
    task_file = DATA / 'asr' / (version + '.task.json')
    if cache.exists():
        return json.loads(cache.read_text(encoding='utf-8')), version, True
    if task_file.exists():
        task = json.loads(task_file.read_text(encoding='utf-8'))
    else:
        if progress:
            progress(0.1, '正在上传识别音频（临时私有存储）')
        remote = upload(audio)
        if cancel and cancel.is_set():
            raise InterruptedError('任务已取消，未提交识别')
        h = headers(resolve=True)
        h['X-DashScope-Async'] = 'enable'
        data = response_json(requests.post(BASE + '/services/audio/asr/transcription', headers=h,
                             json={'model': MODEL, 'input': {'file_urls': [remote]},
                                   'parameters': PARAMETERS}, timeout=(20, 90)), '提交转写')
        task_id = data.get('output', {}).get('task_id')
        if not task_id:
            raise RuntimeError('百炼未返回task_id')
        task = {'task_id': task_id, 'submitted_at': time.time()}
        json_write(task_file, task)
    if accepted:
        accepted(task['task_id'])
    deadline = time.monotonic() + 10800
    while time.monotonic() < deadline:
        if cancel and cancel.is_set():
            raise InterruptedError('已停止本地等待，云任务可能继续；重试将优先恢复查询')
        data = response_json(requests.get(BASE + '/tasks/' + task['task_id'], headers=headers(),
                                         timeout=(20, 90)), '查询转写')
        output = data.get('output', {})
        state = output.get('task_status')
        if state == 'SUCCEEDED':
            results = output.get('results', [])
            result = next((r for r in results if r.get('subtask_status') == 'SUCCEEDED'), None)
            if not result:
                reason = results[0] if results else output
                raise RuntimeError('音频识别失败：' + clean(reason.get('code', '')) + ' ' + clean(reason.get('message', '')))
            url = result['transcription_url']
            if urlparse(url).scheme != 'https' or not (urlparse(url).hostname or '').endswith('.aliyuncs.com'):
                raise RuntimeError('识别结果地址不受信任')
            raw = response_json(requests.get(url, timeout=(20, 90)), '读取识别结果')
            json_write(cache, raw)
            return raw, version, False
        if state in ('FAILED', 'CANCELED', 'UNKNOWN'):
            raise RuntimeError('云转写任务失败：' + clean(output.get('code', state)) + ' ' + clean(output.get('message', '')))
        if progress:
            progress(0.3, '百炼处理中' if state == 'RUNNING' else '百炼排队中')
        if cancel:
            cancel.wait(3)
        else:
            time.sleep(3)
    raise TimeoutError('云端等待超过3小时；任务记录保留，可恢复查询')
