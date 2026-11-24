"""Split the corpus texts into individual snippets.

Reads corpus/texts/*.txt and corpus/kindle/snippets.csv; writes corpus/snippets.csv,
one row per snippet. Decisions made by hand (drops, theme overrides) live in
corpus/overrides.csv and are re-applied on every run, so this is safe to re-run.
An override matches a snippet by id, or by source and key (e.g. a headword).

Run from the project root: python3 corpus/build_snippets.py
"""
import csv
import os
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
TEXTS = os.path.join(HERE, 'texts')
FIELDS = ['id', 'source', 'kind', 'key', 'text', 'words', 'theme', 'secondary_theme',
          'sensitive', 'status', 'flags', 'note']

# Words that mark a snippet as unsuitable beside a grief, health or conflict tweet.
DARK = re.compile(
    r'\b(death|dead|die[sd]?|dying|grave|corpse|funeral|coffin|tomb|burial|buried|mourn\w*|'
    r'widow\w*|orphan\w*|suicide|murder\w*|kill\w*|slain|hang(ed|ing)|execution\w*|'
    r'disease\w*|sick\w*|illness|plague|cancer|insan\w*|lunatic\w*|mad(ness|man|house)|'
    r'war|battle|massacre|blood\w*|starv\w*|famine|drown\w*|poison\w*)\b', re.I)
# Words that suggest an ethnic, religious or sexist jab; these need a human decision.
JAB = re.compile(
    r'\b(negro\w*|nigger\w*|darky|darkies|colored (man|people|person)|mulatto\w*|chinaman|chinese|'
    r'chinee|mongolian\w*|japanese|jap|jew\w*|hebrew\w*|israelite\w*|mahometan\w*|mohammedan\w*|moslem\w*|'
    r'mussulman\w*|hindoo\w*|indian\w*|redskin\w*|squaw\w*|savage\w*|heathen\w*|barbarian\w*|aborigin\w*|'
    r'gipsy|gipsies|gypsy|gypsies|irish\w*|hottentot\w*|cannibal\w*|kaffir\w*|turk\w*|arab\w*|mexican\w*|'
    r'italian\w*|polish|polack\w*|dutchman|african\w*|ethiopian\w*|esquimau\w*|oriental\w*|'
    r'woman|women|wife|wives|female\w*|girl\w*|maiden\w*|spinster\w*|old maid\w*|hag|'
    r'cripple\w*|idiot\w*|imbecile\w*|deaf|dumb|hunchback\w*|dwarf\w*)\b', re.I)


def read(name):
    with open(os.path.join(TEXTS, name), encoding='utf8') as fh:
        return fh.read()


def flat(text):
    return re.sub(r'\s+', ' ', text).strip()


def strip_notes(text):
    return re.sub(r'\[\d+\]', '', text)


def devils_dictionary():
    text = read('devils-dictionary.txt')
    text = text[text.index('\nABASEMENT, n.'):]
    head = re.compile(r"^([A-Z][A-Z'\- ]+(?:, [A-Z][A-Z'\- ]+)*)(?=[,.]| \()", re.M)
    text = re.sub(r'\n\s*\n[A-Z]\s*\n(?=\s*\n)', '\n', text)   # drop the bare letter headings
    blocks = re.split(r'\n\s*\n(?=[A-Z][A-Z\'\- ]+(?: \([^)\n]*\) ?[A-Z]*)?(?:, [A-Z\'\- ]+)*[,.])', text)
    out = []
    for block in blocks:
        block = block.strip('\n')
        m = head.match(block)
        if not m:
            continue
        key = m.group(1).split(',')[0].strip().title()
        # Verse is indented; prose is not. Keep both, with line breaks preserved in verse.
        paras = re.split(r'\n\s*\n', block)
        parts = []
        for p in paras:
            if p.startswith('  '):
                parts.append('\n'.join(l.rstrip() for l in p.split('\n')))
            else:
                parts.append(flat(p))
        body = '\n\n'.join(parts)
        has_verse = any(p.startswith('  ') for p in paras)
        flags = []
        if has_verse:
            flags.append('has verse')
        if len(key) == 1:
            flags.append('letter entry')
        out.append(dict(source='devils-dictionary', kind='definition', key=key, text=body, flags=flags))
    return out


