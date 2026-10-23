import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from fractions import Fraction

from storage import DATA, TOOLS, export_directory, json_write
from domain import composition, subtitles, choose_names
from runtime_paths import is_frozen

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def binaries():
    if is_frozen():
        bundled = {name: TOOLS / (name + '.exe') for name in ('ffmpeg', 'ffprobe')}
        if not all(path.is_file() for path in bundled.values()):
            raise ValueError('程序的音视频资源不完整，请重新完整解压免安装包，不要单独移动 EXE。')
        return {name: str(path) for name, path in bundled.items()}
    config = TOOLS / 'paths.json'
    if config.exists():
        return json.loads(config.read_text(encoding='utf-8'))
    import imageio_ffmpeg
    return {'ffmpeg': imageio_ffmpeg.get_ffmpeg_exe(), 'ffprobe': ''}


def verify_source(project):
    path = Path(project['path'])
    if not path.is_file():
        raise ValueError('原素材已移动，请重新导入')
    stat = path.stat()
    if stat.st_size != project['source_size'] or stat.st_mtime_ns != project['source_mtime_ns']:
        raise ValueError('原素材已发生变化，请重新导入，避免使用旧时间轴或音频缓存')


def probe(path):
    exe = binaries().get('ffprobe')
    if exe and Path(exe).is_file():
        p = subprocess.run([exe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                           capture_output=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW,
                           timeout=60)
        if p.returncode:
            raise ValueError('无法读取媒体信息：' + p.stderr[-300:])
        raw = json.loads(p.stdout)
        video = next((x for x in raw['streams'] if x['codec_type'] == 'video'), None)
        audio = next((x for x in raw['streams'] if x['codec_type'] == 'audio'), None)
        if not video:
            raise ValueError('素材没有视频轨')
        rate = video.get('avg_frame_rate', '0/0')
        if rate == '0/0':
            rate = video.get('r_frame_rate', '25/1')
        fps = float(Fraction(rate))
        duration = float(raw['format'].get('duration', video.get('duration', 0)))
        rotation = next((float(s['rotation']) for s in video.get('side_data_list', [])
                         if 'rotation' in s), float(video.get('tags', {}).get('rotate', 0)))
        rotated = round(rotation) % 180 == 90
        return {'duration_ms': round(duration * 1000), 'width': video['width'], 'height': video['height'],
                'rotation': rotation, 'display_width': video['height'] if rotated else video['width'],
                'display_height': video['width'] if rotated else video['height'],
                'fps': fps, 'fps_fraction': rate, 'video_codec': video.get('codec_name'),
                'audio_codec': audio.get('codec_name') if audio else None,
                'audio_channels': audio.get('channels') if audio else 0,
                'color_transfer': video.get('color_transfer', ''), 'raw': raw}
    # Working fallback until a dedicated ffprobe binary is available.
    p = subprocess.run([binaries()['ffmpeg'], '-hide_banner', '-i', str(path)], capture_output=True,
                       encoding='utf-8', errors='replace', creationflags=NO_WINDOW, timeout=60)
    s = p.stderr
    dur = re.search(r'Duration: (\d+):(\d+):([\d.]+)', s)
    size = re.search(r'Video:.*? (\d{2,5})x(\d{2,5})', s)
    fps = re.search(r' ([\d.]+) fps', s)
    if not dur or not size:
        raise ValueError('无法读取素材，请配置ffprobe')
    return {'duration_ms': round((int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3])) * 1000),
            'width': int(size[1]), 'height': int(size[2]), 'fps': float(fps[1]) if fps else 25,
            'fps_fraction': fps[1] if fps else '25', 'video_codec': 'unknown',
            'audio_codec': 'unknown' if 'Audio:' in s else None, 'audio_channels': 2, 'raw': {}}


