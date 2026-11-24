"""Shortlist corpus snippets and library images for a set of tweets.

Scoring here is by shared words. With Jev available, make_spread.py takes these
shortlists and has Jev score them; without it, the top keyword match is used.
"""
import csv
import math
import os
import re

from pipeline import STOP, content_words, words

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_rarity = None


def rarity(word):
    """0 for the commonest words, about 1 for rare ones, judged from the corpus texts."""
    global _rarity
    if _rarity is None:
        counts, total = {}, 0
        folder = os.path.join(ROOT, 'corpus', 'texts')
        for name in sorted(os.listdir(folder)):
            with open(os.path.join(folder, name), encoding='utf8') as fh:
                for w in re.findall(r"[a-z]+", fh.read().lower()):
                    counts[w] = counts.get(w, 0) + 1
                    total += 1
        _rarity = (counts, total)
    counts, total = _rarity
    per_million = counts.get(word, 0) * 1e6 / total
    return max(0.0, min(1.0, 1 - math.log10(max(per_million, 1)) / 3))     # 1,000 per million and up -> 0


def whole_words(text):
    """Lowercased words that stand alone: not part of '1-year-old', a handle, or a link."""
    text = MENTION_OR_URL.sub(' ', text)
    return [w.lower() for w in re.findall(r"(?<![\w'’-])[A-Za-z]+(?![\w'’-])", text)]


MENTION_OR_URL = re.compile(r'@\w+|https?://\S+')
DEFINITION_MAX_WORDS = 45      # spec: "entries under a word limit"; never trim Bierce

# A small lexicon per theme, used to guess a theme when the quiz is skipped
# and to bias image and snippet choice.
THEME_WORDS = {
    'nature': """moth moths bird birds heron gull rooster garden tomato tomatoes plant plants houseplants flower flowers rose
        roses bee bees honey tree trees leaf leaves moon stars star sky sea fog rain river forest animal animals fox owl
        rabbit hare frog fish butterfly insect seed harvest wild weather wind storm sun""",
    'domestic': """soup tea bread kitchen spoon dishwasher house home household cook cooking recipe laundry stamps letter
        neighbor porch lamp bed sleep cat dog grandmother mother family dinner breakfast coffee cup table chair window
        umbrella diary library sewing broom candle clock""",
    'high_techne': """email password wifi printer telescope telephone machine engine rocket computer code software data
        algorithm map maps calendar bicycle hardware tool tools invention patent science experiment math number numbers
        electric wire signal radio satellite orbit robot internet server keyboard typewriter""",
    'cosmopolitan': """city cities train travel hotel museum gallery opera theatre theater fashion dress perfume wine
        cocktail restaurant cafe paris london tokyo york art artist painting portrait salon party society ambition
        money luxury taxi airport passport abroad language""",
}
THEME_WORDS = {k: set(v.split()) for k, v in THEME_WORDS.items()}


def lemmas(word):
    """Candidate dictionary forms of a word (a crude stand-in for spaCy)."""
    out = [word]
    for suf, rep in (("'s", ''), ('ies', 'y'), ('es', ''), ('s', ''), ('ied', 'y'), ('ed', ''), ('ed', 'e'),
                     ('ing', ''), ('ing', 'e'), ('ly', ''), ('ness', ''), ('ment', '')):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            stem = word[:len(word) - len(suf)] + rep
            out.append(stem)
            if len(stem) > 3 and stem[-1] == stem[-2]:
                out.append(stem[:-1])          # "stopped" -> "stop"
    return out


def load_snippets():
    with open(os.path.join(ROOT, 'corpus', 'snippets.csv'), encoding='utf8') as fh:
        return [r for r in csv.DictReader(fh) if r['status'] == 'ok']


def load_library():
    with open(os.path.join(ROOT, 'library', 'manifest.csv'), encoding='utf8') as fh:
        return [r for r in csv.DictReader(fh) if r['status'] == 'ok']


def guess_theme(tweets):
    counts = {k: 0.0 for k in THEME_WORDS}
    for t in tweets:
        for w in t['tokens']:
            for form in lemmas(w):
                for theme, vocab in THEME_WORDS.items():
                    if form in vocab:
                        counts[theme] += t['score']
                        break
    return max(sorted(counts), key=lambda k: counts[k])


def definition_matches(tweets, snippets):
    """Devil's Dictionary: exact headword matches, best first.

    Favors rarer (longer) words and higher-ranked tweets; skips high-emotion tweets.
    """
    heads = {}
    for s in snippets:
        if (s['kind'] == 'definition' and int(s['words']) <= DEFINITION_MAX_WORDS
                and 'verse' not in s['flags'] and 'letter' not in s['flags'] and 'dark' not in s['sensitive']):
            heads.setdefault(s['key'].lower(), s)
    return _headword_matches(tweets, heads)


