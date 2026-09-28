#!/usr/bin/env python3
"""
One-off importer: pulls GUItober sources (Telegram export + participant folders)
into the site's content/ folder.

  python3 tools/import_sources.py <year> <telegram result.json> <participants folder>

What it does:
  * content/years/<year>/prompts.json + prompts/NN.md  — original daily prompt posts
    (the header line "01: BUTTONS (Кнопки)" becomes the title, hashtag line and
    signature are dropped; the rest of the text is kept verbatim, with Telegram
    formatting converted to light Markdown).
  * content/years/<year>/prompts/NN.jpg                 — the wireframe picture of the post
  * content/years/<year>/works/<nick>/<file>             — copies of participant files
  * content/years/<year>/works.json                      — one entry per file with its days
  * content/people.json                                  — shared people (merged, never overwritten)

Safe to re-run: existing people entries (links, avatars you edited) are kept.
"""
import json, os, re, shutil, sys, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MD_ESC = re.compile(r'([\\*`\[\]~])')


def esc(s):
    return MD_ESC.sub(r'\\\1', s)


def wrap_lines(s, a, b=None):
    b = a if b is None else b
    out = []
    for line in s.split('\n'):
        core = line.strip()
        if not core:
            out.append(line)
            continue
        lead = line[:len(line) - len(line.lstrip())]
        trail = line[len(line.rstrip()):]
        out.append(f'{lead}{a}{core}{b}{trail}')
    return '\n'.join(out)


def entities_to_md(ents):
    parts = []
    for e in ents:
        t, s = e['type'], e['text']
        if t in ('plain', 'hashtag', 'mention', 'custom_emoji', 'bot_command', 'email', 'phone', 'cashtag', 'bank_card', 'spoiler', 'underline'):
            parts.append(esc(s))
        elif t == 'bold':
            parts.append(wrap_lines(esc(s), '**'))
        elif t == 'italic':
            parts.append(wrap_lines(esc(s), '*'))
        elif t == 'strikethrough':
            parts.append(wrap_lines(esc(s), '~~'))
        elif t in ('code', 'pre'):
            parts.append(esc(s) if '\n' in s else f'`{s}`')
        elif t == 'text_link':
            parts.append(f"[{esc(s)}]({e['href']})")
        elif t == 'link':
            parts.append(s)
        elif t == 'blockquote':
            parts.append('\n'.join('> ' + l if l.strip() else '>' for l in esc(s).split('\n')))
        else:
            parts.append(esc(s))
    return ''.join(parts)


def plain(m):
    t = m.get('text')
    return t if isinstance(t, str) else ''.join(x if isinstance(x, str) else x.get('text', '') for x in t)


HEAD = re.compile(r'^\s*(\d{2}):\s*(.+?)\s*$')


