"""Turn the spread's HTML into a PDF and PNG with WeasyPrint.

WeasyPrint runs no JavaScript and has no masks, blend modes or shadows, so the
things the browser used to do are done here instead:

- fit_text() shrinks each `.fit` element's font until its text fits its slot,
  by laying the page out repeatedly and measuring.
- tinted() recolors an ornament's alpha channel with the ink.
- shadow() bakes a soft shadow (of a box, or of a cut-out's outline) into a PNG
  that sits behind the element.

All generated images are cached in build/cache/.
"""
import hashlib
import io
import os
import re

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'build', 'cache')
RES = 2                  # shadow and tint pixels per CSS px; matches the PNG
PNG_WIDTH = 2200         # 2200 x 1700; the 2048-wide share image needs no more
MIN_FONT = 6.0
SPLIT_LEADING = 1.24     # line spacing of a headline split across the fold, with room for its underline
STEP = 0.5

# A blank image stands in for every picture while measuring text: pictures don't affect where text breaks.
_BLANK = io.BytesIO()
Image.new('RGBA', (1, 1)).save(_BLANK, 'PNG')
_BLANK = _BLANK.getvalue()
_MEASURE_CSS = '.fit{height:auto !important;overflow:visible !important}'


def _cache_path(*parts, ext='.png'):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, hashlib.sha1(repr(parts).encode()).hexdigest()[:16] + ext)


def tinted(path, color):
    """The image at `path` as a solid `color` silhouette, keeping its alpha. Returns the PNG path."""
    out = _cache_path('tint', path, color)
    if not os.path.exists(out):
        src = Image.open(path).convert('RGBA')
        im = Image.new('RGBA', src.size, color)
        im.putalpha(src.getchannel('A'))
        im.save(out)
    return out


def shadow(w, h, blur, alpha, mask_path=None):
    """A soft black shadow for a w x h box, or for the opaque part of the image at mask_path
    drawn at w x h. Returns (png_path, pad): the PNG covers the box plus `pad` CSS px on every
    side. `blur` is the CSS blur radius."""
    w, h = round(w), round(h)
    pad = int(blur) + 2
    out = _cache_path('shadow', w, h, blur, alpha, mask_path)
    if not os.path.exists(out):
        size = ((w + 2 * pad) * RES, (h + 2 * pad) * RES)
        m = Image.new('L', size, 0)
        if mask_path:
            src = Image.open(mask_path).convert('RGBA').getchannel('A')
            m.paste(src.resize((w * RES, h * RES), Image.LANCZOS), (pad * RES, pad * RES))
        else:
            ImageDraw.Draw(m).rectangle((pad * RES, pad * RES, (pad + w) * RES - 1, (pad + h) * RES - 1), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(blur * RES / 2)).point(lambda v: round(v * alpha))
        im = Image.new('RGBA', size, (0, 0, 0, 0))
        im.putalpha(m)
        im.save(out)
    return out, pad


def _url_fetcher(measuring):
    from weasyprint.urls import URLFetcher, URLFetcherResponse

    class Fetcher(URLFetcher):
        def fetch(self, url, headers=None):
            if measuring and re.search(r'\.(png|jpe?g)$', url, re.I):
                return URLFetcherResponse(url, _BLANK, {'Content-Type': 'image/png'})
            return super().fetch(url, headers)
    return Fetcher(allowed_protocols={'file'}, fail_on_errors=True)


_FACE_RE = re.compile(r'@font-face\{[^}]*\}')
_FONTS = {}              # @font-face rules -> (FontConfiguration, CSS); fonts load once per process
_SIZES = {}              # fitted sizes already worked out in this process, for rerolls


def _fonts(page):
    from weasyprint import CSS
    from weasyprint.text.fonts import FontConfiguration
    faces = ''.join(_FACE_RE.findall(page))
    if faces not in _FONTS:
        config = FontConfiguration()
        _FONTS[faces] = config, CSS(string=faces, base_url=ROOT, font_config=config)
    return _FONTS[faces]


def _layout(page, measuring):
    from weasyprint import CSS, HTML
    config, faces = _fonts(page)
    sheets = [faces] + ([CSS(string=_MEASURE_CSS)] if measuring else [])
    return HTML(string=_FACE_RE.sub('', page), base_url=ROOT, url_fetcher=_url_fetcher(measuring)).render(
        stylesheets=sheets, font_config=config)


