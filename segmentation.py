"""Local word-timestamp segmentation and migration of saved selections."""
import copy
import re

SEGMENTATION_REVISION = 2
TAIL_MERGE_LIMIT_MS = 7000


def token_text(tokens):
    text = ''
    for token in tokens:
        value = token['text']
        if (text and re.search(r'[A-Za-z0-9]$', text) and re.match(r'[A-Za-z0-9]', value)
                and not (text[-1].isdigit() and value[0].isdigit())):
            text += ' '
        text += value
    return text.strip()


def split_tokens(tokens):
    """Cut at real word boundaries; never invent timestamps inside an ASR token."""
    blocks, block = [], []
    for index, token in enumerate(tokens):
        if block:
            length = sum(c.isalnum() for c in token_text(block))
            duration = block[-1]['end_ms'] - block[0]['start_ms']
            gap = token['start_ms'] - block[-1]['end_ms']
            if length >= 6 and gap >= 400 and duration >= 1000:
                blocks.append(block)
                block = []
        block.append(token)
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        numeric_separator = (following and re.search(r'\d[,.]$', token['text']) and
                             re.match(r'\d', following['text']) and
                             following['start_ms'] - token['end_ms'] < 250)
        strong = re.search(r'[。！？!?；;.][”’"\s]*$', token['text'])
        clause = re.search(r'[，,：:][”’"\s]*$', token['text'])
        length = sum(c.isalnum() for c in token_text(block))
        duration = token['end_ms'] - block[0]['start_ms']
        if not numeric_separator and (strong or (clause and length >= 6 and duration >= 900)):
            blocks.append(block)
            block = []
    if block:
        if (blocks and sum(c.isalnum() for c in token_text(block)) < 4 and
                block[-1]['end_ms'] - block[0]['start_ms'] < 900 and
                not re.search(r'[。！？!?；;.][”’"\s]*$', blocks[-1][-1]['text']) and
                block[-1]['end_ms'] - blocks[-1][0]['start_ms'] <= TAIL_MERGE_LIMIT_MS):
            blocks[-1].extend(block)
        else:
            blocks.append(block)
    return blocks


def refine_project(project):
    """Upgrade local captions and map untrimmed selections without cloud requests."""
    if project.get('segmentation_revision', 1) >= SEGMENTATION_REVISION:
        return None
    original = project.get('sentences', [])
    if not original:
        return None
    updated = copy.deepcopy(project)
    sentences, replacements = [], {}
    split_count = 0
    for sentence in original:
        words = sentence.get('words', [])
        usable = bool(words) and all(
            isinstance(w.get('start_ms'), (int, float)) and
            isinstance(w.get('end_ms'), (int, float)) and
            w['end_ms'] > w['start_ms'] and w.get('text') for w in words)
        corrected = sentence.get('text') != sentence.get('raw_text', sentence.get('text'))
        blocks = split_tokens(words) if usable and not corrected else []
        if len(blocks) <= 1:
            blocks = [None]
        else:
            split_count += 1
        children = []
        for n, block in enumerate(blocks):
            child = copy.deepcopy(sentence)
            child['source_index'] = len(sentences)
            child['version'] = sentence.get('version', '') + ':segments-v2'
            if block is not None:
                sid = f"{sentence['id']}:f2:{n}"
                text = token_text(block)
                child.update(id=sid, text=text, raw_text=text,
                             start_ms=block[0]['start_ms'], end_ms=block[-1]['end_ms'],
                             words=[{**w, 'id': f'{sid}:w{k}'} for k, w in enumerate(block)])
            sentences.append(child)
            children.append(child)
        replacements[sentence['id']] = (sentence, children)
    if not split_count:
        return None
    for group in updated.get('groups', []):
        items = []
        for item in group.get('items', []):
            replacement = replacements.get(item.get('id'))
            if not replacement:
                items.append(item)
                continue
            old, children = replacement
            if (not item.get('edited') and item.get('start_ms') == old['start_ms'] and
                    item.get('end_ms') == old['end_ms'] and item.get('text') == old['text']):
                for n, child in enumerate(children):
                    identifier = item['item_id'] if len(children) == 1 else f"{item['item_id']}:f2:{n}"
                    items.append({**item, **copy.deepcopy(child), 'item_id': identifier})
            else:
                first, last = children[0]['source_index'] + 1, children[-1]['source_index'] + 1
                item['source_number_label'] = str(first) if first == last else f'{first}–{last}'
                items.append(item)
        group['items'] = items
    updated.update(sentences=sentences, segmentation_revision=SEGMENTATION_REVISION,
                   segmentation_summary={'before': len(original), 'after': len(sentences)})
    return updated
