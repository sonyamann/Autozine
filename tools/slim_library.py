"""Write smaller copies of the library's pictures for the server.

The originals are about 5 GB and live only on Sonya's Mac. Spreads use each picture at
1700 px at most (make_spread.cached_image), so the server gets copies at most 2000 px
on the long side, with headroom for crops. Copies keep their path and file type, so the
manifest works unchanged. Pictures retired to library/unused/ are skipped, and any copy of one
left from an earlier run is deleted, so the deploy's rsync --delete drops it from the server too.

    .venv/bin/python tools/slim_library.py build/library-slim     # then tools/deploy.sh sends it
"""
import csv
import os
import sys

from PIL import Image

Image.MAX_IMAGE_PIXELS = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONG_SIDE = 2000


def main(out_root):
    with open(os.path.join(ROOT, 'library', 'manifest.csv'), encoding='utf8', newline='') as fh:
        rows = [r for r in csv.DictReader(fh) if r['status'] == 'ok']
    made = 0
    for r in rows:
        src, out = os.path.join(ROOT, r['path']), os.path.join(out_root, r['path'])
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src):
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        im = Image.open(src)
        ext = os.path.splitext(out)[1].lower()
        if ext == '.jpg':
            im = im.convert('RGB')
        elif im.mode not in ('RGB', 'RGBA', 'L', 'LA'):
            im = im.convert('RGBA')
        im.thumbnail((LONG_SIDE, LONG_SIDE), Image.LANCZOS)
        if ext == '.jpg':
            im.save(out, quality=90)
        elif ext == '.tif':
            im.save(out, compression='tiff_lzw')
        else:
            im.save(out, optimize=True)
        made += 1
    keep = {os.path.normpath(os.path.join(out_root, r['path'])) for r in rows}
    pruned = 0
    for folder, _, names in os.walk(out_root):
        for name in names:
            path = os.path.normpath(os.path.join(folder, name))
            if path not in keep:
                os.remove(path)
                pruned += 1
    for folder, dirs, names in os.walk(out_root, topdown=False):     # and any folder left empty
        if folder != out_root and not os.listdir(folder):
            os.rmdir(folder)
    print(f'{made} pictures written, {len(rows) - made} already current, {pruned} retired copies removed, in {out_root}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'build', 'library-slim'))
