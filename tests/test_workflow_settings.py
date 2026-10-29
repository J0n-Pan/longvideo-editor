from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as application
import storage

HEADERS = {'X-Longvideo-Client': '1'}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    data, exports = tmp_path / 'data', tmp_path / 'default-exports'
    transcripts = tmp_path / 'default-transcripts'
    data.mkdir()
    exports.mkdir()
    transcripts.mkdir()
    monkeypatch.setattr(storage, 'DATA', data)
    monkeypatch.setattr(storage, 'EXPORTS', exports)
    monkeypatch.setattr(storage, 'TRANSCRIPTS', transcripts)
    monkeypatch.setattr(application, 'get_key', lambda: '')
    monkeypatch.setattr(application, 'key_storage', lambda: '测试内存')
    monkeypatch.setattr(application, 'cancellations', {})
    queued = []
    monkeypatch.setattr(application, 'pool', SimpleNamespace(
        submit=lambda fn, *args: queued.append((fn, args))))
    def unexpected_cloud_call(*args, **kwargs):
        raise AssertionError('本地测试不能调用云服务')
    monkeypatch.setattr(application.asr, 'transcribe', unexpected_cloud_call)
    monkeypatch.setattr(application, 'probe', lambda path: {
        'duration_ms': 6000, 'width': 320, 'height': 180, 'fps': 25,
        'fps_fraction': '25/1', 'audio_codec': 'aac'})
    source = tmp_path / '测试素材.MP4'
    source.write_bytes(b'local-test-source')
    with TestClient(application.app, base_url='http://127.0.0.1') as client:
        yield SimpleNamespace(client=client, source=source, exports=exports, transcripts=transcripts,
                              queued=queued, root=tmp_path)


def import_video(workspace):
    response = workspace.client.post('/api/import', headers=HEADERS,
                                     json={'path': str(workspace.source)})
    assert response.status_code == 200, response.text
    return response.json()[0]


def test_export_settings_persist_and_files_are_scoped_to_current_folder(workspace):
    client = workspace.client
    (workspace.exports / '原目录.mp4').write_bytes(b'old')
    destination = workspace.root / '自定义导出' / '下一层'
    assert client.put('/api/settings', json={'exports_path': str(destination)}).status_code == 403
    response = client.put('/api/settings', headers=HEADERS,
                          json={'exports_path': f'"{destination}"'})
    assert response.status_code == 200
    assert Path(client.get('/api/info').json()['exports_path']) == destination.resolve()
    assert storage.get('settings', 'workspace')['exports_path'] == str(destination.resolve())
    assert (workspace.exports / '原目录.mp4').is_file()
    (destination / '新视频.mp4').write_bytes(b'new')
    (destination / '新视频.srt').write_text('字幕', encoding='utf-8')
    assert [x['file_name'] for x in client.get('/api/exports').json()] == ['新视频.mp4']
    assert client.get('/api/exports/新视频.mp4').content == b'new'
    assert client.get('/api/exports/新视频.srt').status_code == 200
    assert client.get('/api/exports/原目录.mp4').status_code == 404
    assert client.get('/api/exports/..%5Cdefault-exports%5C原目录.mp4').status_code == 404


@pytest.mark.parametrize('value', ['', 'relative-folder', 'bad\npath', None])
def test_invalid_export_path_does_not_replace_saved_setting(workspace, value):
    response = workspace.client.put('/api/settings', headers=HEADERS,
                                    json={'exports_path': value})
    assert response.status_code == 400
    assert storage.export_directory() == workspace.exports.resolve()


def test_existing_file_and_unwritable_folder_are_rejected(workspace, monkeypatch):
    client = workspace.client
    assert client.put('/api/settings', headers=HEADERS,
                      json={'exports_path': str(workspace.source)}).status_code == 400
    def denied(*args, **kwargs):
        raise PermissionError('test: no write permission')
    monkeypatch.setattr(storage.tempfile, 'TemporaryFile', denied)
    assert client.put('/api/settings', headers=HEADERS,
                      json={'exports_path': str(workspace.root / 'denied')}).status_code == 400
    assert storage.export_directory() == workspace.exports.resolve()