def dreams():
    text = read('ten-thousand-dreams-interpreted.txt')
    text = text[text.index('\n_Abandon_.'):text.rindex('\nINDEX\n')]
    out = []
    for block in re.split(r'\n(?=_[A-Z][^_\n]*_\.?(?:\[\d+\])?\s*\n)', text):
        m = re.match(r'_([^_]+)_', block.strip())
        if not m:
            continue
        key = m.group(1).strip().rstrip('.')
        body = block.strip()[m.end():]
        for i, para in enumerate(p for p in re.split(r'\n\s*\n', body) if flat(strip_notes(p)).strip('.[] ')):
            t = flat(strip_notes(para))
            if t.startswith('[') or len(t.split()) < 5:
                continue
            out.append(dict(source='ten-thousand-dreams-interpreted', kind='portent', key=key, text=t,
                            flags=[] if i == 0 else ['secondary reading']))
    return out


def flowers():
    text = read('language-of-flowers.txt')
    out, seen = [], set()
    for line in text.split('\n'):
        m = re.match(r'^([A-Z][A-Za-z\'(),\- ]+?)\s{3,}_([^_]+?)_?\s*$', line)
        if not m:
            continue
        key, meaning = m.group(1).strip(), m.group(2).strip().rstrip('.')
        if out and key[0] < out[-1]['key'][0] and out[-1]['key'][0] == 'Z':
            break   # the second half of the book repeats the list, meaning first
        if (key, meaning) in seen:
            continue
        seen.add((key, meaning))
        out.append(dict(source='language-of-flowers', kind='flower', key=key, text=meaning + '.', flags=[]))
    return out


def proverbs():
    text = read('marriage-of-heaven-and-hell.txt')
    start = text.index('PROVERBS OF HELL')
    end = text.index('The ancient poets animated', start)
    out = []
    for para in re.split(r'\n\s*\n', text[start:end])[1:]:
        t = flat(para)
        if t and not t.isupper() and not t.startswith('[') and '*' not in t:
            out.append(dict(source='marriage-of-heaven-and-hell', kind='proverb', key='', text=t, flags=[]))
    return out


def heraclitus():
    text = read('early-greek-philosophy.txt')
    start = text.index('  (1) It is wise to hearken')
    end = text.index('Footnote 330:', start)
    out = []
    for m in re.finditer(r'\((\d+(?:[, \-–]+\d+)*)\) (.*?)(?=\n\s*\n)', text[start:end] + '\n\n', re.S):
        t = flat(strip_notes(m.group(2)))
        t = re.sub(r'\s*R\. ?P\.[\d ,a-z.]*$', '', t).strip()
        if len(t.split()) >= 3:
            out.append(dict(source='early-greek-philosophy', kind='fragment', key=f'Fragment {m.group(1)}',
                            text=t, flags=[]))
    return out


def pillow_book():
    text = read('pillow-book.txt')
    out = []
    heads = list(re.finditer(r'^\+([^+\n]+)\+\s*$', text, re.M))
    for i, h in enumerate(heads):
        title = h.group(1).strip()
        if 'Things' not in title:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        for para in re.split(r'\n\s*\n', text[h.end():end]):
            t = flat(strip_notes(para))
            if len(t.split()) >= 4 and not t.startswith('['):
                out.append(dict(source='pillow-book', kind='list item', key=title, text=t, flags=[]))
    return out


def kenko():
    text = read('miscellany-of-a-japanese-priest.txt')
    text = text[text.index('Section 1.'):text.index('\nNOTES')]
    out = []
    for block in re.split(r'\n(?=Section \d+\.)', text):
        m = re.match(r'Section (\d+)\.\s*', block)
        if not m:
            continue
        t = block[m.end():]
        t = re.sub(r'(?<=[A-Za-z,.;:!?\'])\s?\d{1,3}(?=[\s,.;:!?])', '', t)   # footnote numbers
        t = re.sub(r'\s+([!?;:,.])', r'\1', flat(t))
        t = t.replace("'^h", 'Th').replace('^', '')
        flags = ['OCR text; proofread before use']
        out.append(dict(source='miscellany-of-a-japanese-priest', kind='essay', key=f'Section {m.group(1)}',
                        text=t, flags=flags))
    return out


