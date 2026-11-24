"""Render the free centerfold spread for a handle.

    python3 app/make_spread.py --tweets app/fixtures/sample_tweets.json
    python3 app/make_spread.py --tweets ... --theme nature --ink forest --type bookish --layout b
    python3 app/make_spread.py --handle somehandle

Outputs go to build/out/<handle>-<seed>/: spread.pdf, spread.png (2200 x 1700),
two share images, the HTML, and record.json (seed, answers, labels and every choice made,
so the same inputs always render the same spread).
"""
import argparse
import datetime
import functools
import hashlib
import html
import json
import math
import os
import random
import sys
import unicodedata

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev        # noqa: E402
import match      # noqa: E402
import pipeline   # noqa: E402
import render     # noqa: E402

Image.MAX_IMAGE_PIXELS = None
ROOT = match.ROOT
APP = os.path.join(ROOT, 'app')
MIN_DEFINITION_SCORE = 2.0      # Jev's 0-3 scale; 2 is "apt"
IMAGE_MARGIN, IMAGE_SHORTLIST = 0.3, 6     # pictures are drawn from up to 6 scoring within 0.3 of the best fit
MIN_USABLE, MAX_PAGES, MAX_TAGGED = 40, 15, 80
MIN_SPREAD = 8                   # fewer usable tweets than this and no spread is made
MIN_B_SIDE = 12                  # a B-side needs this many: six new tweets after the first spread's six
# Jev cut-offs, set from 82 hand-labeled tweets (labels/). A blocked tweet can still appear, but not beside a
# definition or quote; it costs little to block one wrongly, so these lean cautious.
RAW_AT, ATTACK_AT = 0.25, 0.5
ALONE_AT, ALONE_AT_QUOTE, ALONE_AT_IMAGE = 0.3, 0.65, 0.4   # stands-alone score needed by text / quote / image tweets
# Quote tweets point at another post (0.4 let "…so I can build this." through at 0.49, Nov 20). An image usually
# adds to words that already work, so image tweets get a gentler bar (Nov 23: @taylor_ had 101 of them, 11 plain).
MIN_ORIGINAL = 15                # below this, quote and image tweets are let back in for Jev to vet
PAGE_W, PAGE_H = 1100, 850       # CSS px: 11 x 8.5 in at 100 px per inch
# Quiz tokens: two per theme, shown as emoji in the quiz and in the spread's header.
TOKENS = {'nature': [('acorn', '🌰'), ('moth', '🦋')],
          'domestic': [('teacup', '☕'), ('thimble', '🧵')],
          'high_techne': [('rocket', '🚀'), ('typewriter', '⌨️')],
          'cosmopolitan': [('cocktail glass', '🍸'), ('steamer trunk', '🧳')]}
TOKEN_EMOJI = {name: glyph for options in TOKENS.values() for name, glyph in options}
TOKEN_THEME = {name: theme for theme, options in TOKENS.items() for name, _ in options}
PAPER_LIGHT = '#fbf8f1'     # the light paper used for cards and for prints on ink
FOLD_GAP = 18                    # a headline split across the fold stops this far either side of it
SHADOWS = dict(photo=(3, 10, .28), panel=(3, 10, .30), strip=(2, 5, .30), ticket=(2, 6, .18))   # drop, blur, alpha

INKS = {'vermilion': '#cf432b', 'ultramarine': '#2b40a3', 'forest': '#1e6b4b', 'plum': '#6c2b5d'}
TYPES = {
    'bookish': dict(head='Tapestry', body='EB Garamond', head_weight=400, head_style='normal'),
    'machine': dict(head='Space Mono', body='Space Grotesk', head_weight=700, head_style='normal'),
    'poster': dict(head='Rye', body='Work Sans', head_weight=400, head_style='normal', sub=('Zilla Slab', 700)),
}
FONT_FILES = [('Tapestry', 'Tapestry-Regular.ttf', 'normal', '400'),
              ('EB Garamond', 'EBGaramond.ttf', 'normal', '400 800'),
              ('EB Garamond', 'EBGaramond-Italic.ttf', 'italic', '400 800'),
              ('Space Mono', 'SpaceMono-Regular.ttf', 'normal', '400'),
              ('Space Mono', 'SpaceMono-Bold.ttf', 'normal', '700'),
              ('Space Grotesk', 'SpaceGrotesk.ttf', 'normal', '300 700'),
              ('Rye', 'Rye-Regular.ttf', 'normal', '400'),
              ('Zilla Slab', 'ZillaSlab-Bold.ttf', 'normal', '700'),
              ('Work Sans', 'WorkSans.ttf', 'normal', '100 900'),
              ('Work Sans', 'WorkSans-Italic.ttf', 'italic', '100 900')]
CREDITS = {'devils-dictionary': "Ambrose Bierce, The Devil's Dictionary (1911)",
           'marriage-of-heaven-and-hell': 'William Blake, The Marriage of Heaven and Hell (1790)',
           'early-greek-philosophy': 'Heraclitus, tr. John Burnet (1908)',
           'middlemarch': 'George Eliot, Middlemarch (1871–72)',
           'house-of-mirth': 'Edith Wharton, The House of Mirth (1905)',
           'of-human-bondage': 'W. Somerset Maugham, Of Human Bondage (1915)',
           'lud-in-the-mist': 'Hope Mirrlees, Lud-in-the-Mist (1926)',
           'heart-of-darkness': 'Joseph Conrad, Heart of Darkness (1899)',
           'old-new-york': 'Edith Wharton, Old New York (1924)',
           'people-of-the-abyss': 'Jack London, The People of the Abyss (1903)',
           'a-high-wind-in-jamaica': 'Richard Hughes, A High Wind in Jamaica (1929)',
           'keats-poems-1820': 'John Keats, Poems (1820)',
           'pillow-book': 'Sei Shōnagon, The Pillow Book, tr. Arthur Waley (1928)'}


