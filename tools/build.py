#!/usr/bin/env python3
"""
GUItober site build.

  python3 tools/build.py            # optimise new/changed media + regenerate assets/js/data.js
  python3 tools/build.py --data     # only regenerate data.js (fast, no media work)

Reads everything from content/ (JSON + Markdown + source images) and writes:
  media/…                optimised previews (webp), full-size images, mp4 videos + posters
  assets/js/data.js      one bundle with all content, so the site also works from file://

Nothing in content/ is modified. Media is only re-encoded when the source is newer
than its output, so re-runs are cheap.
Requires: Pillow, ffmpeg (for videos / gifs).
"""
import json, os, subprocess, sys, shutil, tempfile
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(ROOT, 'content')
MEDIA = os.path.join(ROOT, 'media')

THUMB_W = 720        # gallery preview width
FULL_MAX = 2400      # longest side of the image shown in the modal
IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp'}
VID_EXT = {'.mp4', '.mov', '.m4v', '.webm', '.gif'}


def rel(p):
    return os.path.relpath(p, ROOT).replace(os.sep, '/')


def fresh(src, dst):
    # an empty output (e.g. an interrupted run) is never considered fresh
    return os.path.exists(dst) and os.path.getsize(dst) > 0 and os.path.getmtime(dst) >= os.path.getmtime(src)


