"""Source-time selection, ordered compositions and safe output names."""
import copy
import math
import re
import uuid
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
    spans = []
    for item in group.get('items', []):
        start = max(0, int(item['start_ms']) - 80)
        end = min(duration_ms, int(item['end_ms']) + 120)
        if end <= start:
            raise ValueError('片段结束时间必须晚于开始时间')
        if spans:
            prev = spans[-1]
            last = prev['items'][-1]
            consecutive = (item.get('version') == last.get('version') and
                           item.get('source_index') == last.get('source_index', -2) + 1 and
                           item['start_ms'] >= last['start_ms'] and
                           not item.get('edited') and not last.get('edited'))
            if consecutive:
                prev['end_ms'] = max(prev['end_ms'], end)
                prev['items'].append(copy.deepcopy(item))
                continue
        spans.append({'start_ms': start, 'end_ms': end, 'items': [copy.deepcopy(item)]})
    cursor = 0
    for span in spans:
        # Source frame boundaries are recorded explicitly; all UI data stays in source milliseconds.
        span['start_ms'] = math.floor(span['start_ms'] / 1000 * fps) / fps * 1000
        span['end_ms'] = min(duration_ms, math.ceil(span['end_ms'] / 1000 * fps) / fps * 1000)
        span['output_start_ms'] = cursor
        span['duration_ms'] = span['end_ms'] - span['start_ms']
        cursor += span['duration_ms']
    return spans