def cached_image(asset, max_px=1700):
    """A browser-friendly copy of a library image. Returns (path, has_alpha, w, h)."""
    cache = os.path.join(ROOT, 'build', 'cache')
    os.makedirs(cache, exist_ok=True)
    im = Image.open(os.path.join(ROOT, asset['path']))
    if asset.get('crop'):                 # trim the paper margin or mount recorded in the manifest
        l, t, r, b = (float(v) for v in asset['crop'].split(','))
        im = im.crop((int(l * im.width), int(t * im.height), int(r * im.width), int(b * im.height)))
    alpha = im.mode in ('RGBA', 'LA', 'P') and im.convert('RGBA').getextrema()[3][0] < 250
    out = os.path.join(cache, asset['id'] + ('-c' if asset.get('crop') else '') + ('.png' if alpha else '.jpg'))
    if not os.path.exists(out):
        im = im.convert('RGBA' if alpha else 'RGB')
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        im.save(out, quality=90)
    w, h = Image.open(out).size
    return out, alpha, w, h


def unstyle(text):
    """Turn the 'styled' letters people paste in to fake italics or bold on X (e.g. U+1D608 MATHEMATICAL
    SANS-SERIF ITALIC CAPITAL A), and fullwidth letters, into plain ones. No font of ours has those characters;
    their style comes back as real italic or bold, marked with \x03..\x06 for esc()."""
    out, style = [], None
    for ch in text:
        cp = ord(ch)
        new = None
        if 0x1D400 <= cp <= 0x1D7FF or 0xFF01 <= cp <= 0xFF5E:
            name = unicodedata.name(ch, '')
            plain = unicodedata.normalize('NFKC', ch)
            new = 'b' if 'BOLD' in name and 'ITALIC' not in name else 'i' if 'ITALIC' in name else ''
            ch = plain
        if new != style:
            if style:
                out.append('\x04' if style == 'i' else '\x06')
            if new:
                out.append('\x03' if new == 'i' else '\x05')
            style = new
        out.append(ch)
    if style:
        out.append('\x04' if style == 'i' else '\x06')
    return ''.join(out)


def esc(text):
    return (html.escape(unstyle(text)).replace('\x01', '<mark>').replace('\x02', '</mark>').replace('\n', '<br>')
            .replace('\x03', '<i>').replace('\x04', '</i>').replace('\x05', '<b>').replace('\x06', '</b>'))


def quote_text(q):
    """A quotation as printed: in quote marks, or for a Pillow Book list item, after its list's title."""
    if q['kind'] == 'list item':
        return f'<i class="list">{esc(q["key"])}:</i> {esc(q["text"])}'
    return f'“{esc(q["text"])}”'


CREDIT_PX, CREDIT_LEADING = 13, 1.25      # a quotation's source line


@functools.lru_cache(maxsize=None)
def credit_lines(text, width):
    """How many lines a quotation's source takes at `width` px, so the quote above leaves it room."""
    font = ImageFont.truetype(os.path.join(APP, 'fonts', 'EBGaramond-Italic.ttf'), CREDIT_PX * 4)
    lines, line = 1, ''
    for word in text.split():
        trial = f'{line} {word}'.strip()
        if line and font.getlength(trial) / 4 > width:
            lines, line = lines + 1, word
        else:
            line = trial
    return lines


def image_credit(a):
    if a['source_collection'].startswith('Purchased'):
        return f"{a['subject']}, public domain clip art"
    who = a['author'].split(';')[0].split('(')[0].strip()
    title = (a['title'] or a['subject']).strip().rstrip('.')
    src = a['source_collection'].split(',')[0].split('(')[0].strip()
    return ', '.join(x for x in (who, title[:60], src) if x)


def use_penalty(n):
    """How far a picture's fit drops after n recent uses: 0.15 for one, 0.45 for seven, at most 1."""
    return min(1.0, 0.15 * math.log2(1 + n))