def tarot():
    text = read('pictorial-key-to-the-tarot.txt')
    out = []
    # Greater Arcana: "N. Name.—meanings"
    start = text.index('THE GREATER ARCANA AND THEIR DIVINATORY MEANINGS')
    for m in re.finditer(r'^(\d+|Zero)\.? ?([A-Z][A-Za-z ]+?)\.?—(.+)$', text[start:start + 12000], re.M):
        out.append(dict(source='pictorial-key-to-the-tarot', kind='tarot', key=m.group(2).strip(),
                        text=flat(m.group(3)), flags=['greater arcana']))
    # Lesser Arcana: suit line, rank line, then a paragraph containing "Divinatory Meanings:"
    for m in re.finditer(r'^(?:THE SUIT OF )?(WANDS|CUPS|SWORDS|PENTACLES)\n([A-Z][a-z]+)\n+(.+)$', text, re.M):
        body = m.group(3)
        if 'Divinatory Meanings' not in body:
            continue
        meaning = body.split('Divinatory Meanings', 1)[1].lstrip(': ').strip()
        out.append(dict(source='pictorial-key-to-the-tarot', kind='tarot',
                        key=f'{m.group(2)} of {m.group(1).title()}', text=flat(meaning), flags=['lesser arcana']))
    return out


def kindle():
    out = []
    with open(os.path.join(HERE, 'kindle', 'snippets.csv'), encoding='utf8') as fh:
        for r in csv.DictReader(fh):
            out.append(dict(id=r['id'], source=r['source'], kind='highlight', key=r['location'], text=r['text'],
                            theme=r['theme'], secondary_theme=r['secondary_theme'],
                            flags=[f for f in r['flags'].split('; ') if f], note=r['note']))
    return out


PREFIX = {'devils-dictionary': 'D', 'ten-thousand-dreams-interpreted': 'M', 'language-of-flowers': 'F',
          'marriage-of-heaven-and-hell': 'B', 'early-greek-philosophy': 'H', 'pillow-book': 'P',
          'miscellany-of-a-japanese-priest': 'Y', 'pictorial-key-to-the-tarot': 'T'}


def main():
    rows = []
    for fn in (devils_dictionary, dreams, flowers, proverbs, heraclitus, pillow_book, kenko, tarot):
        part = fn()
        for i, r in enumerate(part):
            r['id'] = f"{PREFIX[r['source']]}{i + 1:04d}"
        rows += part
    rows += kindle()

    overrides = {}
    path = os.path.join(HERE, 'overrides.csv')
    if os.path.exists(path):
        with open(path, encoding='utf8') as fh:
            for r in csv.DictReader(fh):
                overrides[r['id'] or (r['source'], r['key'])] = r

    themes = {}
    path = os.path.join(HERE, 'theme_tags.csv')       # written by tag_themes.py
    if os.path.exists(path):
        with open(path) as fh:
            themes = {r['id']: r for r in csv.DictReader(fh)}

    for r in rows:
        if r['id'] in themes and not r.get('theme'):
            r['theme'], r['secondary_theme'] = themes[r['id']]['theme'], themes[r['id']]['secondary_theme']
        r['words'] = len(r['text'].split())
        scan = r['key'] + ' ' + r['text']
        sens = []
        if DARK.search(scan):
            sens.append('dark')
        if JAB.search(scan) and r['kind'] != 'highlight':
            sens.append('review: people')
        for f in r['flags']:
            if f in ('death',) or 'suicide' in f:
                sens.append('dark')
            if 'women' in f or 'racial' in f:
                sens.append('review: people')
        r['sensitive'] = '; '.join(dict.fromkeys(sens))
        r.setdefault('theme', '')
        r.setdefault('secondary_theme', '')
        r.setdefault('note', '')
        r['status'] = 'ok'
        if 'review: people' in r['sensitive'] and r['kind'] != 'highlight':
            r['status'] = 'drop'     # mentions women, ethnicity, religion or disability; dropped unreviewed
            r['note'] = 'dropped without review (keyword match)'
        o = overrides.get(r['id']) or overrides.get((r['source'], r['key']))
        if o:
            for k in ('status', 'theme', 'secondary_theme', 'note'):
                if o.get(k):
                    r[k] = o[k]
        r['flags'] = '; '.join(r['flags'])

    with open(os.path.join(HERE, 'snippets.csv'), 'w', newline='', encoding='utf8') as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    by = Counter(r['source'] for r in rows)
    for s, n in by.items():
        ok = sum(1 for r in rows if r['source'] == s and r['status'] == 'ok')
        print(f'{s:36s} {n:5d} snippets, {ok:5d} ok')
    print(f'{len(rows)} total')


if __name__ == '__main__':
    main()