def _fit_boxes(doc, attr='id'):
    """Yield (key, box, slot box) for every `.fit` element; key is its `attr` attribute."""
    def walk(box, parent):
        box = getattr(box, '_box', box)        # unwrap absolutely positioned boxes
        el = getattr(box, 'element', None)
        if el is not None and 'fit' in (el.get('class') or '').split() and box.element_tag == 'div' \
                and el is not getattr(parent, 'element', None):
            yield el.get(attr), box, parent
        for child in getattr(box, 'children', ()):
            yield from walk(child, box)
    for page in doc.pages:
        yield from walk(page._page_box, None)


def _probe(page, slots, tries):
    """Lay out each slot's text at each size in `tries` ({id: [sizes]}) in one small document holding only
    those texts, at the slot's width and in its classes. Returns {(id, size): fits}."""
    style = re.search(r'<style>(.*?)</style>', page, re.S).group(1)
    parts = []
    for fid, sizes in tries.items():
        cls, width, _, inner, parent_size = slots[fid]
        for size in sizes:
            # The slot's own font size matters even though the text is resized: em-based letter-spacing
            # is resolved against it and inherited.
            parts.append(f'<div class="{cls}" style="position:absolute;left:0;top:0;width:{width}px;height:auto;'
                         f'font-size:{parent_size}px;padding:0;border:0;margin:0;transform:none">'
                         f'<div class="fit" data-k="{fid} {size}" '
                         f'style="font-size:{size}px">{inner}</div></div>')
    doc = _layout(f'<!doctype html><html><head><meta charset="utf-8"><style>{style}'
                  f'@page{{size:3000px 30000px}}</style></head><body>{"".join(parts)}</body></html>', measuring=True)
    out = {}
    for key, box, _ in _fit_boxes(doc, 'data-k'):
        fid, size = key.split()
        out[fid, float(size)] = box.height <= slots[fid][2] + 1
    return out


def _with_sizes(page, sizes):
    rules = ''.join(f'#{k}{{font-size:{v:g}px}}' for k, v in sizes.items())
    return page.replace('</style>', rules + '</style>', 1)


def fit_text(page):
    """Shrink each `.fit` element's font (in 0.5 px steps, down to 6 px) to the largest size at which
    its text fits its slot, as the browser prototype did. Returns (page, ids still overflowing).

    One layout of the whole page finds each slot's width and height and which texts overflow. Candidate
    sizes are then tried in two small documents: every 2 px, then every 0.5 px below the first that failed."""
    n = 0

    def number(_):
        nonlocal n
        n += 1
        return f'class="fit" id="fit{n}"'
    page = re.sub(r'class="fit"', number, page)
    style = re.search(r'<style>(.*?)</style>', page, re.S).group(1)

    slots, base = {}, {}
    for fid, box, parent in _fit_boxes(_layout(page, measuring=True)):
        if box.height > parent.height + 1:
            inner = re.search(rf'<div class="fit" id="{fid}">(.*?)</div>', page, re.S).group(1)
            slots[fid] = (parent.element.get('class'), box.width, parent.height, inner, parent.style['font_size'])
            base[fid] = box.style['font_size']

    sizes, over, todo = {}, [], {}
    for fid in slots:
        key = (style, base[fid]) + slots[fid]
        if key in _SIZES:
            sizes[fid] = _SIZES[key]
        else:
            todo[fid] = key
    coarse = {fid: [b for b in (base[fid] - 2 * k for k in range(1, 200)) if b > MIN_FONT] + [MIN_FONT]
              for fid in todo}
    fits = _probe(page, slots, coarse) if todo else {}
    best, fine = {}, {}
    for fid in todo:
        ok = [sz for sz in coarse[fid] if fits[fid, sz]]
        best[fid] = max(ok) if ok else None
        if ok:
            above = min([sz for sz in coarse[fid] if sz > best[fid]] + [base[fid]])
            fine[fid] = [best[fid] + STEP * j for j in range(1, 8) if best[fid] + STEP * j < above]
    fits = _probe(page, slots, {k: v for k, v in fine.items() if v}) if any(fine.values()) else {}
    for fid, key in todo.items():
        if best[fid] is None:
            sizes[fid] = MIN_FONT
            over.append(fid)
            continue
        sizes[fid] = max([best[fid]] + [sz for sz in fine[fid] if fits[fid, sz]])
        _SIZES[key] = sizes[fid]
    return _with_sizes(page, sizes), over