def compose(handle, tweets, theme, ink, type_name, rng, use_jev=False, after=None, uses=None):
    """Choose everything that goes on the spread. Returns a dict the template can render.

    `after` is the first spread's record when making its B-side: the B-side leaves out the first
    spread's tweets, pictures, ornaments and quotation, and its card is a dream reading instead of a
    Devil's Dictionary definition. `uses` counts how often each picture appeared on recent spreads; a
    much-used picture loses some of its fit, so popular pictures rotate out."""
    snippets, library = match.load_snippets(), match.load_library()
    b_side = after is not None
    if b_side:
        gone = set(after['tweets_used'])
        tweets = [t for t in tweets if t['id'] not in gone]
        library = [a for a in library if a['id'] not in set(after['images'])]
    plain = [t for t in tweets]
    word_matches, score_matches = ((match.dream_matches, jev.score_dreams) if b_side else
                                   (match.definition_matches, jev.score_definitions))

    # The card (a definition, or on a B-side a dream reading): the best exact match, beside its tweet.
    defs = word_matches(tweets, snippets)
    card = None
    if defs and use_jev:          # re-rank the keyword matches by how well the pairing reads
        defs = defs[:20]
        scores = score_matches([(t['display'], w, e['text']) for _, t, w, e, _ in defs])
        # Rarer words break ties, so "year" and "future" don't land on every spread.
        order = sorted(range(len(defs)), key=lambda i: (-scores[i] - 0.6 * defs[i][4], i))
        defs = [defs[i] + (scores[i],) for i in order if scores[i] >= MIN_DEFINITION_SCORE]
    if defs:
        _, t, word, entry = defs[0][:4]
        card = dict(tweet=t, word=word, entry=entry, score=defs[0][5] if use_jev else None)
    used = [card['tweet']] if card else []      # no card when nothing scores well enough

    # The headline wants a short tweet; accounts that only post long ones get their shortest. A quote or
    # image tweet never heads the spread (its context is missing), unless nothing else is left.
    own = [t for t in plain if not t['labels'].get('context')]
    hero = (pipeline.pick_diverse(own, 1, taken=used, where=lambda t: len(t['display']) <= 110)
            or pipeline.pick_diverse(own, 1, taken=used, where=lambda t: len(t['display']) <= 160)
            or sorted((t for t in own if t not in used), key=lambda t: len(t['display']))[:1]
            or sorted((t for t in plain if t not in used), key=lambda t: len(t['display']))[:1])
    used += hero
    rest = pipeline.pick_diverse(plain, 4, taken=used, where=lambda t: len(t['display']) <= 140)
    if len(rest) < 4:             # long-winded accounts: fill the empty slots with longer tweets, set smaller
        rest += pipeline.pick_diverse(plain, 4 - len(rest), taken=used + rest, where=lambda t: len(t['display']) <= 240)
    used += rest
    texts = [t['display'] for t in used]
    if use_jev:
        pool = match.one_liners(used, snippets, 10 ** 6, rng, theme, per_source=False, pillow_book=b_side,
                                exclude=[after['quote']] if b_side else ())
        cands = pool[:15] + rng.sample(pool[15:], min(60, len(pool) - 15))
        scores = jev.score_lines(texts, [q['text'] for q in cands])
        jitter = [rng.random() * 0.25 for _ in cands]      # varies the pick between near-equal quotes
        quotes = [cands[max(range(len(cands)), key=lambda i: (scores[i] + jitter[i], -i))]] if cands else []
    else:
        quotes = match.one_liners(used, snippets, 1, rng, theme, pillow_book=b_side,
                                  exclude=[after['quote']] if b_side else ())

    if card is None:
        # No tweet word matched well. Use a mild entry that suits the page as a whole,
        # shown beside one more tweet with no word highlighted.
        extra = pipeline.pick_diverse(plain, 1, taken=used, where=lambda t: len(t['display']) <= 180
                                      and not t['labels']['high_emotion'])
        pool = match.general_dreams(snippets) if b_side else match.general_definitions(snippets)
        if extra and pool:
            cands = rng.sample(pool, min(50, len(pool)))
            if use_jev:
                scorer = jev.score_general_dreams if b_side else jev.score_general_definitions
                scores = scorer(texts, [e['text'] for e in cands])
                best = max(range(len(cands)), key=lambda i: (scores[i], -i))
            else:
                scores, best = None, 0
            card = dict(tweet=extra[0], word=None, entry=cands[best], score=scores and scores[best])
            used += extra

    images = match.rank_images(used, library, theme, rng, role=('illustration', 'silhouette'))
    fit = {}                      # Jev's score for each candidate picture
    if use_jev and len(images) > 2:
        cands = images[:15] + rng.sample(images[15:], min(30, len(images) - 15))
        scores = jev.score_images(texts, [a['description'] or f"{a['subject']}. {a['title']}".strip() for a in cands])
        images = [cands[i] for i in sorted(range(len(cands)), key=lambda i: (-scores[i], i))]
        fit = {a['id']: sc for a, sc in zip(cands, scores)}

    def pick(ok):
        """A picture that passes `ok`, drawn by the seed from the near-best, not always the very best, so
        accounts with similar tweets don't all get the same picture. Better fits are likelier."""
        fits = [a for a in images if ok(a)]
        if not fits:
            return None
        if not fit:               # keyword ranking only: any of the top few
            return rng.choice(fits[:IMAGE_SHORTLIST])
        adj = {a['id']: fit[a['id']] - use_penalty((uses or {}).get(a['id'], 0)) for a in fits}
        fits.sort(key=lambda a: -adj[a['id']])                      # stable: ties keep Jev's order
        floor = adj[fits[0]['id']] - IMAGE_MARGIN
        near = [a for a in fits[:IMAGE_SHORTLIST] if adj[a['id']] >= floor]
        return rng.choices(near, weights=[adj[a['id']] - floor + .05 for a in near])[0]
    def shape(a):
        w, h = int(a['width']), int(a['height'])
        if a.get('crop'):
            l, t, r, b = (float(v) for v in a['crop'].split(','))
            w, h = w * (r - l), h * (b - t)
        return w / h
    # The main slot is upright; very narrow or very wide pictures leave it half empty.
    big = pick(lambda a: 0.62 <= shape(a) <= 1.15
               and max(int(a['width']), int(a['height'])) >= 1200) or images[0]    # the main slot prints large
    small = pick(lambda a: a['id'] != big['id'] and a['subject'][:25] != big['subject'][:25]
                 and a['source_url'] != big['source_url'] and 0.6 <= shape(a) <= 1.7
                 and a.get('small_ok') != 'no') or next(a for a in images if a['id'] != big['id'])   # fine lines go to mush small
    ornaments = [a for a in library if a['theme'] == 'ornament' and a['color'] == 'monochrome'
                 and a['path'].endswith('.png') and a.get('small_ok') != 'no'    # busy ones blur at ~100 px
                 and 'nudity' not in a['flags']]
    borders = sorted((a for a in ornaments if a['role'] == 'border' and int(a['width']) > int(a['height']) * 3),
                     key=lambda a: a['id'])
    motifs = sorted((a for a in ornaments if a['role'] in ('ornament', 'medallion')
                     and 0.7 < int(a['width']) / int(a['height']) < 1.4), key=lambda a: a['id'])
    themed = [a for a in motifs if a['secondary_theme'] == theme]      # ornaments that came from this theme
    border, motif = rng.choice(borders), rng.choice(themed if len(themed) >= 10 else motifs)

    if card:                  # how the card reads: "Abasement, n. ..." or "Knife. Broken knives, denotes ..."
        e = card['entry']
        if b_side:
            card.update(label='Ten Thousand Dreams', by='Gustavus Hindman Miller, 1901',
                        head=e['key'].strip().title(), sep='. ', body=e['text'])
        else:
            head, _, body = e['text'].partition(', ')
            card.update(label='The Devil’s Dictionary', by='Ambrose Bierce, 1911', head=head.title(), sep=', ', body=body)

    return dict(handle=handle, theme=theme, ink=ink, type=type_name, card=card, hero=hero[0], rest=rest,
                quote=quotes[0] if quotes else None, big=big, small=small, border=border, motif=motif,
                tilt=[rng.uniform(-3, 3) for _ in range(8)])


