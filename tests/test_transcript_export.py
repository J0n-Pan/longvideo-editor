from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transcript_export import export_transcript, transcript_text


def sample():
    return {'name': '中文/访谈:文稿', 'path': '素材.mp4', 'sentences': [
        {'source_index': 4, 'text': '修正后的文字。', 'start_ms': 3723123, 'end_ms': 3725123},
        {'source_index': 5, 'text': '完整的第二句话。', 'start_ms': 3726000, 'end_ms': 3729000}
    ]}


def test_source_numbers_hour_timestamps_and_corrected_text_are_preserved():
    text = transcript_text(sample())
    assert '5. [01:02:03.123 --> 01:02:05.123] 修正后的文字。' in text
    assert '6. [01:02:06.000' in text


def test_concurrent_exports_never_overwrite_existing_documents(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: export_transcript(sample(), tmp_path), range(8)))
    assert len({x['file_name'] for x in results}) == 8
    assert all('/' not in x['file_name'] and ':' not in x['file_name'] for x in results)
    assert all(Path(x['path']).read_text(encoding='utf-8-sig') == transcript_text(sample()) for x in results)


def test_missing_destination_reports_a_useful_error(tmp_path):
    with pytest.raises(ValueError, match='文件夹'):
        export_transcript(sample(), tmp_path / 'missing')
