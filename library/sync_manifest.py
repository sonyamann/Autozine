"""Bring manifest.csv in line with the files in the theme folders.

- A file that was deleted drops its manifest row.
- A file moved to another theme folder gets its path and theme updated.
- A file in unused/<theme>/ is kept in the manifest with status "unused" and is never picked.
  Moving it back into a theme folder makes it usable again and clears the flag that set it aside.
- A file whose pixel size changed (cropped by hand) gets its size updated, any recorded
  crop cleared, its "needs cropping" flag lifted, and its small-size legibility re-checked.
- A file with no manifest ID is listed so it can be classified.

Files are matched by the ID prefix in their name (e.g. A072_022.jpg).
Run from the project root: python3 library/sync_manifest.py [--dry-run]
"""
import csv
import os
import re
import sys

from PIL import Image, ImageStat

Image.MAX_IMAGE_PIXELS = None

LIBRARY = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(LIBRARY, 'manifest.csv')
THEMES = ['nature', 'domestic', 'high_techne', 'cosmopolitan', 'folklore', 'ornament']


def main(dry_run):
    root = os.path.dirname(LIBRARY)
    on_disk, unknown = {}, []
    for theme, sub in [(t, t) for t in THEMES] + [(t, os.path.join('unused', t)) for t in THEMES]:
        folder = os.path.join(LIBRARY, sub)
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            if name.startswith('.'):
                continue
            m = re.match(r'(A\d+)_', name)
            path = os.path.relpath(os.path.join(folder, name), root)
            if m:
                on_disk[m.group(1)] = (theme, path)
            else:
                unknown.append(path)

    with open(MANIFEST, newline='') as fh:
        rows = list(csv.DictReader(fh))
    fields = list(rows[0])

    kept, removed, moved, cropped = [], [], [], []
    for row in rows:
        if row['id'] not in on_disk:
            removed.append(row)
            continue
        theme, path = on_disk[row['id']]
        unused = os.sep + 'unused' + os.sep in os.sep + path
        if path != row['path']:
            was = row['theme'] + (' (unused)' if row['status'] == 'unused' else '')
            moved.append((row['id'], was, theme + (' (unused)' if unused else '')))
            if row['status'] == 'unused' and not unused:      # brought back by hand: lift the block
                row['flags'] = '; '.join(f for f in row['flags'].split('; ')
                                         if f and not f.startswith(('faint', 'nudity', 'cropped text')))
            row['theme'], row['path'] = theme, path
            row['status'] = 'unused' if unused else 'ok'
        with Image.open(os.path.join(root, row['path'])) as im:
            if im.size != (int(row['width']), int(row['height'])):
                row['width'], row['height'] = im.size
                row['crop'] = ''
                row['flags'] = '; '.join(f for f in row['flags'].split('; ')
                                         if f and not f.startswith(('needs cropping', 'not used until cropped', 'below full-page')))
                if row['description']:      # same test used when the library was first marked
                    contrast = ImageStat.Stat(im.convert('L').resize((28, 28), Image.BOX)).stddev[0]
                    row['small_ok'] = 'yes' if contrast >= 38 else 'no'
                cropped.append(row['id'])
        kept.append(row)

    print(f'{len(removed)} removed, {len(moved)} moved, {len(cropped)} cropped, {len(kept)} rows kept')
    for row in removed:
        print(f"  removed {row['id']}  {row['subject']}")
    for id_, old, new in moved:
        print(f'  moved   {id_}  {old} -> {new}')
    if unknown:
        print(f'{len(unknown)} files not in the manifest:')
        for path in unknown:
            print(f'  {path}')

    if cropped:
        print('  cropped ' + ' '.join(cropped))
    if not dry_run and (removed or moved or cropped):
        with open(MANIFEST, 'w', newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(kept)


if __name__ == '__main__':
    main('--dry-run' in sys.argv)
