import copy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as application
from domain import mixed_composition
import edit_projects as edits
import storage

H = {'X-Longvideo-Client': '1'}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for name in ('data', 'exports', 'texts'):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(storage, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(storage, 'EXPORTS', tmp_path / 'exports')
    monkeypatch.setattr(storage, 'TRANSCRIPTS', tmp_path / 'texts')
    monkeypatch.setattr(application, 'get_key', lambda: '')
    queued = []
    monkeypatch.setattr(application, 'pool', SimpleNamespace(submit=lambda fn, *args: queued.append((fn, args))))
    sources = {}
    for aid, duration in (('A', 12000), ('B', 5000)):
        source = tmp_path / (aid + '.mp4')
        source.write_bytes(b'local synthetic source ' + aid.encode())
        stat = source.stat()
        sentences = [dict(id=aid + str(i), source_index=i, version='same-version', text='已修正句子' + str(i),
                          raw_text='原文', start_ms=500 + i * 1000, end_ms=1100 + i * 1000, words=[])
                     for i in range(2)]
        project = {'id': aid, 'name': '素材' + aid, 'path': str(source), 'source_size': stat.st_size,
                   'source_mtime_ns': stat.st_mtime_ns, 'media': {'duration_ms': duration, 'fps': 25,
                   'fps_fraction': '25/1', 'width': 320, 'height': 180, 'audio_codec': 'aac'},
                   'sentences': sentences, 'segmentation_revision': 2, 'asr_status': '已转写',
                   'groups': [{'id': 'old-' + aid, 'name': '旧组' + aid, 'items': [
                       {**sentences[0], 'item_id': 'old-item-' + aid, 'start_ms': 550, 'edited': True}]}]}
        storage.save('project', project)
        sources[aid] = project
    with TestClient(application.app, base_url='http://127.0.0.1') as client:
        yield SimpleNamespace(client=client, sources=sources, queued=queued, root=tmp_path)


def request(w, method, url, body=None, status=200):
    response = w.client.request(method, url, json=body, headers=H)
    assert response.status_code == status, response.text
    return response.json()


def new_mixed(w):
    edit = request(w, 'POST', '/api/edits', {'name': '跨素材工程'})
    return request(w, 'PUT', '/api/edits/' + edit['id'] + '/assets',
                   {'revision': edit['revision'], 'asset_ids': ['A', 'B']})


def item(w, aid, number):
    return {**copy.deepcopy(w.sources[aid]['sentences'][number]), 'asset_id': aid,
            'item_id': aid + '-' + str(number), 'edited': False}


def save_groups(w, edit, groups):
    return request(w, 'PUT', '/api/edits/' + edit['id'], {'revision': edit['revision'], 'groups': groups})


def test_migration_is_idempotent_and_preserves_old_corrections_and_cuts(workspace):
    w = workspace
    before = storage.all_objects('edit')
    assert len(before) == 2
    edits.migrate_legacy()
    assert storage.all_objects('edit') == before
    for edit in before:
        aid = edit['asset_ids'][0]
        migrated = edit['groups'][0]['items'][0]
        assert migrated == {**w.sources[aid]['groups'][0]['items'][0], 'asset_id': aid}
        assert edit['groups'][0]['output']['width'] == 320
        assert storage.get('project', aid) == w.sources[aid]
    backup = storage.get('workspace_backup', 'before-mixed-v1')
    assert backup['projects'][0]['groups'] == w.sources['A']['groups']


def test_shared_asset_reference_does_not_retranscribe_or_share_groups(workspace):
    w = workspace
    first, second = new_mixed(w), new_mixed(w)
    first['groups'][0]['items'] = [item(w, 'A', 0), item(w, 'B', 1)]
    first = save_groups(w, first, first['groups'])
    assert not storage.get('edit', second['id'])['groups'][0]['items']
    assert not w.queued and not storage.all_objects('job')
    assert len(storage.all_objects('project')) == 2
    assert 'words' not in first['groups'][0]['items'][0]


def test_sources_with_same_version_and_consecutive_numbers_never_merge(workspace):
    w = workspace
    group = {'items': [item(w, 'A', 0), item(w, 'B', 1), item(w, 'A', 1)]}
    spans = mixed_composition(group, w.sources)
    assert [s['asset_id'] for s in spans] == ['A', 'B', 'A']
    assert spans[2]['output_start_ms'] == spans[0]['duration_ms'] + spans[1]['duration_ms']


def test_used_asset_cannot_be_detached_but_unused_reference_can(workspace):
    w = workspace
    edit = new_mixed(w)
    edit['groups'][0]['items'] = [item(w, 'A', 0)]
    edit = save_groups(w, edit, edit['groups'])
    request(w, 'PUT', '/api/edits/' + edit['id'] + '/assets',
            {'revision': edit['revision'], 'asset_ids': ['B']}, status=400)
    updated = request(w, 'PUT', '/api/edits/' + edit['id'] + '/assets',
                      {'revision': edit['revision'], 'asset_ids': ['A']})
    assert updated['asset_ids'] == ['A'] and storage.get('project', 'B') == w.sources['B']


def test_stale_save_cannot_overwrite_another_page(workspace):
    w = workspace
    edit = new_mixed(w)
    saved = request(w, 'PUT', '/api/edits/' + edit['id'], {'revision': edit['revision'], 'name': '最新命名'})
    request(w, 'PUT', '/api/edits/' + edit['id'],
            {'revision': edit['revision'], 'name': '过时命名'}, status=409)
    assert storage.get('edit', edit['id']) == saved


@pytest.mark.parametrize('problem', ['outside-asset', 'source-duration', 'duplicate-item', 'invalid-output'])
def test_invalid_mixed_group_save_is_atomic(workspace, problem):
    w = workspace
    edit = new_mixed(w)
    before = copy.deepcopy(edit)
    g = edit['groups'][0]
    g['items'] = [item(w, 'B', 0)]
    if problem == 'outside-asset': g['items'][0]['asset_id'] = 'not-attached'
    if problem == 'source-duration': g['items'][0]['end_ms'] = 6000
    if problem == 'duplicate-item': g['items'].append(copy.deepcopy(g['items'][0]))
    if problem == 'invalid-output': g['output'] = {'width': 101, 'height': 200, 'fps_fraction': '0'}
    request(w, 'PUT', '/api/edits/' + edit['id'],
            {'revision': edit['revision'], 'groups': edit['groups']}, status=400)
    assert storage.get('edit', edit['id']) == before


def test_output_profile_is_fixed_after_reordering_and_preview_matches_plan(workspace):
    w = workspace
    edit = new_mixed(w)
    g = edit['groups'][0]
    g['items'] = [item(w, 'A', 0), item(w, 'B', 1)]
    edit = save_groups(w, edit, edit['groups'])
    profile = edit['groups'][0]['output']
    edit['groups'][0]['items'].reverse()
    edit = save_groups(w, edit, edit['groups'])
    assert edit['groups'][0]['output'] == profile
    base = '/api/edits/' + edit['id']
    plan = request(w, 'POST', base + '/export-plan', {})[0]
    preview = request(w, 'POST', base + '/preview-plan', {})[0]
    assert [s['asset_id'] for s in preview['spans']] == ['B', 'A']
    assert plan['duration_ms'] == sum(s['duration_ms'] for s in preview['spans'])
    assert plan['source_count'] == 2


def test_export_uses_submission_snapshot_even_after_further_edits(workspace, monkeypatch):
    w = workspace
    edit = new_mixed(w)
    edit['groups'][0]['items'] = [item(w, 'A', 0), item(w, 'B', 1)]
    edit = save_groups(w, edit, edit['groups'])
    expected = copy.deepcopy(edit['groups'])
    job = request(w, 'POST', '/api/edits/' + edit['id'] + '/export', {})
    edit['groups'][0]['items'].reverse()
    save_groups(w, edit, edit['groups'])
    captured = []
    monkeypatch.setattr(application, 'render_mixed', lambda sources, groups, *a, **kw: captured.append((sources, groups)) or [])
    worker, args = w.queued[-1]
    worker(*args)
    assert captured[0][1] == expected and set(captured[0][0]) == {'A', 'B'}
    assert storage.get('job', job['id'])['status'] == 'succeeded'


@pytest.mark.parametrize('failure', ['moved', 'changed', 'hdr'])
def test_all_sources_are_checked_before_export_is_queued(workspace, failure):
    w = workspace
    edit = new_mixed(w)
    edit['groups'][0]['items'] = [item(w, 'A', 0), item(w, 'B', 1)]
    edit = save_groups(w, edit, edit['groups'])
    if failure == 'moved': Path(w.sources['B']['path']).unlink()
    if failure == 'changed': Path(w.sources['B']['path']).write_bytes(b'changed')
    if failure == 'hdr':
        b = storage.get('project', 'B');b['media']['color_transfer'] = 'smpte2084';storage.save('project', b)
    response = request(w, 'POST', '/api/edits/' + edit['id'] + '/export', {}, status=400)
    assert '素材B' in response['detail'] and not w.queued


def test_shared_transcript_correction_preserves_each_edit_cut_and_changes_revision(workspace):
    w = workspace
    edit = new_mixed(w)
    edit['groups'][0]['items'] = [item(w, 'A', 0)]
    edit['groups'][0]['items'][0].update(start_ms=600, edited=True)
    edit = save_groups(w, edit, edit['groups'])
    request(w, 'PUT', '/api/projects/A/sentence/A0', {'text': '共享文稿修正'})
    updated = storage.get('edit', edit['id'])
    assert updated['groups'][0]['items'][0]['text'] == '共享文稿修正'
    assert updated['groups'][0]['items'][0]['start_ms'] == 600
    assert updated['revision'] == edit['revision'] + 1
    assert storage.get('project', 'A')['sentences'][0]['text'] == '共享文稿修正'