def import_prompts(year, result_path, ydir):
    data = json.load(open(result_path, encoding='utf-8'))
    src_dir = os.path.dirname(result_path)
    posts = {}
    for m in data['messages']:
        if m.get('forwarded_from') != 'Game UI / UX community' and m.get('from') != 'Game UI / UX community':
            continue
        txt = plain(m)
        first = txt.split('\n', 1)[0]
        h = HEAD.match(first)
        if not h or f'#guitober{year}' not in txt:
            continue
        day = int(h.group(1))
        posts[day] = m  # later edits of the same day win

    os.makedirs(os.path.join(ydir, 'prompts'), exist_ok=True)
    prompts = []
    for day in sorted(posts):
        m = posts[day]
        header = HEAD.match(plain(m).split('\n', 1)[0]).group(2)
        # "BUTTONS (Кнопки)" -> en BUTTONS, ru Кнопки
        mm = re.match(r'^(.*?)\s*\((.+)\)\s*$', header)
        if mm:
            title_en, title = mm.group(1).strip(), mm.group(2).strip()
        else:
            title_en, title = header, header
        title = title[:1].upper() + title[1:] if title.isupper() is False else title.capitalize()
        if 'рефакторинг' in header.lower():
            title = 'Рефакторинг'
            title_en = 'REFACTORING'

        md = entities_to_md(m.get('text_entities', []))
        lines = md.split('\n')
        lines = lines[1:]  # drop header line (it is the title)
        # drop hashtag-only lines
        lines = [l for l in lines if not re.fullmatch(r'\s*(#guitober\w*\s*)+', l)]
        body = '\n'.join(lines).strip('\n')
        # drop trailing signature  "_____ \n Анатолий Карасов"
        body = re.sub(r'\n*\*?_{3,}\*?\s*\n\*?Анатолий Карасов\*?\s*$', '', body).rstrip()
        body = body.strip('\n') + '\n'
        open(os.path.join(ydir, 'prompts', f'{day:02d}.md'), 'w', encoding='utf-8').write(body)

        image = None
        if m.get('photo'):
            ext = os.path.splitext(m['photo'])[1].lower()
            image = f'prompts/{day:02d}{ext}'
            shutil.copy2(os.path.join(src_dir, m['photo']), os.path.join(ydir, image))
        prompts.append({
            'day': day,
            'title': title,
            'titleEn': title_en.upper(),
            'text': f'prompts/{day:02d}.md',
            'image': image,
            'source': f"telegram:{m['id']}",
        })
    json.dump(prompts, open(os.path.join(ydir, 'prompts.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'{year}: {len(prompts)} prompts')


FILE_DAYS = re.compile(r'_(\d{1,2})(?:-(\d{1,2}))?(?=(?:_v\d+)?(?:_\d+)?(?:\s*\(.*\))?\.\w+$)')
MEDIA = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.mov', '.m4v', '.webm'}


def import_works(year, part_dir, ydir):
    people_path = os.path.join(ROOT, 'content', 'people.json')
    people = json.load(open(people_path, encoding='utf-8')) if os.path.exists(people_path) else []
    by_id = {p['id'].lower(): p for p in people}
    works = []
    wdir = os.path.join(ydir, 'works')
    os.makedirs(wdir, exist_ok=True)
    for folder in sorted(os.listdir(part_dir), key=str.lower):
        fp = os.path.join(part_dir, folder)
        if not os.path.isdir(fp):
            continue
        mm = re.match(r'^(\S+?)(?:\s+\((.*)\))?$', unicodedata.normalize('NFC', folder))
        nick, name = mm.group(1), (mm.group(2) or mm.group(1))
        pid = nick.lower()
        if pid not in by_id:
            p = {'id': pid, 'name': name, 'telegram': nick, 'avatar': None,
                 'link': {'label': 'Портфолио', 'url': ''}}
            people.append(p)
            by_id[pid] = p
        os.makedirs(os.path.join(wdir, nick), exist_ok=True)
        files = sorted((f for f in os.listdir(fp) if os.path.splitext(f)[1].lower() in MEDIA), key=str.lower)
        for i, f in enumerate(files):
            safe = re.sub(r'[^\w.\-]+', '_', unicodedata.normalize('NFC', f), flags=re.UNICODE)
            dst = os.path.join(wdir, nick, safe)
            if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(os.path.join(fp, f)):
                shutil.copy2(os.path.join(fp, f), dst)
            m = FILE_DAYS.search(f)
            days = []
            if m:
                a = int(m.group(1)); b = int(m.group(2) or a)
                days = list(range(min(a, b), max(a, b) + 1))
            stem = os.path.splitext(safe)[0]
            works.append({
                'id': f'{year}-{pid}-{i + 1:02d}',
                'author': pid,
                'file': f'works/{nick}/{safe}',
                'days': days,
            })
    json.dump(works, open(os.path.join(ydir, 'works.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    people.sort(key=lambda p: p['id'])
    json.dump(people, open(people_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'{year}: {len(works)} works, {len(people)} people total')


if __name__ == '__main__':
    year, result_path, part_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    ydir = os.path.join(ROOT, 'content', 'years', year)
    os.makedirs(ydir, exist_ok=True)
    import_prompts(year, result_path, ydir)
    import_works(year, part_dir, ydir)