_SPLIT = re.compile(r'<div class="hero split" data-left="(\d+)" data-right="(\d+)" data-width="(\d+)" '
                    r'data-top="(\d+)" data-height="(\d+)" data-size="([\d.]+)">(.*?)</div>', re.S)


def split_headlines(page):
    """Set each `.hero.split` headline across both pages with the fold between words: every line starts in
    the left page's column and carries on in the right page's, so no word sits on the fold. Words are
    measured once in the headline's font, then filled in reading order (left, right, next line) at the
    largest size, in 0.5 px steps, at which they fit the box. Returns the page with each headline as lines."""
    m = _SPLIT.search(page)
    if not m:
        return page
    left, right, width, top, height = (int(v) for v in m.groups()[:5])
    base = float(m[6])
    words = m[7].replace('<br>', ' ').split()
    style = re.search(r'<style>(.*?)</style>', page, re.S).group(1)
    probe = ''.join(f'<div class="hero" data-k="{i}" style="left:0;top:0;width:auto;white-space:nowrap;'
                    f'font-size:{base}px">{w}</div>' for i, w in enumerate(words + ['x x', 'xx']))
    doc = _layout(f'<!doctype html><html><head><meta charset="utf-8"><style>{style}</style></head>'
                  f'<body>{probe}</body></html>', measuring=True)
    widths = {}

    def walk(box):
        box = getattr(box, '_box', box)
        el = getattr(box, 'element', None)
        if el is not None and el.get('data-k') is not None and box.element_tag == 'div':
            widths.setdefault(int(el.get('data-k')), box.width)
        for child in getattr(box, 'children', ()):
            walk(child)
    walk(doc.pages[0]._page_box)
    n = len(words)
    space = widths[n] - widths[n + 1]

    def fill(size):
        """The cells (left, right, left, ...) as (words, text width) at this size, or None if they don't fit."""
        k, room = size / base, width - 2          # a little slack for rounding
        if any(widths[i] * k > room for i in range(n)):
            return None
        cells, cell, used = [], [], 0.0
        for i, w in enumerate(words):
            add = widths[i] * k + (space * k if cell else 0)
            if cell and used + add > room:
                cells.append((cell, used))
                cell, used = [], 0.0
                add = widths[i] * k
            cell.append(w)
            used += add
        cells.append((cell, used))
        rows = (len(cells) + 1) // 2
        return cells if rows * size * SPLIT_LEADING <= height + 1 else None

    size = base
    while size > MIN_FONT and fill(size) is None:
        size -= STEP
    cells = fill(size) or fill(MIN_FONT) or [(words, width)]
    lh = size * SPLIT_LEADING
    rule = max(2.0, size * .05)
    lines = []
    for i, (cell, used) in enumerate(cells):
        y = top + (i // 2) * lh
        # Both halves sit flush against the fold, so each line reads across it as one line.
        lines.append(f'<div class="hero" style="left:{left if i % 2 == 0 else right}px;top:{y:.1f}px;'
                     f'width:{width}px;height:{lh:.1f}px;font-size:{size:g}px;line-height:{size * 1.04:.1f}px;'
                     f'white-space:nowrap;text-align:{"right" if i % 2 == 0 else "left"}">{" ".join(cell)}</div>')
        if i % 2 == 0:            # an underline from the start of the left half to the end of the right, across the fold
            x0 = left + width - used
            x1 = right + cells[i + 1][1] if i + 1 < len(cells) else left + width
            lines.append(f'<div class="headrule" style="left:{x0:.1f}px;top:{y + size * 1.1:.1f}px;'
                         f'width:{x1 - x0:.1f}px;height:{rule:.1f}px"></div>')
    lines = ''.join(lines)
    return split_headlines(page[:m.start()] + lines + page[m.end():])


def render(page, pdf_path, png_path):
    """Write the PDF, then rasterize it to a PNG_WIDTH-wide PNG."""
    import pypdfium2 as pdfium
    _layout(page, measuring=False).write_pdf(pdf_path)
    pdf = pdfium.PdfDocument(pdf_path)
    first = pdf[0]
    first.render(scale=PNG_WIDTH / first.get_width()).to_pil().convert('RGB').save(png_path)
    pdf.close()
