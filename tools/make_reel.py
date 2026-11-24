"""Write the progress page's slot-machine reel: 40 small library pictures in app/static/reel/.

Ten per quiz theme, drawn from pictures that read well small (small_ok=yes), so the reel
shows off the whole archive. Rerun after big library changes; the files are committed.

    .venv/bin/python tools/make_reel.py
"""
import csv
import os
import random

from PIL import Image

Image.MAX_IMAGE_PIXELS = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'app', 'static', 'reel')
THEMES = ('nature', 'domestic', 'high_techne', 'cosmopolitan')


def shape(a):
    w, h = int(a['width']), int(a['height'])
    if a.get('crop'):
        l, t, r, b = (float(v) for v in a['crop'].split(','))
        w, h = w * (r - l), h * (b - t)
    return w / h


def main():
    with open(os.path.join(ROOT, 'library', 'manifest.csv'), encoding='utf8', newline='') as fh:
        rows = [r for r in csv.DictReader(fh) if r['status'] == 'ok' and r['small_ok'] == 'yes'
                and r['role'] == 'illustration' and 0.65 <= shape(r) <= 1.3 and not r['flags']]
    rng = random.Random(7)
    picks = [a for t in THEMES for a in rng.sample([r for r in rows if r['theme'] == t], 10)]
    rng.shuffle(picks)
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        os.remove(os.path.join(OUT, f))
    for n, a in enumerate(picks, 1):
        im = Image.open(os.path.join(ROOT, a['path']))
        if a.get('crop'):
            l, t, r, b = (float(v) for v in a['crop'].split(','))
            im = im.crop((int(l * im.width), int(t * im.height), int(r * im.width), int(b * im.height)))
        if im.mode in ('RGBA', 'LA', 'P'):
            im = im.convert('RGBA')
            paper = Image.new('RGBA', im.size, (251, 248, 241, 255))
            paper.alpha_composite(im)
            im = paper
        im = im.convert('RGB')
        im.thumbnail((300, 300), Image.LANCZOS)
        im.save(os.path.join(OUT, f'{n:02d}.jpg'), quality=78, optimize=True)
    print(len(picks), 'pictures in', OUT, '-', ' '.join(a['id'] for a in picks))


if __name__ == '__main__':
    main()