def _dream_ok(s, max_words=40):
    return (s['kind'] == 'portent' and int(s['words']) <= max_words and 'dark' not in s['sensitive']
            and 'secondary reading' not in s['flags'])


def dream_matches(tweets, snippets):
    """Miller's dream readings whose headword a tweet uses, best first (for the B-side's card).
    Only a word's main reading ("To dream of ...") is used; follow-on readings need their context."""
    heads = {}
    for s in snippets:
        if _dream_ok(s):
            heads.setdefault(s['key'].lower(), s)
    return _headword_matches(tweets, heads)


def general_dreams(snippets):
    """Short, mild dream readings that can stand on a page without a matching tweet."""
    return [s for s in snippets if _dream_ok(s, 26) and s['text'].startswith('To dream')]


def _headword_matches(tweets, heads):
    found = []
    for t in tweets:
        if t['labels']['high_emotion']:
            continue
        for w in dict.fromkeys(whole_words(t['display'])):
            if w in STOP or len(w) < 4:
                continue
            for form in lemmas(w):
                if form in heads:
                    rare = rarity(form)
                    if rare >= 0.1:            # skip the most everyday words, like "time" and "man"
                        found.append((t['score'] ** 0.5 * (0.3 + rare), t, w, heads[form], rare))
                    break
    found.sort(key=lambda m: (-m[0], m[1]['id'], m[2]))
    return found


def highlight(text, word):
    """Wrap the first stand-alone occurrence of `word` for display."""
    return re.sub(r"(?i)(?<![\w'’-])(%s)(?![\w'’-])" % re.escape(word), '\x01\\1\x02', text, count=1)


def one_liners(tweets, snippets, n, rng, theme, per_source=True, pillow_book=False, exclude=()):
    """Short corpus lines (Blake, Heraclitus, Kindle highlights) matched by shared words. With pillow_book,
    the Pillow Book's shorter list items join the pool (for the B-side). `exclude`: snippet ids to skip."""
    kinds = ('proverb', 'fragment', 'highlight') + (('list item',) if pillow_book else ())
    pool = [s for s in snippets if s['kind'] in kinds and s['id'] not in exclude
            and 5 <= int(s['words']) <= (34 if s['kind'] == 'list item' else 28) and 'dark' not in s['sensitive'] and '\n' not in s['text']
            and len(re.findall(r'(?<=[a-z,;] )[A-Z][a-z]+', s['text'])) < 2]      # skip lines full of names
    vocab = {}
    for t in tweets:
        if not t['labels']['high_emotion']:
            for w in t['tokens']:
                for form in lemmas(w):
                    vocab[form] = max(vocab.get(form, 0), t['score'])
    scored = []
    for s in pool:
        toks = set(f for w in content_words(s['text']) for f in lemmas(w))
        score = sum(vocab.get(w, 0) for w in toks) + (0.5 if s.get('theme') == theme else 0) + rng.random() * 0.3
        scored.append((score, s['id'], s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out, sources = [], set()
    for _, _, s in scored:                      # one per source, for variety
        if not per_source or s['source'] not in sources:
            out.append(s)
            sources.add(s['source'])
        if len(out) == n:
            break
    return out


def rank_images(tweets, library, theme, rng, role=('illustration',), min_px=600, portrait=None):
    """Images for the theme, best match to the tweets first."""
    vocab = {}
    for t in tweets:
        for w in t['tokens']:
            for form in lemmas(w):
                vocab[form] = max(vocab.get(form, 0), t['score'])
    scored = []
    for a in library:
        if (a['theme'] != theme or a['role'] not in role or 'nudity' in a['flags'] or 'cropped text' in a['flags']
                or 'faint' in a['flags'] or 'needs cropping' in a['flags']):
            continue
        w, h = int(a['width']), int(a['height'])
        if max(w, h) < min_px:
            continue
        if portrait is not None and (h > w) != portrait:
            continue
        toks = set(f for x in content_words(a['subject'] + ' ' + a['title'] + ' ' + a.get('description', '')) for f in lemmas(x))
        score = sum(vocab.get(x, 0) for x in toks) + rng.random()
        scored.append((score, a['id'], a))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [a for _, _, a in scored]


def general_definitions(snippets):
    """Short, mild Devil's Dictionary entries that can stand on a page without a matching tweet."""
    return [s for s in snippets if s['kind'] == 'definition' and int(s['words']) <= 22
            and not s['sensitive'] and 'verse' not in s['flags'] and 'letter' not in s['flags']]
