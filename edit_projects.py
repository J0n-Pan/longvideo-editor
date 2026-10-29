"""Shared source assets, independently versioned editing projects and group snapshots."""
import copy
import hashlib
import json
import math
import time
import uuid

from domain import default_output, validate_output
from storage import LOCK, connection, get, save, all_objects


class EditConflict(ValueError):
    pass


def empty_group():
    return {'id': uuid.uuid4().hex[:12], 'name': '切片001', 'items': [], 'output': None}


def create_edit(name):
    name = str(name or '').strip()
    if not name or len(name) > 100:
        raise ValueError('工程名称需为 1–100 个字符')
    return save('edit', {'id': uuid.uuid4().hex[:16], 'name': name, 'asset_ids': [],
                         'groups': [empty_group()], 'revision': 1, 'updated_at': time.time()})


def migrate_legacy():
    # One transaction makes retries idempotent and preserves the pre-migration groups.
    with LOCK, connection() as db:
        if db.execute("SELECT 1 FROM objects WHERE kind='migration' AND id='mixed-v1'").fetchone():
            return
        assets = [json.loads(row[0]) for row in db.execute("SELECT data FROM objects WHERE kind='project'")]
        def put(kind, value):
            db.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?)',
                       (kind, value['id'], json.dumps(value, ensure_ascii=False)))
        put('workspace_backup', {'id': 'before-mixed-v1', 'created_at': time.time(),
                                'projects': [{'id': a['id'], 'groups': a.get('groups', [])} for a in assets]})
        for asset in assets:
            groups = copy.deepcopy(asset.get('groups') or [])
            for group in groups:
                for item in group['items']:
                    item['asset_id'] = asset['id']
                group['output'] = default_output(asset.get('media') or {}) if group['items'] else None
            put('edit', {'id': 'legacy-' + hashlib.sha256(asset['id'].encode()).hexdigest()[:16],
                         'name': asset.get('name') or '旧版工程', 'asset_ids': [asset['id']],
                         'groups': groups or [empty_group()], 'revision': 1, 'updated_at': time.time()})
        if not assets:
            put('edit', {'id': uuid.uuid4().hex[:16], 'name': '我的剪辑工程', 'asset_ids': [],
                         'groups': [empty_group()], 'revision': 1, 'updated_at': time.time()})
        put('migration', {'id': 'mixed-v1', 'completed_at': time.time()})


def check_revision(edit, expected):
    if type(expected) is not int or expected != edit['revision']:
        raise EditConflict('工程已被其他页面修改，请刷新后继续；本次修改尚未覆盖已保存内容。')


def write_edit(edit):
    edit.update(revision=edit['revision'] + 1, updated_at=time.time())
    return save('edit', edit)


def set_assets(eid, asset_ids, expected=None, *, append=False):
    if not isinstance(asset_ids, list) or any(not isinstance(x, str) for x in asset_ids):
        raise ValueError('工程素材清单无效')
    with LOCK:
        edit = get('edit', eid)
        if not append:
            check_revision(edit, expected)
        for aid in asset_ids:
            get('project', aid)
        chosen = list(dict.fromkeys([*(edit['asset_ids'] if append else []), *asset_ids]))
        used = {i['asset_id'] for g in edit['groups'] for i in g['items']}
        if used - set(chosen):
            raise ValueError('素材仍被本工程的分组引用，请先移除相应片段再移出工程。')
        edit['asset_ids'] = chosen
        return write_edit(edit)


def update_edit(eid, data):
    with LOCK:
        edit = get('edit', eid)
        check_revision(edit, data.get('revision'))
        if 'name' in data:
            name = str(data['name']).strip()
            if not name or len(name) > 100:
                raise ValueError('工程名称需为 1–100 个字符')
            edit['name'] = name
        if 'groups' in data:
            groups = copy.deepcopy(data['groups'])
            if not isinstance(groups, list) or len(groups) > 200:
                raise ValueError('分组数据无效')
            sources, group_ids = {}, set()
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get('id'), str) or not group['id'] or group['id'] in group_ids:
                    raise ValueError('分组编号无效或重复')
                group_ids.add(group['id'])
                if not isinstance(group.get('name'), str) or not group['name'].strip() or len(group['name']) > 200:
                    raise ValueError('组名称需为 1–200 个字符')
                items = group.get('items')
                if not isinstance(items, list) or len(items) > 10000:
                    raise ValueError('组内片段数据无效')
                item_ids = set()
                for item in items:
                    if not isinstance(item, dict) or item.get('asset_id') not in edit['asset_ids']:
                        raise ValueError('片段来源不在当前工程素材清单中')
                    if not isinstance(item.get('item_id'), str) or not item['item_id'] or item['item_id'] in item_ids:
                        raise ValueError('片段编号无效或重复')
                    item_ids.add(item['item_id'])
                    aid = item['asset_id']
                    if aid not in sources:
                        sources[aid] = get('project', aid)
                    bounds = [item.get('start_ms'), item.get('end_ms')]
                    if (any(type(x) not in (float, int) or not math.isfinite(x) for x in bounds)
                            or not 0 <= bounds[0] < bounds[1] <= sources[aid]['media']['duration_ms']):
                        raise ValueError(f'片段时间超出素材「{sources[aid]["name"]}」范围')
                    if not isinstance(item.get('id'), str) or not isinstance(item.get('text'), str):
                        raise ValueError('片段句子数据无效')
                    # Word arrays live with shared assets, rather than being duplicated in each edit.
                    item.pop('words', None)
                profile = group.get('output')
                if profile is None and items:
                    profile = default_output(sources[items[0]['asset_id']]['media'])
                group['output'] = validate_output(profile) if profile is not None else None
            edit['groups'] = groups
        return write_edit(edit)


def selected_groups(edit, ids):
    if ids is not None and (not isinstance(ids, list) or any(not isinstance(x, str) for x in ids)):
        raise ValueError('导出分组清单无效')
    available = {g['id'] for g in edit['groups']}
    if ids is not None and set(ids) - available:
        raise ValueError('部分待导出的分组已不存在')
    return [g for g in edit['groups'] if g['items'] and (ids is None or g['id'] in ids)]


def sources_for(edit, groups):
    sources = {}
    for group in groups:
        for item in group['items']:
            aid = item['asset_id']
            if aid not in edit['asset_ids']:
                raise ValueError('片段引用了工程外的素材')
            if aid not in sources:
                asset = get('project', aid)
                sources[aid] = {key: asset[key] for key in
                                ('id', 'name', 'path', 'source_size', 'source_mtime_ns', 'media')}
    return sources