def render_html(c):
    ty, ink = TYPES[c['type']], INKS[c['ink']]
    sub = ty.get('sub') or (ty['head'], ty['head_weight'])     # font for mid-size tweets on ink
    faces = '\n'.join(
        f"@font-face{{font-family:'{fam}';src:url('file://{APP}/fonts/{fn}');font-style:{st};font-weight:{wt};}}"
        for fam, fn, st, wt in FONT_FILES)
    big, big_alpha, bw, bh = cached_image(c['big'])
    small, small_alpha, sw, sh = cached_image(c['small'])
    border = cached_image(c['border'])[0]
    motif = cached_image(c['motif'])[0]
    t, r = c['tilt'], c['rest']

    def box(x, y, w, h, rot=0.0, extra=''):
        return f'left:{x}px;top:{y}px;width:{w}px;height:{h}px;transform:rotate({rot:.2f}deg);{extra}'

    def drop(kind, x, y, w, h, rot=0.0):
        """A baked soft shadow behind a box (WeasyPrint has no box-shadow)."""
        dy, blur, alpha = SHADOWS[kind]
        path, pad = render.shadow(w, h, blur, alpha)
        return f'<img class="shadow" src="file://{path}" style="{box(x - pad, y + dy - pad, round(w) + 2 * pad, round(h) + 2 * pad, rot)}">'

    def tweet(i, cls, x, y, w, h, rot=0.0):
        if i >= len(r):
            return ''
        return ((drop(cls, x, y, w, h, rot) if cls in SHADOWS else '') +
                f'<div class="tweet {cls}" style="{box(x, y, w, h, rot)}"><div class="fit">{esc(r[i]["display"])}</div>'
                f'<span class="meta">♥ {r[i].get("likeCount", 0):,}</span></div>')

    def picture(which, x, y, w, max_h, rot, tape=True, on_ink=False):
        path, alpha, iw, ih = (big, big_alpha, bw, bh) if which == 'big' else (small, small_alpha, sw, sh)
        h = min(max_h, int(w * ih / iw))
        if h == max_h:
            w = min(w, int(max_h * iw / ih * 1.15))      # crop a little rather than letterbox
        # A cut-out on ink: a one-color silhouette wholly on ink prints in paper color; anything else
        # (a color cut-out, or one that might straddle the ink's edge) gets a paper card behind it.
        card = False
        if alpha and on_ink == 'tint' and c[which]['color'] == 'monochrome':
            path = render.tinted(path, PAPER_LIGHT)
        elif alpha and on_ink:
            alpha, card = False, True
        if alpha:     # cut-out: shadow of its outline, inside the box so it turns with it
            s = min(w / iw, h / ih)
            dw, dh = iw * s, ih * s
            spath, pad = render.shadow(dw, dh, 5, .25, mask_path=path)
            sx, sy = (w - dw) / 2 - pad, (h - dh) / 2 + 3 - pad
            out = (f'<div class="cutout" style="{box(x, y, w, h, rot)}"><img class="shadow" src="file://{spath}" '
                   f'style="left:{sx:.1f}px;top:{sy:.1f}px;width:{round(dw) + 2 * pad}px;height:{round(dh) + 2 * pad}px">'
                   f'<img class="art" src="file://{path}"></div>')
        else:
            out = drop('photo', x, y, w, h, rot) + \
                f'<div class="photo framed{" card" if card else ""}" style="{box(x, y, w, h, rot)}"><img src="file://{path}"></div>'
        if tape and not alpha:
            out += f'<div class="tape" style="left:{x - 28}px;top:{y - 8}px;transform:rotate(-34deg)"></div>'
            out += f'<div class="tape" style="left:{x + w - 62}px;top:{y + h - 18}px;transform:rotate(-38deg)"></div>'
        return out

    def tinted(path, x, y, w, h, rot=0.0, color=None, pos='center'):
        return (f'<img class="tint" src="file://{render.tinted(path, color or ink)}" '
                f'style="{box(x, y, w, h, rot)}object-position:{pos}">')

    def split_hero(top, height, size):
        """A headline across both pages; render.split_headlines() breaks its lines around the fold."""
        return (f'<div class="hero split" data-left="44" data-right="{PAGE_W // 2 + FOLD_GAP}" '
                f'data-width="{PAGE_W // 2 - 44 - FOLD_GAP}" data-top="{top}" data-height="{height}" data-size="{size}">'
                f'{esc(c["hero"]["display"])}</div>')

    def hero(x, y, w, h, size):
        return f'<div class="hero" style="{box(x, y, w, h)}font-size:{size}px"><div class="fit">{esc(c["hero"]["display"])}</div></div>'


    def pair(x, y):
        if not c['card']:           # no definition scored well enough: a spare tweet takes the slot
            if len(r) < 4:
                return ''
            return (f'<div class="pair" style="left:{x}px;top:{y}px"><div class="tweet spare"><div class="fit">'
                    f'{esc(r[3]["display"])}</div><span class="meta">♥ {r[3].get("likeCount", 0):,}</span></div></div>')
        cd = c['card']
        return f'''<div class="pair" style="left:{x}px;top:{y}px;transform:rotate({t[0] * .4:.2f}deg)">
      <div class="tweet source"><div class="fit">{esc(match.highlight(c['card']['tweet']['display'], c['card']['word']) if c['card']['word'] else c['card']['tweet']['display'])}</div>
        <span class="meta">♥ {c['card']['tweet'].get('likeCount', 0):,}</span></div>
      <div class="defshadow"></div><div class="defcard"><div class="label">{esc(cd['label'])}</div>
        <div class="fit"><b>{esc(cd['head'])}</b>{cd['sep']}{esc(cd['body'])}</div>
        <div class="by">{esc(cd['by'])}</div></div></div>'''

    def quote(x, y, w, h, cls=''):
        if not c['quote']:
            return ''
        by = CREDITS.get(c['quote']['source'], c['quote']['source'])
        room = 10 + credit_lines(by, w - 14) * CREDIT_PX * CREDIT_LEADING     # the credit sits below the quote
        return (f'<div class="quote {cls}" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;padding-bottom:{room:.0f}px">'
                f'<div class="fit">{quote_text(c["quote"])}</div>'
                f'<div class="by">{esc(by)}</div></div>')

    def credit(cls=''):
        text = ' · '.join([image_credit(c['big']), image_credit(c['small'])])
        return f'<div class="credit {cls}">Images: {esc(text)}. Ornament: public domain clip art. Made with Autozine.</div>'

    def brand(cls=''):
        tok = f'<span class="tok">{c["token"]}</span>' if c.get('token') else ''
        return f'<div class="brand {cls}">{tok}@{esc(c["handle"])} <span>· a zine</span></div>'

    # Layout a's dots peek out beside the photo rather than behind the headline, which they made hard to read (Nov 19).
    if c['layout'] == 'a':        # image and headline left; cards right over an ink block
        body = f'''<div class="wash"></div><div class="block"></div><div class="dots" style="left:10px;top:300px;width:190px;height:190px"></div>
{brand()}{picture('big', 88, 62, 410, 410, t[1])}{tinted(motif, 392, 36, 120, 120, t[2] * 2)}
{hero(44, 500, 470, 250, 64)}
{pair(584, 40)}{quote(584, 300, 250, 120)}{picture('small', 858, 290, 200, 250, t[3], tape=False)}
{tweet(0, 'strip', 600, 446, 270, 96, t[4] * .7)}{tweet(1, 'onink', 584, 590, 300, 150)}
{tweet(2, 'ticket', 900, 600, 176, 170, t[5])}
{tinted(border, 584, 762, 300, 34, color='#fbf8f1', pos='left center')}{credit()}'''
    elif c['layout'] == 'b':      # gallery wall: ink page with the picture left; words right
        panel_box = (66, 72, 418, min(484, int(378 * bh / bw)) + 40, t[1] * .5)
        body = f'''<div class="inkpage"></div><div class="dots" style="left:820px;top:560px"></div>
{brand('light')}{drop('panel', *panel_box)}<div class="panel" style="{box(*panel_box)}"></div>{picture('big', 86, 92, 378, 484, t[1] * .5, tape=False)}
{quote(78, 628, 250, 130, 'light')}
{hero(590, 64, 466, 300, 60)}{tinted(motif, 978, 362, 64, 64, t[2] * 2)}
{pair(584, 432)}
{tweet(0, 'strip', 590, 700, 262, 96, t[4] * .7)}{tweet(1, 'ticket', 884, 676, 176, 132, t[5])}
{picture('small', 356, 612, 150, 170, t[3], tape=False, on_ink='tint')}
{credit('dark')}'''
    else:                         # banner: headline across both pages, broken around the fold, over an ink band
                                  # (the big picture stops short of the quote so its lower tape strip clears it)
        body = f'''<div class="band"></div><div class="dots" style="left:900px;top:120px"></div><div class="dots light" style="left:852px;top:300px;width:150px;height:150px"></div>
{brand()}{split_hero(66, 184, 84)}
{tweet(0, 'onink', 44, 326, 360, 180)}{tinted(motif, 414, 360, 104, 104, t[2] * 2, color='#fbf8f1')}
{picture('big', 612, 262, 250, 316, t[1], on_ink='card')}{picture('small', 892, 330, 172, 220, t[3], tape=False, on_ink='card')}
{pair(44, 556)}
{quote(584, 618, 262, 104)}{tweet(1, 'strip', 584, 734, 262, 74, t[4] * .7)}
{tweet(2, 'ticket', 882, 598, 178, 176, t[5])}
{credit('dark')}'''

    page = f'''<!doctype html><html><head><meta charset="utf-8"><style>
{faces}
@page{{size:{PAGE_W}px {PAGE_H}px;margin:0}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{PAGE_W}px;height:{PAGE_H}px;overflow:hidden}}
body{{background:var(--paper);color:var(--dark);font-family:var(--body),serif;position:relative;
  background-image:radial-gradient(rgba(0,0,0,.035) 1px,transparent 1.2px);background-size:5px 5px}}
.fold{{position:absolute;left:{PAGE_W // 2 - 45}px;top:0;width:90px;height:100%;background:linear-gradient(to right,
  rgba(0,0,0,0),rgba(0,0,0,.025) 30%,rgba(0,0,0,.09) 49.6%,rgba(0,0,0,.16) 50%,rgba(0,0,0,.09) 50.4%,rgba(0,0,0,.025) 70%,rgba(0,0,0,0))}}
.wash{{position:absolute;left:0;top:0;width:550px;height:850px;background:var(--ink);opacity:.10}}
.block{{position:absolute;left:550px;top:560px;width:550px;height:290px;background:var(--ink)}}
.dots{{position:absolute;width:260px;height:260px;border-radius:50%;
  background-image:radial-gradient(var(--ink) 34%,transparent 36%);background-size:11px 11px;opacity:.55}}
.dots.light{{background-image:radial-gradient(#fbf8f1 34%,transparent 36%);opacity:.35}}
.tint{{position:absolute;object-fit:contain}}
.shadow{{position:absolute}}
.photo{{position:absolute}}
.photo.framed{{border:9px solid #fbf8f1}}
.photo img{{display:block;width:100%;height:100%;object-fit:cover}}
.photo.card{{background:#fbf8f1;padding:6px}} .photo.card img{{object-fit:contain}}
.cutout{{position:absolute}} .cutout .art{{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:contain}}
.tape{{position:absolute;width:92px;height:26px;background:var(--ink);opacity:.55}}
.fit{{width:100%;height:100%;overflow:hidden}}
.headrule{{position:absolute;background:var(--ink)}}
.hero{{position:absolute;font-family:var(--head),serif;
  font-weight:{ty['head_weight']};font-style:{ty['head_style']};line-height:1.04;letter-spacing:-.01em}}
.tweet{{position:absolute}}
.tweet .meta{{position:absolute;right:8px;bottom:4px;font:500 9px var(--body);opacity:.6;letter-spacing:.04em}}
.strip{{background:var(--dark);color:#f6f1e7;padding:9px 14px 18px;font:700 17px/1.25 'Space Mono',monospace}}
.ticket{{background:#fbf8f1;border:1.5px dashed var(--ink);padding:14px 16px 20px;font:500 19px/1.25 var(--body)}}
.plain{{font:italic 500 23px/1.2 var(--body);padding:0 0 16px}}
.onink{{color:#fbf8f1;font-family:'{sub[0]}',serif;font-weight:{sub[1]};font-size:34px;line-height:1.08;padding-bottom:16px}}
.onink .meta{{opacity:.8}}
.pair{{position:absolute;width:480px;height:236px}}
.spare{{left:0;top:0;width:440px;height:200px;font:500 30px/1.2 var(--body);padding-bottom:18px}}
.source{{left:0;top:0;width:230px;height:150px;font:500 21px/1.22 var(--body);padding-bottom:16px}}
mark{{background:var(--ink);color:#fbf8f1;padding:0 .18em;border-radius:2px}}
.defcard{{position:absolute;right:0;top:18px;width:236px;height:214px;background:#fbf8f1;border:2px solid var(--dark);
  padding:30px 16px 26px;font:400 16px/1.28 'EB Garamond',serif;transform:rotate(1.6deg)}}
.defshadow{{position:absolute;right:-5px;top:23px;width:236px;height:214px;background:var(--ink);transform:rotate(1.6deg)}}
.defcard .label{{position:absolute;left:0;top:0;right:0;background:var(--dark);color:#fbf8f1;
  font:700 9px 'Space Grotesk',sans-serif;letter-spacing:.2em;text-transform:uppercase;padding:6px 16px}}
.by{{position:absolute;left:16px;bottom:8px;font:italic 400 10px 'EB Garamond',serif;opacity:.7}}
.quote{{position:absolute;font:italic 400 19px/1.22 'EB Garamond',serif;
  border-left:3px solid var(--ink);padding:2px 0 26px 14px}}
.quote .by{{left:14px;right:0;bottom:0;font-size:{CREDIT_PX}px;line-height:{CREDIT_LEADING};opacity:.8}}
.credit{{position:absolute;left:584px;right:36px;bottom:14px;font:400 7px/1.3 'Space Grotesk',sans-serif;color:#fbf8f1;opacity:.85}}
.brand{{position:absolute;left:44px;top:30px;font:700 10px 'Space Grotesk',sans-serif;letter-spacing:.24em;text-transform:uppercase}}
.brand span{{color:var(--ink)}}
.brand .tok{{font-size:17px;letter-spacing:0;margin-right:9px;vertical-align:-3px}}
.inkpage{{position:absolute;left:0;top:0;width:550px;height:850px;background:var(--ink)}}
.panel{{position:absolute;background:var(--paper)}}
.band{{position:absolute;left:0;top:300px;width:1100px;height:232px;background:var(--ink)}}
.brand.light,.brand.light span{{color:#fbf8f1}}
.quote.light{{color:#fbf8f1;border-left-color:#fbf8f1}}
.credit.dark{{color:var(--dark);opacity:.6}}
</style></head><body>
{body}
<div class="fold"></div>
</body></html>'''
    for name, value in dict(ink=ink, paper='#f2eadb', dark='#1d1a17', head=f"'{ty['head']}'", body=f"'{ty['body']}'").items():
        page = page.replace(f'var(--{name})', value)
    return page