def test_missing_export_folder_can_be_repaired_in_settings(workspace):
    path = workspace.root / 'deleted-export-folder'
    storage.set_export_directory(str(path))
    path.rmdir()
    assert workspace.client.get('/api/info').json()['exports_path'] == str(path.resolve())
    assert workspace.client.get('/api/exports').json() == []
    assert workspace.client.put('/api/settings', headers=HEADERS,
                                json={'exports_path': str(path)}).status_code == 200


def test_missing_key_waits_without_cloud_submission(workspace):
    project = import_video(workspace)
    assert project['auto_transcription']['status'] == 'needs_key'
    saved = storage.get('project', project['id'])
    assert saved['asr_status'] == '等待密钥' and saved['auto_transcribe_pending']
    assert workspace.queued == []


def test_file_picker_import_uses_auto_flow_and_cancel_keeps_projects(workspace, monkeypatch):
    monkeypatch.setattr(application, 'get_key', lambda: 'test-placeholder')
    monkeypatch.setattr(application, 'select_video_files', lambda folder: [str(workspace.source)])
    selected = workspace.client.post('/api/import/pick', headers=HEADERS, json={})
    assert selected.status_code == 200
    assert selected.json()[0]['auto_transcription']['status'] == 'queued'
    assert len(workspace.queued) == 1
    monkeypatch.setattr(application, 'select_video_files', lambda folder: [])
    canceled = workspace.client.post('/api/import/pick', headers=HEADERS, json={})
    assert canceled.json() == []
    assert len(storage.all_objects('project')) == 1 and len(workspace.queued) == 1


def test_import_queues_full_video_once_and_reuses_corrected_transcript(workspace, monkeypatch):
    monkeypatch.setattr(application, 'get_key', lambda: 'test-placeholder')
    project = import_video(workspace)
    repeated = import_video(workspace)
    assert repeated['id'] == project['id']
    assert len(workspace.queued) == 1
    job = storage.all_objects('job')[0]
    assert job['payload'] == {'start_ms': 0, 'end_ms': 6000}
    saved = storage.get('project', project['id'])
    saved.update(sentences=[{'id': 'corrected', 'text': '人工修正过的文稿'}],
                 groups=[{'id': 'kept', 'name': '人工命名', 'items': []}])
    storage.save('project', saved)
    repeated = import_video(workspace)
    assert repeated['auto_transcription']['status'] == 'ready'
    assert repeated['sentences'][0]['text'] == '人工修正过的文稿'
    assert repeated['groups'][0]['name'] == '人工命名'
    assert len(workspace.queued) == 1


def test_saving_key_resumes_waiting_imports(workspace, monkeypatch):
    project = import_video(workspace)
    state = {'key': ''}
    monkeypatch.setattr(application, 'get_key', lambda: state['key'])
    def set_key(value):
        state['key'] = value
        return '测试内存'
    monkeypatch.setattr(application, 'set_key', set_key)
    response = workspace.client.post('/api/credentials', headers=HEADERS,
                                     json={'key': 'test-placeholder'})
    assert response.status_code == 200
    assert response.json()['auto_transcriptions'][0]['status'] == 'queued'
    assert len(workspace.queued) == 1
    assert not storage.get('project', project['id'])['auto_transcribe_pending']


@pytest.mark.parametrize('fail', [False, True])
def test_automatic_transcription_finishes_or_reports_failure(workspace, monkeypatch, fail):
    monkeypatch.setattr(application, 'get_key', lambda: 'test-placeholder')
    audio = workspace.root / 'audio.mp3'
    audio.write_bytes(b'local-test-audio')
    monkeypatch.setattr(application, 'extract_audio', lambda *args: (audio, 6))
    def transcribe(audio, progress, cancel, accepted):
        if fail:
            raise RuntimeError('模拟转写失败')
        accepted('test-cloud-task')
        return {'transcripts': [{'sentences': [
            {'text': '测试句子。', 'begin_time': 100, 'end_time': 1200, 'words': []}
        ]}]}, 'test-version', False
    monkeypatch.setattr(application.asr, 'transcribe', transcribe)
    project = import_video(workspace)
    worker, args = workspace.queued[0]
    worker(*args)
    job = storage.all_objects('job')[0]
    saved = storage.get('project', project['id'])
    assert job['status'] == ('failed' if fail else 'succeeded')
    assert saved['asr_status'] == ('转写失败' if fail else '已转写')
    if not fail:
        assert saved['sentences'][0]['text'] == '测试句子。'
        assert job['cloud_task_id'] == 'test-cloud-task'


