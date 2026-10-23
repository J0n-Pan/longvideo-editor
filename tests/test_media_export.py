import errno
from pathlib import Path
import sys
from threading import Event

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media


@pytest.fixture
def export_case(tmp_path, monkeypatch):
    cache = tmp_path / 'cache-drive' / 'jobs'
    cache.mkdir(parents=True)
    destination = tmp_path / 'output-drive' / '中文 视频'
    destination.mkdir(parents=True)
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'synthetic source')
    stat = source.stat()
    project = {'id': 'synthetic', 'path': str(source),
               'source_size': stat.st_size, 'source_mtime_ns': stat.st_mtime_ns,
               'media': {'duration_ms': 4000, 'fps': 25, 'fps_fraction': '25/1'}}
    group = {'id': 'group', 'name': '中文导出测试', 'items': [
        {'source_index': 0, 'version': 'test', 'text': '测试句子。',
         'start_ms': 1000, 'end_ms': 2000}]}
    outputs = []

    def encode(args, duration_s, progress=None, cancel=None):
        target = Path(args[-1])
        target.write_bytes(b'encoded video')
        outputs.append(target)
        if cancel and cancel.is_set():
            raise InterruptedError('任务已取消')

    monkeypatch.setattr(media, 'DATA', cache.parent)
    monkeypatch.setattr(media, 'ffmpeg', encode)
    monkeypatch.setattr(media, 'probe', lambda _: {'duration_ms': 1200})
    return project, group, cache, destination, outputs


@pytest.mark.parametrize('cross_volume', [False, True])
def test_export_publishes_only_complete_mp4_on_destination_volume(
        export_case, monkeypatch, cross_volume):
    project, group, cache, destination, outputs = export_case
    if not cross_volume:
        destination = cache.parent / 'same-drive-output'
        destination.mkdir()
    original_rename = media.os.rename
    published = []

    def volume(path):
        return 'D' if cross_volume and Path(path).is_relative_to(destination) else 'C'

    def rename(source, target):
        # Model Windows refusing a C: -> D: rename, including WinError 17.
        if volume(source) != volume(target):
            error = OSError(errno.EXDEV, 'Cannot move a file to a different disk drive')
            error.winerror = 17
            raise error
        assert not Path(target).exists(), 'Publishing must not overwrite an existing video'
        assert Path(source).read_bytes() == b'encoded video'
        published.append((Path(source), Path(target)))
        original_rename(source, target)

    monkeypatch.setattr(media.os, 'rename', rename)
    result = media.render(project, [group], 'test', output_directory=destination)[0]
    final = Path(result['path'])
    assert final.parent == destination
    assert final.read_bytes() == b'encoded video'
    assert published[0][0].is_relative_to(destination)
    assert outputs[0].is_relative_to(cache)
    assert list(destination.iterdir()) == [final]
    assert list(cache.iterdir()) == []
    assert not list(destination.glob('.longvideo-export-*'))


def test_export_keeps_existing_video_and_sidecars(export_case):
    project, group, cache, destination, outputs = export_case
    for suffix in ('.mp4', '.srt', '.json'):
        (destination / (group['name'] + suffix)).write_bytes(b'previous export')
    result = media.render(project, [group], 'test', output_directory=destination)[0]
    assert result['file_name'] == group['name'] + '_02.mp4'
    for suffix in ('.mp4', '.srt', '.json'):
        assert (destination / (group['name'] + suffix)).read_bytes() == b'previous export'
    assert sorted(path.name for path in destination.iterdir()) == sorted([
        group['name'] + suffix for suffix in ('.mp4', '.srt', '.json')
    ] + [result['file_name']])


def test_legacy_sidecars_are_preserved_without_renaming_new_mp4(export_case):
    project, group, cache, destination, outputs = export_case
    legacy = [destination / (group['name'] + suffix) for suffix in ('.srt', '.json')]
    for path in legacy:
        path.write_bytes(b'legacy sidecar')
    result = media.render(project, [group], 'test', output_directory=destination)[0]
    assert result['file_name'] == group['name'] + '.mp4'
    assert set(destination.iterdir()) == {*legacy, Path(result['path'])}
    assert all(path.read_bytes() == b'legacy sidecar' for path in legacy)


@pytest.mark.parametrize('failure', ['encode', 'cancel'])
def test_failed_or_cancelled_merge_removes_partial_output(export_case, monkeypatch, failure):
    project, group, cache, destination, outputs = export_case
    encode = media.ffmpeg
    cancel = Event()

    def failed_merge(args, duration_s, progress=None, cancel=None):
        if Path(args[-1]).suffix == '.mp4':
            if failure == 'cancel':
                cancel.set()
            else:
                Path(args[-1]).write_bytes(b'incomplete video')
                raise RuntimeError('synthetic encoding failure')
        return encode(args, duration_s, progress, cancel)

    monkeypatch.setattr(media, 'ffmpeg', failed_merge)
    expected = RuntimeError if failure == 'encode' else InterruptedError
    with pytest.raises(expected):
        media.render(project, [group], 'test', cancel=cancel, output_directory=destination)
    assert list(destination.iterdir()) == []
    assert list(cache.iterdir()) == []