def share_images(png_path, out_dir, c):
    spread = Image.open(png_path).convert('RGB')
    spread.resize((2048, 1583), Image.LANCZOS).save(os.path.join(out_dir, 'share-2048.jpg'), quality=88)
    # Link preview: spread on an ink backdrop, handle and site name in the side space.
    card = Image.new('RGB', (1200, 630), INKS[c['ink']])
    small = spread.resize((764, 590), Image.LANCZOS)
    card.paste((30, 26, 22), (406 + 8, 20 + 8, 406 + 8 + 764, 20 + 8 + 590))
    card.paste(small, (406, 20))
    d = ImageDraw.Draw(card)
    fonts = os.path.join(APP, 'fonts')
    big = ImageFont.truetype(os.path.join(fonts, 'Rye-Regular.ttf'), 46)
    name = '@' + c['handle']
    while d.textlength(name, font=big) > 350 and big.size > 20:
        big = ImageFont.truetype(os.path.join(fonts, 'Rye-Regular.ttf'), big.size - 2)
    d.text((34, 230), name, font=big, fill='#fbf8f1')
    d.text((34, 230 + big.size + 14), 'as a zine', font=ImageFont.truetype(os.path.join(fonts, 'Rye-Regular.ttf'), 34),
           fill='#fbf8f1')
    d.text((34, 574), 'AUTOZINE', font=ImageFont.truetype(os.path.join(fonts, 'SpaceMono-Bold.ttf'), 18), fill='#fbf8f1')
    card.save(os.path.join(out_dir, 'share-1200x630.jpg'), quality=90)