def test_automatic_transcription_keeps_budget_guard(workspace, monkeypatch):
    monkeypatch.setattr(application, 'get_key', lambda: 'test-placeholder')
    storage.save('job', {'id': 'previous-charge', 'kind': 'asr', 'project_id': 'older',
                        'status': 'succeeded', 'cloud_task_id': 'older-task',
                        'estimated_yuan': application.BUDGET})
    project = import_video(workspace)
    assert project['auto_transcription']['status'] == 'failed'
    assert '预算' in project['auto_transcription']['message']
    assert workspace.queued == []


def test_queued_export_keeps_its_original_destination(workspace, monkeypatch):
    project = import_video(workspace)
    project['groups'] = [{'id': 'group', 'name': '短片', 'items': [{'id': 'sentence'}]}]
    storage.save('project', project)
    response = workspace.client.post(f"/api/projects/{project['id']}/export",
                                     headers=HEADERS, json={})
    assert response.status_code == 200
    storage.set_export_directory(str(workspace.root / 'next-exports'))
    destinations = []
    monkeypatch.setattr(application, 'render',
                        lambda *args, output_directory: destinations.append(output_directory) or [])
    worker, args = workspace.queued[0]
    worker(*args)
    assert destinations == [workspace.exports.resolve()]
    assert storage.get('job', response.json()['id'])['status'] == 'succeeded'


def test_removed_manual_transcription_route_is_unavailable(workspace):
    response = workspace.client.post('/api/projects/example/transcribe', headers=HEADERS, json={})
    assert response.status_code == 404


def test_folder_picker_saves_video_and_transcript_paths_independently(workspace, monkeypatch):
    client = workspace.client
    for kind, key in [('video', 'exports_path'), ('transcript', 'transcripts_path')]:
        destination = workspace.root / f'选择的{kind}目录'
        destination.mkdir()
        monkeypatch.setattr(application, 'select_directory', lambda initial, path=destination: str(path))
        assert client.post('/api/settings/folder/pick', json={'kind': kind}).status_code == 403
        response = client.post('/api/settings/folder/pick', headers=HEADERS, json={'kind': kind})
        assert response.status_code == 200, response.text
        assert not response.json()['canceled']
        assert response.json()[key] == str(destination.resolve())
        assert client.get('/api/info').json()[key] == str(destination.resolve())
    settings = storage.get('settings', 'workspace')
    assert settings['exports_path'] != settings['transcripts_path']
    assert storage.export_directory() == (workspace.root / '选择的video目录').resolve()


@pytest.mark.parametrize('kind', ['video', 'transcript'])
def test_canceling_folder_picker_keeps_both_settings(workspace, monkeypatch, kind):
    before = workspace.client.get('/api/info').json()
    monkeypatch.setattr(application, 'select_directory', lambda initial: None)
    response = workspace.client.post('/api/settings/folder/pick', headers=HEADERS, json={'kind': kind})
    assert response.status_code == 200 and response.json()['canceled']
    after = workspace.client.get('/api/info').json()
    assert (before['exports_path'], before['transcripts_path']) == (after['exports_path'], after['transcripts_path'])


def test_folder_picker_uses_shared_lock_and_recovers_from_errors(workspace, monkeypatch):
    client = workspace.client
    with application.picker_lock:
        assert client.post('/api/settings/folder/pick', headers=HEADERS,
                           json={'kind': 'video'}).status_code == 409
    monkeypatch.setattr(application, 'select_directory', lambda initial: str(workspace.source))
    assert client.post('/api/settings/folder/pick', headers=HEADERS,
                       json={'kind': 'transcript'}).status_code == 400
    monkeypatch.setattr(application, 'select_directory', lambda initial: None)
    assert client.post('/api/settings/folder/pick', headers=HEADERS,
                       json={'kind': 'transcript'}).json()['canceled']
    assert client.post('/api/settings/folder/pick', headers=HEADERS,
                       json={'kind': 'unknown'}).status_code == 400
    assert storage.export_directory() == workspace.exports.resolve()
    assert storage.transcript_directory() == workspace.transcripts.resolve()


