#!/usr/bin/env python3
"""
Apply corrections made in the site's edit mode (index.html?edit) to content/years/<year>/works.json.

  python3 tools/apply_edits.py edits.json       # file downloaded / copied from the edit panel
  python3 tools/apply_edits.py edits.json --dry # only show what would change

edits.json format:
  { "2024": { "2024-datasin-01": {"days": [2]},
              "2024-alleksat-01": {"remove": true} } }

Removed works are dropped from works.json (their source files stay in content/…/works/).
Afterwards run:  python3 tools/build.py
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(path, dry=False):
    edits = json.load(open(path, encoding='utf-8'))
    for year, changes in edits.items():
        p = os.path.join(ROOT, 'content', 'years', str(year), 'works.json')
        works = json.load(open(p, encoding='utf-8'))
        by_id = {w['id']: w for w in works}
        out = []
        for w in works:
            ch = changes.get(w['id'])
            if not ch:
                out.append(w); continue
            if ch.get('remove'):
                print(f"{year}  remove  {w['id']}  ({w['file']})")
                continue
            if 'days' in ch and sorted(ch['days']) != sorted(w.get('days', [])):
                print(f"{year}  days    {w['id']}  {w.get('days')} -> {sorted(ch['days'])}  ({w['file']})")
                w = dict(w, days=sorted(int(d) for d in ch['days']))
            out.append(w)
        for wid in changes:
            if wid not in by_id:
                print(f"{year}  !! unknown work id {wid}")
        if not dry:
            json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('dry run, nothing written' if dry else 'works.json updated — now run: python3 tools/build.py')


if __name__ == '__main__':
    main(sys.argv[1], '--dry' in sys.argv)