class NotEnoughTweets(Exception):
    """Too few usable tweets to fill a spread."""
    def __init__(self, usable, needed=None):
        self.usable, self.needed = usable, needed or MIN_SPREAD
        super().__init__(f'only {usable} usable tweets; this needs {self.needed}')


def seed_for(handle, answers):
    """The spread's seed: the same handle (any case) and answers always give the same spread."""
    return int(hashlib.sha256(json.dumps([handle.lower(), answers], sort_keys=True).encode()).hexdigest()[:12], 16)


def get_tweets(handle, max_age=None, before_page=None):
    """Fetch (or reuse) enough of the handle's tweets; returns the cached JSON's path.
    `before_page` is passed to fetch.fetch (the web app's spending cap)."""
    import fetch
    pages = 3
    while True:       # posts that need context are dropped, so some accounts need more: one page at a time
        path = fetch.fetch(handle, pages, max_age=max_age, before_page=before_page)
        data = json.load(open(path, encoding='utf8'))
        if len(pipeline.process(data['tweets'])[0]) >= MIN_USABLE or data.get('done') or pages >= MAX_PAGES:
            return path
        pages += 1


RECENT_ENOUGH = 16          # usable posts within a window needed to keep to that window


def posted(t):
    return datetime.datetime.strptime(t['createdAt'], '%a %b %d %H:%M:%S %z %Y')