def test_transcript_export_contains_all_corrected_sentences_in_its_own_folder(workspace, monkeypatch):
    project = import_video(workspace)
    project.update(sentences=[
        {'id': 's1', 'source_index': 0, 'text': '原始文字', 'start_ms': 100, 'end_ms': 1500},
        {'id': 's2', 'source_index': 1, 'text': '第二句。', 'start_ms': 3000, 'end_ms': 5000}
    ], groups=[{'id': 'group', 'name': '只选第二句的组', 'items': []}])
    storage.save('project', project)
    client = workspace.client
    assert client.put(f"/api/projects/{project['id']}/sentence/s1", headers=HEADERS,
                      json={'text': '人工修正的中文。'}).status_code == 200
    destination = workspace.root / '文稿单独保存'
    monkeypatch.setattr(application, 'select_directory', lambda initial: str(destination))
    assert client.post('/api/settings/folder/pick', headers=HEADERS,
                       json={'kind': 'transcript'}).status_code == 200
    endpoint = f"/api/projects/{project['id']}/transcript/export"
    assert client.post(endpoint, json={}).status_code == 403
    first = client.post(endpoint, headers=HEADERS, json={})
    assert first.status_code == 200, first.text
    result = first.json()
    file = Path(result['path'])
    assert file.parent == destination.resolve() and result['sentence_count'] == 2
    content = file.read_text(encoding='utf-8-sig')
    assert '1. [00:00:00.100 --> 00:00:01.500] 人工修正的中文。' in content
    assert content.index('人工修正的中文。') < content.index('第二句。')
    original = file.read_bytes()
    assert original.startswith(b'\xef\xbb\xbf')
    assert b'\n' not in original.replace(b'\r\n', b'')
    repeated = client.post(endpoint, headers=HEADERS, json={}).json()
    assert repeated['file_name'] != result['file_name'] and file.read_bytes() == original
    assert not list(workspace.exports.glob('*.txt')) and workspace.queued == []


def test_empty_transcript_cannot_create_export_file(workspace):
    project = import_video(workspace)
    response = workspace.client.post(f"/api/projects/{project['id']}/transcript/export",
                                     headers=HEADERS, json={})
    assert response.status_code == 400 and '尚无转写文稿' in response.json()['detail']
    assert list(workspace.transcripts.iterdir()) == []


def test_startup_refines_existing_captions_and_keeps_one_local_backup(workspace):
    from test_segmentation import sample
    project = sample()
    storage.save('project', project)
    application.recover()
    refined = storage.get('project', project['id'])
    assert refined['segmentation_revision'] == 2
    assert len(refined['sentences']) > len(project['sentences'])
    backup = storage.get('transcript_backup', project['id'] + ':before-segmentation-v2')
    assert backup['project'] == project
    application.recover()
    assert storage.get('project', project['id']) == refined
    assert storage.get('transcript_backup', backup['id']) == backup


def test_background_exit_is_guarded_and_stops_accepting_new_writes(workspace, monkeypatch):
    scheduled = []
    monkeypatch.setattr(application.app.state, 'request_shutdown', lambda: None, raising=False)
    def timer(delay, callback):
        return SimpleNamespace(start=lambda: scheduled.append(callback), daemon=False)
    monkeypatch.setattr(application.threading, 'Timer', timer)
    storage.save('job', {'id': 'active', 'kind': 'export', 'status': 'running'})
    assert workspace.client.post('/api/shutdown', json={}).status_code == 403
    assert workspace.client.post('/api/shutdown', headers=HEADERS, json={}).status_code == 409
    assert scheduled == []
    storage.update_job('active', status='succeeded')
    with application.picker_lock:
        assert workspace.client.post('/api/shutdown', headers=HEADERS, json={}).status_code == 409
    assert workspace.client.get('/api/info').json()['background_running']
    assert workspace.client.post('/api/shutdown', headers=HEADERS, json={}).json()['stopping']
    assert len(scheduled) == 1
    assert workspace.client.post('/api/import', headers=HEADERS, json={'path':str(workspace.source)}).status_code == 409
    assert workspace.client.post('/api/shutdown', headers=HEADERS, json={}).status_code == 200
    assert len(scheduled) == 1
