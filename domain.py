"""Source-time selection, ordered compositions and safe output names."""
import copy
import math
import re
import uuid
from fractions import Fraction
from segmentation import split_tokens, token_text


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip().rstrip('. ')
    value = value[:90].rstrip('. ') or '未命名短片'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)', value, re.I):
        value = '_' + value
    return value


def choose_names(groups, existing):
    used = {str(x).casefold() for x in existing}
    result = []
    for group in groups:
        base = safe_name(group.get('name', ''))
        name, n = base, 2
        while (name + '.mp4').casefold() in used:
            name, n = f'{base}_{n:02d}', n + 1
        used.add((name + '.mp4').casefold())
        result.append({'group_id': group['id'], 'name': group['name'], 'file_name': name + '.mp4'})
    return result


def normalize_transcript(raw, asset_id, offset_ms=0, version=''):
    output = []
    transcripts = raw.get('transcripts', [])
    if not transcripts:
        raise ValueError('转写结果没有transcripts')
    for sentence in transcripts[0].get('sentences', []):
        tokens = []
        for word in sentence.get('words', []):
            text = str(word.get('text', ''))
            punctuation = str(word.get('punctuation', '') or '')
            if punctuation and not text.endswith(punctuation):
                text += punctuation
            if not text:
                continue
            start = word.get('begin_time')
            end = word.get('end_time')
            if start is None or end is None:
                continue
            tokens.append({'text': text, 'start_ms': round(start + offset_ms),
                           'end_ms': round(end + offset_ms)})
        blocks = split_tokens(tokens)
        if not blocks:
            start, end = sentence.get('begin_time'), sentence.get('end_time')
            if start is None or end is None:
                continue
            blocks = [[{'text': sentence.get('text', ''), 'start_ms': round(start + offset_ms),
                        'end_ms': round(end + offset_ms)}]]
        for block in blocks:
            i = len(output)
            text = token_text(block)
            if len(blocks) == 1:
                text = sentence.get('text', text).strip()
            if not text or block[-1]['end_ms'] <= block[0]['start_ms']:
                continue
            sid = f'{asset_id}:{version[:8]}:f2:{i}'
            output.append({'id': sid, 'source_index': i, 'version': version + ':segments-v2',
                           'start_ms': block[0]['start_ms'], 'end_ms': block[-1]['end_ms'],
                           'text': text, 'raw_text': text,
                           'words': [{**t, 'id': f'{sid}:w{k}'} for k, t in enumerate(block)]})
    if not output:
        raise ValueError('没有可用句子或时间戳，请检查音轨与识别结果')
    return output


def make_item(sentence):
    return {**copy.deepcopy(sentence), 'item_id': uuid.uuid4().hex[:12], 'edited': False}


def composition(group, duration_ms, fps=25.0):
    return _composition(group, lambda item: (duration_ms, fps))


def mixed_composition(group, sources):
    def timing(item):
        media = sources[item['asset_id']]['media']
        return media['duration_ms'], media['fps']
    return _composition(group, timing)


def _composition(group, timing):
    spans = []
    for item in group.get('items', []):
        duration_ms, fps = timing(item)
        start = max(0, int(item['start_ms']) - 80)
        end = min(duration_ms, int(item['end_ms']) + 120)
        if end <= start:
            raise ValueError('片段结束时间必须晚于开始时间')
        if spans:
            prev = spans[-1]
            last = prev['items'][-1]
            consecutive = (item.get('asset_id') == last.get('asset_id') and
                           item.get('version') == last.get('version') and
                           item.get('source_index') == last.get('source_index', -2) + 1 and
                           item['start_ms'] >= last['start_ms'] and
                           not item.get('edited') and not last.get('edited'))
            if consecutive:
                prev['end_ms'] = max(prev['end_ms'], end)
                prev['items'].append(copy.deepcopy(item))
                continue
        spans.append({'start_ms': start, 'end_ms': end, 'items': [copy.deepcopy(item)],
                      **({'asset_id': item['asset_id']} if 'asset_id' in item else {})})
    cursor = 0
    for span in spans:
        duration_ms, fps = timing(span['items'][0])
        # Source frame boundaries are recorded explicitly; all UI data stays in source milliseconds.
        span['start_ms'] = math.floor(span['start_ms'] / 1000 * fps) / fps * 1000
        span['end_ms'] = min(duration_ms, math.ceil(span['end_ms'] / 1000 * fps) / fps * 1000)
        span['output_start_ms'] = cursor
        span['duration_ms'] = span['end_ms'] - span['start_ms']
        cursor += span['duration_ms']
    return spans


def default_output(media):
    width = int(media.get('display_width') or media.get('width') or 1920)
    height = int(media.get('display_height') or media.get('height') or 1080)
    return validate_output({'width': max(2, width // 2 * 2), 'height': max(2, height // 2 * 2),
                            'fps_fraction': media.get('fps_fraction') or str(media.get('fps') or 25)})


def validate_output(value):
    if not isinstance(value, dict):
        raise ValueError('输出规格无效')
    width, height = value.get('width'), value.get('height')
    if any(type(x) is not int or x < 2 or x > 7680 or x % 2 for x in (width, height)):
        raise ValueError('输出宽高需为 2–7680 之间的偶数')
    try:
        fps = Fraction(str(value.get('fps_fraction', '')))
    except (ValueError, ZeroDivisionError):
        raise ValueError('输出帧率无效') from None
    if not 1 <= fps <= 120:
        raise ValueError('输出帧率需在 1–120 fps 之间')
    return {'width': width, 'height': height, 'fps_fraction': str(fps), 'fit': 'pad'}
