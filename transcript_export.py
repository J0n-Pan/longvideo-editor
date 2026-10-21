from pathlib import Path

from domain import safe_name


def timestamp(milliseconds):
    seconds, millis = divmod(max(0, int(milliseconds)), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}'


def transcript_text(project):
    sentences = project.get('sentences') or []
    if not sentences:
        raise ValueError('当前素材尚无转写文稿，转写完成后再导出。')
    lines = [project['name'], f'原视频：{Path(project["path"]).name}', f'共 {len(sentences)} 句', '']
    for index, sentence in enumerate(sentences):
        source_index = sentence.get('source_index')
        number = source_index + 1 if isinstance(source_index, int) and source_index >= 0 else index + 1
        lines.append(f'{number}. [{timestamp(sentence["start_ms"])} --> '
                     f'{timestamp(sentence["end_ms"])}] {sentence["text"]}')
        lines.append('')
    return '\n'.join(lines)


def export_transcript(project, destination):
    content = transcript_text(project)
    destination = Path(destination)
    base = safe_name(project['name']) + '_文稿'
    for index in range(1, 10000):
        suffix = '' if index == 1 else f'_{index:02d}'
        target = destination / f'{base}{suffix}.txt'
        created = False
        try:
            with target.open('x', encoding='utf-8-sig', newline='\r\n') as output:
                created = True
                output.write(content)
        except FileExistsError:
            continue
        except OSError:
            if created:
                target.unlink(missing_ok=True)
            raise ValueError('文稿导出失败，请检查所选文件夹是否存在并且可写。') from None
        return {'file_name': target.name, 'path': str(target),
                'sentence_count': len(project['sentences'])}
    raise ValueError('同名文稿过多，请更换导出文件夹后重试。')