def load(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def read_text(p):
    if not p or not os.path.exists(p):
        return ''
    with open(p, encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------- media workers
def do_image(job):
    from PIL import Image, ImageOps
    src, out_t, out_f = job
    os.makedirs(os.path.dirname(out_t), exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ('RGB', 'RGBA'):
            im = im.convert('RGBA' if 'A' in im.getbands() else 'RGB')
        w, h = im.size
        if not fresh(src, out_f):
            f = im.copy()
            if max(w, h) > FULL_MAX:
                f.thumbnail((FULL_MAX, FULL_MAX), Image.LANCZOS)
            f.save(out_f, 'WEBP', quality=88, method=4)
        if not fresh(src, out_t):
            t = im.copy()
            if w > THUMB_W:
                t = t.resize((THUMB_W, round(h * THUMB_W / w)), Image.LANCZOS)
            t.save(out_t, 'WEBP', quality=78, method=4)
    return src, w, h


def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height:stream_side_data=rotation', '-of', 'json', path],
                       capture_output=True, text=True)
    s = json.loads(r.stdout or '{}').get('streams', [{}])[0]
    w, h = s.get('width', 16), s.get('height', 9)
    rot = 0
    for sd in s.get('side_data_list', []) or []:
        rot = int(sd.get('rotation', 0) or 0)
    if abs(rot) in (90, 270):
        w, h = h, w
    return w, h


def do_video(job):
    # temp files live in the system temp dir; results are copied in place
    # (works on folders where deleting/renaming files is not allowed)
    src, out_v, out_t = job
    os.makedirs(os.path.dirname(out_v), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpd:
        if not fresh(src, out_v):
            tmp = os.path.join(tmpd, 'v.mp4')
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', src,
                            '-vf', "scale='trunc(min(1280,iw)/2)*2':-2:flags=lanczos,format=yuv420p",
                            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '26',
                            '-movflags', '+faststart', '-c:a', 'aac', '-b:a', '96k', tmp], check=True)
            shutil.copyfile(tmp, out_v)
        if not fresh(src, out_t):
            png = os.path.join(tmpd, 'p.png')
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', '0.5', '-i', out_v, '-frames:v', '1', png])
            if not os.path.exists(png):
                subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', out_v, '-frames:v', '1', png], check=True)
            from PIL import Image
            with Image.open(png) as im:
                im = im.convert('RGB')
                if im.width > THUMB_W:
                    im = im.resize((THUMB_W, round(im.height * THUMB_W / im.width)), Image.LANCZOS)
                im.save(out_t, 'WEBP', quality=78)
    w, h = probe(out_v)
    return src, w, h


# ---------------------------------------------------------------- build
def build(media=True):
    site = load(os.path.join(CONTENT, 'site.json'))
    people = load(os.path.join(CONTENT, 'people.json'), [])
    bundle = {'site': site, 'people': {p['id']: p for p in people}, 'years': {}}

    img_jobs, vid_jobs, work_refs = [], [], []

    for year in site['years']:
        ydir = os.path.join(CONTENT, 'years', str(year))
        cfg = load(os.path.join(ydir, 'year.json'))
        prompts = load(os.path.join(ydir, 'prompts.json'), [])
        for p in prompts:
            p['text'] = read_text(os.path.join(ydir, p['text'])) if p.get('text') else ''
            if p.get('image'):
                p['image'] = rel(os.path.join(ydir, p['image']))
        cfg['prompts'] = prompts
        cfg['rulesText'] = read_text(os.path.join(ydir, cfg.get('rules', 'rules.md')))
        # optional: second-column text and an accent-framed note on the rules page
        cfg['rulesAsideText'] = read_text(os.path.join(ydir, cfg['rulesAside'])) if cfg.get('rulesAside') else ''
        cfg['rulesNoteText'] = read_text(os.path.join(ydir, cfg['rulesNote'])) if cfg.get('rulesNote') else ''
        for key in ('logo', 'logoMini', 'promptsImage'):
            if cfg.get(key):
                cfg[key] = rel(os.path.join(ydir, cfg[key]))
        cfg['rulesImages'] = [rel(os.path.join(ydir, x)) for x in cfg.get('rulesImages', [])]
        for m in cfg.get('medals', []):
            m['image'] = rel(os.path.join(ydir, m['image']))
        for f in (cfg.get('features') or {}).values():
            if f.get('image'):
                f['image'] = rel(os.path.join(ydir, f['image']))

        works = load(os.path.join(ydir, 'works.json'), [])
        used_bases = set()
        out = []
        for w in works:
            src = os.path.join(ydir, w['file'])
            if not os.path.exists(src):
                print('missing', src)
                continue
            base, ext = os.path.splitext(w['file'])
            ext = ext.lower()
            mbase = os.path.join(MEDIA, str(year), base.replace('works/', '', 1))
            if mbase in used_bases:            # e.g. name_01.jpg and name_01.png in one folder
                mbase += '-' + ext.lstrip('.')
            used_bases.add(mbase)
            item = {'id': w['id'], 'author': w['author'], 'days': w.get('days', []), 'file': w['file']}
            if w.get('title'):
                item['title'] = w['title']
            if ext in IMG_EXT:
                t, f = mbase + '.t.webp', mbase + '.f.webp'
                item.update(type='image', thumb=rel(t), full=rel(f))
                img_jobs.append((src, t, f))
            elif ext in VID_EXT:
                v, t = mbase + '.mp4', mbase + '.t.webp'
                item.update(type='video', thumb=rel(t), video=rel(v))
                vid_jobs.append((src, v, t))
            else:
                continue
            work_refs.append((src, item))
            out.append(item)
        cfg['works'] = out
        bundle['years'][str(year)] = cfg

    sizes = {}
    cache_p = os.path.join(MEDIA, '.sizes.json')
    cache = load(cache_p, {})
    if media:
        todo_i = [j for j in img_jobs if not (fresh(j[0], j[1]) and fresh(j[0], j[2]) and rel(j[0]) in cache)]
        todo_v = [j for j in vid_jobs if not (fresh(j[0], j[1]) and fresh(j[0], j[2]) and rel(j[0]) in cache)]
        print(f'images: {len(todo_i)} to process, videos: {len(todo_v)} to process', flush=True)
        with ProcessPoolExecutor() as ex:
            for n, (src, w, h) in enumerate(ex.map(do_image, todo_i), 1):
                cache[rel(src)] = [w, h]
                if n % 25 == 0:
                    print(f'  images {n}/{len(todo_i)}', flush=True)
                    json.dump(cache, open(cache_p, 'w'))
        json.dump(cache, open(cache_p, 'w'))
        with ProcessPoolExecutor(max_workers=2) as ex:
            for n, (src, w, h) in enumerate(ex.map(do_video, todo_v), 1):
                cache[rel(src)] = [w, h]
                print(f'  video {n}/{len(todo_v)} {os.path.basename(src)}', flush=True)
                json.dump(cache, open(cache_p, 'w'))
    for src, item in work_refs:
        w, h = cache.get(rel(src), [4, 3])
        item['w'], item['h'] = w, h

    os.makedirs(os.path.join(ROOT, 'assets', 'js'), exist_ok=True)
    with open(os.path.join(ROOT, 'assets', 'js', 'data.js'), 'w', encoding='utf-8') as f:
        f.write('/* generated by tools/build.py — do not edit, edit content/ instead */\n')
        f.write('window.GUITOBER = ')
        json.dump(bundle, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    print('data.js written:', sum(len(y['works']) for y in bundle['years'].values()), 'works')


if __name__ == '__main__':
    build(media='--data' not in sys.argv)