def ffmpeg(args, duration_s, progress=None, cancel=None):
    command = [binaries()['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-y', '-progress', 'pipe:1', '-nostats', *map(str, args)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
    try:
        for line in process.stdout:
            if cancel and cancel.is_set():
                process.terminate()
                raise InterruptedError('任务已取消')
            key, _, value = line.strip().partition('=')
            if key == 'out_time_us' and progress:
                try:
                    progress(min(0.99, int(value) / 1000000 / max(duration_s, 0.001)))
                except ValueError:
                    pass
        error = process.stderr.read()
        code = process.wait()
        if code:
            raise RuntimeError('FFmpeg处理失败：' + error[-700:])
        if cancel and cancel.is_set():
            raise InterruptedError('任务已取消')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def extract_audio(project, start_ms=0, end_ms=None, progress=None, cancel=None):
    verify_source(project)
    end_ms = min(end_ms or project['media']['duration_ms'], project['media']['duration_ms'])
    duration = (end_ms - start_ms) / 1000
    if duration <= 0:
        raise ValueError('识别范围为空')
    target = DATA / 'audio' / f'{project["id"]}_{start_ms}_{end_ms}_mono16k_64k.mp3'
    if target.exists() and target.stat().st_size > 100:
        return target, duration
    temp = target.with_name(target.stem + '.part.mp3')
    ffmpeg(['-ss', start_ms / 1000, '-i', project['path'], '-t', duration, '-map', '0:a:0',
            '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '64k', temp],
           duration, progress, cancel)
    os.replace(temp, target)
    return target, duration


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def render(project, groups, job_id, progress=None, cancel=None, output_directory=None):
    verify_source(project)
    destination = Path(output_directory) if output_directory is not None else export_directory()
    if not destination.is_dir():
        raise ValueError('导出目录不存在，请在设置中重新保存路径。')
    plans = choose_names(groups, [p.name for p in destination.iterdir()])
    results = []
    fps = project['media']['fps']
    if project['media'].get('color_transfer') in ('smpte2084', 'arib-std-b67'):
        raise ValueError('检测到HDR素材，需要单独配置色彩处理后再导出')
    for gidx, (group, plan) in enumerate(zip(groups, plans)):
        spans = composition(group, project['media']['duration_ms'], fps)
        if not spans:
            continue
        with tempfile.TemporaryDirectory(prefix='render_', dir=DATA / 'jobs') as td:
            folder = Path(td)
            parts = []
            for i, span in enumerate(spans):
                if cancel and cancel.is_set():
                    raise InterruptedError('任务已取消，已完成文件保留')
                part = folder / f'{i:05d}.mkv'
                dur = span['duration_ms'] / 1000
                def advance(p):
                    if progress:
                        progress((gidx + (i + p) / (len(spans) + 1)) / len(groups),
                                 f'正在导出 {group["name"]}，区段 {i+1}/{len(spans)}')
                ffmpeg(['-ss', span['start_ms'] / 1000, '-i', project['path'], '-t', dur,
                        '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'veryfast',
                        '-crf', '18', '-pix_fmt', 'yuv420p', '-r', project['media']['fps_fraction'],
                        '-fps_mode', 'cfr', '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2',
                        '-af', f'apad=whole_dur={dur:.6f}', part], dur, advance, cancel)
                info = probe(part)
                span['actual_duration_ms'] = info['duration_ms']
                parts.append(part)
            listing = folder / 'concat.txt'
            listing.write_text(''.join(f"file '{p.name}'\n" for p in parts), encoding='utf-8')
            merged = folder / 'merged.mp4'
            total = sum(s['actual_duration_ms'] for s in spans) / 1000
            ffmpeg(['-f', 'concat', '-safe', '0', '-i', listing, '-c:v', 'copy', '-c:a', 'aac',
                    '-b:a', '192k', '-movflags', '+faststart', merged], total, None, cancel)
            final = destination / plan['file_name']
            # Windows rename is non-overwriting. Re-resolve names if another app created a file meanwhile.
            if final.exists():
                plan = choose_names([group], [p.name for p in destination.iterdir()])[0]
                final = destination / plan['file_name']
            os.rename(merged, final)
            final.with_suffix('.srt').write_text(subtitles(spans), encoding='utf-8-sig')
            bundle = {'schema': 1, 'source_path': project['path'], 'source_id': project['id'],
                      'group': group, 'spans': spans, 'file_name': final.name,
                      'created_at': time.time(), 'model': 'paraformer-v2'}
            json_write(final.with_suffix('.json'), bundle)
            result = {'name': group['name'], 'file_name': final.name, 'path': str(final),
                      'duration_ms': probe(final)['duration_ms'], 'group_id': group['id']}
            results.append(result)
            if progress:
                progress((gidx + 1) / len(groups), f'已导出 {final.name}', results)
    return results
