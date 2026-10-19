import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from domain import safe_name, choose_names, normalize_transcript, make_item, composition, subtitles


def sentence(i, start, text='测试。'):
    return {'id': str(i), 'version': 'v1', 'source_index': i, 'start_ms': start,
            'end_ms': start+1000, 'text': text, 'words': []}


def test_reorder_is_not_sorted_back_to_source_time():
    a, b = make_item(sentence(0, 1000)), make_item(sentence(5, 20000))
    spans = composition({'items': [b, a]}, 100000, 25)
    assert spans[0]['start_ms'] > spans[1]['start_ms']
    srt = subtitles(spans)
    assert '00:00:00,080' in srt
    assert '00:00:20,' not in srt


def test_contiguous_sentence_pause_preserved_without_merging_reversed():
    a, b = make_item(sentence(0, 1000)), make_item(sentence(1, 5000))
    spans = composition({'items': [a, b]}, 100000, 25)
    assert len(spans) == 1
    assert spans[0]['duration_ms'] >= 5000
    assert len(composition({'items': [b, a]}, 100000, 25)) == 2


def test_names_unicode_reserved_and_collisions():
    groups = [{'id':'1','name':'CON'}, {'id':'2','name':'好处/介绍'}, {'id':'3','name':'好处/介绍'}]
    plans = choose_names(groups, ['好处_介绍.mp4'])
    assert plans[0]['file_name'] == '_CON.mp4'
    assert plans[1]['file_name'] == '好处_介绍_02.mp4'
    assert plans[2]['file_name'] == '好处_介绍_03.mp4'
    assert safe_name('... ') == '未命名短片'


def test_repeated_text_retains_distinct_source_identity_and_offset():
    raw = {'transcripts':[{'sentences':[
        {'text':'你好。','begin_time':10,'end_time':500,'words':[{'text':'你好','punctuation':'。','begin_time':10,'end_time':500}]},
        {'text':'你好。','begin_time':3000,'end_time':3500,'words':[{'text':'你好','punctuation':'。','begin_time':3000,'end_time':3500}]}
    ]}]}
    result = normalize_transcript(raw,'asset',60000,'version')
    assert result[0]['id'] != result[1]['id']
    assert result[1]['start_ms'] == 63000
    assert composition({'items':[make_item(result[1])]},100000)[0]['start_ms'] > 62000


def test_subtitles_use_actual_piece_duration_after_reordering():
    a, b = make_item(sentence(0, 1000,'第一句。')), make_item(sentence(5,20000,'第二句。'))
    spans = composition({'items':[b,a]},100000,25)
    spans[0]['actual_duration_ms'] = 1500
    srt = subtitles(spans)
    assert srt.index('第二句。') < srt.index('第一句。')
    assert '00:00:01,580' in srt