def prefer_recent(tweets, dropped):
    """Keep to the past year when it has enough usable posts, else the past two, else everything. Rarely
    active accounts reach back years, and old posts have had longer to collect likes, so without this they
    crowd out recent ones (Nov 23). Measured from the newest post, so re-rendering gives the same spread."""
    dated = [t for t in tweets if t.get('createdAt')]
    if not dated:
        return tweets, dropped
    newest = max(posted(t) for t in dated)
    for days in (365, 730):
        keep = [t for t in tweets if t.get('createdAt') and (newest - posted(t)).days < days]
        if len(keep) >= RECENT_ENOUGH:
            return keep, dropped + [(t, f'older than {days // 365} year{"s" if days > 365 else ""}') for t in tweets if t not in keep]
    return tweets, dropped


def make(tweets_path, theme=None, ink=None, type=None, layout=None, reroll=0, use_jev=True,
         progress=lambda stage, **info: None, after=None, uses=None, token=None):
    """Make the spread from a tweets file. Returns (output dir, record). `progress` is called with
    'reading', 'composing' and 'rendering' as each stage starts; 'rendering' also passes
    picture=<the main picture's manifest row>, which the web app's progress page shows. Raises NotEnoughTweets.

    With `after` (the first spread's record), makes that spread's B-side: same theme, ink and type,
    another layout, and none of the first spread's tweets, pictures or quotation.

    `uses` ({picture id: recent uses}, from the web app) nudges picture choice away from much-used
    pictures. The counts are saved in the record, so the spread can be made again exactly.

    `token` is the quiz token picked (a key of TOKEN_EMOJI); its emoji leads the header. It doesn't
    change the seed. Without one, one of the theme's two tokens is chosen by the seed."""
    raw = json.load(open(tweets_path, encoding='utf8'))['tweets']
    handle = raw[0]['author']['userName']
    answers = dict(theme=theme, ink=ink, type=type, layout=layout, reroll=reroll)
    seed = seed_for(handle, dict(answers, side='b') if after else answers)
    rng = random.Random(seed)
    jev.usage.update(input_tokens=0, requests=0)
    progress('reading')

    tweets, dropped = pipeline.process(raw)
    if len(tweets) < MIN_ORIGINAL:        # thin account: reconsider quote and image tweets
        tweets, dropped = pipeline.process(raw, allow_context=True)
    tweets, dropped = prefer_recent(tweets, dropped)
    tweets = tweets[:MAX_TAGGED]          # ranked best first; bounds the tagging cost
    use_jev = jev.available() and use_jev and bool(tweets)
    if use_jev:
        tags, jev_theme, jev_type = jev.tag_tweets(tweets)
        for t in tweets:
            t['labels'].update(jev={k: round(v, 3) for k, v in tags[t['id']].items()})
            t['labels']['high_emotion'] = (t['labels']['high_emotion'] or tags[t['id']]['high_emotion'] >= RAW_AT
                                           or tags[t['id']]['attack'] >= ATTACK_AT)
        def stands_alone(t):          # quote and image tweets must clear a higher bar
            bar = {'quote tweet': ALONE_AT_QUOTE, 'has an image or video': ALONE_AT_IMAGE}.get(t['labels'].get('context'), ALONE_AT)
            return tags[t['id']]['standalone'] >= bar
        dropped += [(t, 'needs context (Jev)') for t in tweets if not stands_alone(t)]
        tweets = [t for t in tweets if stands_alone(t)]
    else:
        dropped += [(t, t['labels']['context']) for t in tweets if t['labels'].get('context')]
        tweets = [t for t in tweets if not t['labels'].get('context')]
    if len(tweets) < (MIN_B_SIDE if after else MIN_SPREAD):
        raise NotEnoughTweets(len(tweets), MIN_B_SIDE if after else MIN_SPREAD)
    if after:
        theme, ink, type_name = (after['resolved'][k] for k in ('theme', 'ink', 'type'))
    else:
        theme = answers['theme'] or (jev_theme if use_jev else match.guess_theme(tweets))
        ink = answers['ink'] or rng.choice(sorted(INKS))
        type_name = answers['type'] or (jev_type if use_jev else rng.choice(sorted(TYPES)))
    if after:
        token = after['resolved'].get('token')
    elif TOKEN_THEME.get(token) != theme:
        options = TOKENS.get(theme) or TOKENS['nature']
        token = options[seed % len(options)][0]
    progress('composing')

    c = compose(handle, tweets, theme, ink, type_name, rng, use_jev, after=after, uses=uses)
    c['layout'] = (rng.choice(sorted(set('abc') - {after['resolved']['layout']})) if after else
                   layout or rng.choice('abc'))
    c['token'] = TOKEN_EMOJI.get(token)
    # The token doesn't change the seed, so it names the folder too: acorn and moth spreads don't collide.
    out_dir = os.path.join(ROOT, 'build', 'out', f'{handle}-{seed:x}' + (f'-{token.replace(" ", "-")}' if token else ''))
    os.makedirs(out_dir, exist_ok=True)
    progress('rendering', picture=c['big'])
    page, overflow = render.fit_text(render.split_headlines(render_html(c)))
    with open(os.path.join(out_dir, 'spread.html'), 'w', encoding='utf8') as fh:
        fh.write(page)
    png = os.path.join(out_dir, 'spread.png')
    render.render(page, os.path.join(out_dir, 'spread.pdf'), png)
    share_images(png, out_dir, c)

    record = dict(handle=handle, seed=seed, side='b' if after else 'a', answers=answers, resolved=dict(theme=theme, ink=ink, type=type_name, layout=c['layout'], token=token),
                  labels={t['id']: t['labels'] for t in tweets},
                  dropped={t['id']: why for t, why in dropped},
                  tweets_fetched=len(raw),
                  tweets_used=[c['hero']['id']] + [t['id'] for t in c['rest']] + ([c['card']['tweet']['id']] if c['card'] else []),
                  definition=c['card'] and dict(word=c['card']['word'], snippet=c['card']['entry']['id'], score=c['card']['score'],
                                                source=c['card']['entry']['source']),
                  jev=use_jev and dict(model=jev.MODEL, **jev.usage),
                  quote=c['quote'] and c['quote']['id'],
                  images=[c['big']['id'], c['small']['id'], c['border']['id'], c['motif']['id']],
                  overflow=overflow, uses={k: v for k, v in (uses or {}).items() if v})
    json.dump(record, open(os.path.join(out_dir, 'record.json'), 'w'), indent=1)
    return out_dir, record

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tweets', help='JSON file shaped like the tweet API response')
    ap.add_argument('--handle', help='fetch this handle from TwitterAPI.io (cached)')
    ap.add_argument('--layout', choices=['a', 'b', 'c'])
    ap.add_argument('--theme', choices=sorted(match.THEME_WORDS), help='omit for "surprise me"')
    ap.add_argument('--ink', choices=sorted(INKS))
    ap.add_argument('--type', choices=sorted(TYPES))
    ap.add_argument('--reroll', type=int, default=0)
    ap.add_argument('--no-jev', action='store_true', help='use keyword rules only')
    args = ap.parse_args()
    if args.handle:
        args.tweets = get_tweets(args.handle)
    try:
        out_dir, record = make(args.tweets, args.theme, args.ink, args.type, args.layout, args.reroll,
                               use_jev=not args.no_jev)
    except NotEnoughTweets as e:
        raise SystemExit(f'Not enough to work with: {e}')
    r = record['resolved']
    print(f"{len(record['labels'])} usable tweets, {len(record['dropped'])} dropped; "
          f"theme={r['theme']} ink={r['ink']} type={r['type']} layout={r['layout']}")
    if record['overflow']:
        print(f'warning: text still overflows at {render.MIN_FONT:g}px in {", ".join(record["overflow"])}')
    print(out_dir)


if __name__ == '__main__':
    main()
