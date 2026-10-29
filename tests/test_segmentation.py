import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from domain import composition, make_item, normalize_transcript
from segmentation import refine_project, split_tokens, token_text


def words(text, start=1000, step=180):
    return [{'text': c, 'start_ms': start + i * step, 'end_ms': start + (i + 1) * step}
            for i, c in enumerate(text)]


def legacy_sentence(index, text, start):
    tokens = words(text, start)
    return {'id': f'old:{index}', 'source_index': index, 'version': 'asr',
            'text': text, 'raw_text': text, 'words': tokens,
            'start_ms': tokens[0]['start_ms'], 'end_ms': tokens[-1]['end_ms']}


def sample():
    sentences = [legacy_sentence(0, '这是一段完整的说明，接下来介绍具体方法。', 1000),
                 legacy_sentence(1, '这里开始第二个例子，然后继续解释其中原因。', 10000),
                 legacy_sentence(2, '这句话已经经过人工修正，所以不应当丢失修改。', 20000)]
    sentences[2]['text'] = '人工修正后的专有名词和说明。'
    return {'id': 'synthetic', 'sentences': sentences, 'groups': [
        {'id': 'g', 'name': '测试', 'items': [make_item(sentences[1]), make_item(sentences[0])]},
        {'id': 'trim', 'name': '边界', 'items': [
            {**make_item(sentences[0]), 'start_ms': 1200, 'end_ms': 3900, 'edited': True}]},
        {'id': 'corrected', 'name': '修正', 'items': [make_item(sentences[2])]}]}


def test_clauses_keep_all_tokens_and_exact_word_boundaries():
    tokens = words('首先介绍今天学习的方法，然后我们再来演示操作。')
    blocks = split_tokens(tokens)
    assert len(blocks) == 2
    assert [t for block in blocks for t in block] == tokens
    assert token_text(blocks[0]).endswith('，')
    assert blocks[1][0]['start_ms'] == blocks[0][-1]['end_ms']


def test_long_continuous_speech_is_not_cut_by_character_count_or_duration():
    tokens = words('我们开始逐步讲解一个比较长的方法并且继续说明每个步骤为什么这样安排接下来可以开始实际练习')
    blocks = split_tokens(tokens)
    assert len(blocks) == 1
    assert len(token_text(blocks[0])) > 24
    assert blocks[0][-1]['end_ms'] - blocks[0][0]['start_ms'] > 6000
    assert blocks[0] == tokens


def test_long_pauses_still_split_without_punctuation():
    paused = words('先完成这一部分')
    paused += words('再进入下一个步骤', paused[-1]['end_ms'] + 600)
    assert len(split_tokens(paused)) == 2


def test_decimal_thousands_and_english_words_are_not_broken():
    tokens = [{'text': value, 'start_ms': i * 300, 'end_ms': (i + 1) * 300}
              for i, value in enumerate(['价格', '1,', '000', '元。', '数值', '3.', '14', '。'])]
    blocks = split_tokens(tokens)
    assert any('1,000' in token_text(block) for block in blocks)
    assert any('3.14' in token_text(block) for block in blocks)
    assert token_text([{'text': 'learn'}, {'text': 'English'}, {'text': 'today.'}]) == 'learn English today.'


def test_timestamp_free_sentence_is_kept_whole():
    raw = {'transcripts': [{'sentences': [{'text': '一句没有逐词时间戳的文稿，保持完整。',
                                        'begin_time': 1000, 'end_time': 9000}]}]}
    result = normalize_transcript(raw, 'asset', version='test')
    assert len(result) == 1 and result[0]['start_ms'] == 1000 and result[0]['end_ms'] == 9000


def test_saved_group_order_and_composed_source_ranges_survive_upgrade():
    original = sample()
    snapshot = copy.deepcopy(original)
    refined = refine_project(original)
    assert original == snapshot
    assert len(refined['sentences']) > len(original['sentences'])
    assert [s['source_index'] for s in refined['sentences']] == list(range(len(refined['sentences'])))
    for before, after in zip(original['groups'], refined['groups']):
        ranges = lambda group: [(s['start_ms'], s['end_ms']) for s in composition(group, 30000, 25)]
        assert ranges(before) == ranges(after)
    items = refined['groups'][0]['items']
    assert items[0]['start_ms'] > items[-1]['start_ms']
    assert len({item['item_id'] for item in items}) == len(items)
    assert {item['id'] for item in items} <= {s['id'] for s in refined['sentences']}
    assert refined['groups'][1]['items'][0]['start_ms'] == 1200
    assert refined['groups'][1]['items'][0]['source_number_label'] == '1–2'
    assert refined['groups'][2]['items'][0]['text'] == original['sentences'][2]['text']
    assert refine_project(refined) is None
