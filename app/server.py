"""The Autozine web app: handle entry and quiz, a progress page, and a share page per spread.

    .venv/bin/python app/server.py              # http://127.0.0.1:3004/autozine/

Spreads are made one at a time by a background thread, so a render's memory is
never doubled. Each spread gets a random, unlisted id; its files live in the
spread's build/out/ folder and a row in build/autozine.db points to them. The same
handle and answers always give the same spread, so a repeat request reuses it.

After a spread is made, an account with enough tweets can have a B-side: a second
spread with new tweets and pictures and a dream-book card (see make_spread.make).
Its row points to the first spread's through `parent`.

Guardrails, since every new handle buys a tweet fetch: each visitor (by hashed IP) may
start AUTOZINE_PER_HOUR spreads an hour and AUTOZINE_PER_DAY a day; no new spreads are
taken while MAX_QUEUE are waiting; and tweet fetches stop for the day (UTC) once they
reach AUTOZINE_DAILY_BUDGET dollars. Accounts fetched in the last week still work then.

Settings (environment or .env): TWITTERAPI_KEY, TYPESAFE_API_KEY,
AUTOZINE_BASE (URL path prefix, default /autozine), PORT (default 3004),
AUTOZINE_PER_HOUR (default 5), AUTOZINE_PER_DAY (default 20), AUTOZINE_DAILY_BUDGET (default 30),
AUTOZINE_EXEMPT_KEY (opening /exempt?key=<it> marks that browser as exempt from the per-visitor limits).
"""
import contextlib
import hashlib
import json
import logging
import os
import queue
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch            # noqa: E402
import make_spread      # noqa: E402

APP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP)
DB_PATH = os.path.join(ROOT, 'build', 'autozine.db')
BASE = (fetch.env('AUTOZINE_BASE') or '/autozine').rstrip('/')
HANDLE = re.compile(r'^@?([A-Za-z0-9_]{1,15})$')
PROFILE = re.compile(r'^(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/@?([A-Za-z0-9_]{1,15})(?:[/?#].*)?$', re.I)
X_PAGES = {'home', 'explore', 'search', 'i', 'settings', 'messages', 'notifications', 'compose', 'intent', 'login', 'share'}
INVISIBLE = re.compile('[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\u00a0]')


def clean_handle(raw):
    """The handle in what a visitor typed or pasted, or None. Forgives spaces, invisible characters
    (copied text often carries a left-to-right mark), a leading @, and a pasted profile link."""
    text = re.sub(r'^@\s+', '@', INVISIBLE.sub('', raw or '').strip())
    m = PROFILE.match(text) or HANDLE.match(text)
    return m[1] if m and m[1].lower() not in X_PAGES else None
FILES = {'spread.png': 'image/png', 'spread.pdf': 'application/pdf',
         'share-2048.jpg': 'image/jpeg', 'share-1200x630.jpg': 'image/jpeg'}
STAGES = {'fetching': 'Fetching posts', 'waiting': 'Waiting for a free press',
          'reading': 'Reading them closely', 'composing': 'Choosing pictures and words',
          'rendering': 'Setting type and pasting it up'}
SHOWN_AS = {'queued': 'fetching'}          # a spread waiting for a fetcher shows as fetching
log = logging.getLogger('autozine')
PER_HOUR = int(fetch.env('AUTOZINE_PER_HOUR') or 5)
PER_DAY = int(fetch.env('AUTOZINE_PER_DAY') or 20)
DAILY_BUDGET = float(fetch.env('AUTOZINE_DAILY_BUDGET') or 30)
MAX_QUEUE = 20
PREFETCH_PER_HOUR = int(fetch.env('AUTOZINE_PREFETCH_PER_HOUR') or 10)

TOKENS = make_spread.TOKENS        # quiz tokens, two per theme; the one picked also heads the spread


# --- storage --------------------------------------------------------------------------------------

@contextlib.contextmanager
def db():
    """A connection that commits on success and is always closed."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute('''create table if not exists spreads (
            id text primary key, handle text not null, key text not null, answers text not null,
            status text not null, error text, out_dir text, created real not null, updated real not null)''')
        con.execute('create index if not exists spreads_key on spreads (key)')
        con.execute('create table if not exists picture_uses (image text not null, at real not null)')
        con.execute('create index if not exists picture_uses_at on picture_uses (at)')
        columns = {r['name'] for r in con.execute('pragma table_info(spreads)')}
        if 'parent' not in columns:
            con.execute('alter table spreads add column parent text')      # the first spread, for a B-side
        if 'visitor' not in columns:
            con.execute('alter table spreads add column visitor text')     # hashed IP, for the rate limit
        if 'picture' not in columns:
            con.execute('alter table spreads add column picture text')     # the main picture, once chosen
        con.execute('create table if not exists fetch_pages (at real not null)')   # each billed page
        con.execute('create table if not exists prefetches (visitor text not null, at real not null)')
        with con:
            yield con
    finally:
        con.close()


def get(spread_id):
    with db() as con:
        return con.execute('select * from spreads where id = ?', (spread_id,)).fetchone()


def record_of(row):
    with open(os.path.join(row['out_dir'], 'record.json'), encoding='utf8') as fh:
        return json.load(fh)


def b_side_of(spread_id):
    with db() as con:
        return con.execute("select * from spreads where parent = ? and status != 'failed'", (spread_id,)).fetchone()


USE_WINDOW = 30 * 24 * 3600       # a picture's recent uses, which steer later spreads away from it


def recent_uses():
    with db() as con:
        return dict(con.execute('select image, count(*) from picture_uses where at > ? group by image',
                                (time.time() - USE_WINDOW,)).fetchall())


def note_uses(record):
    now = time.time()
    with db() as con:          # the two pictures; ornaments and borders are decoration, so they don't count
        con.executemany('insert into picture_uses (image, at) values (?, ?)', [(i, now) for i in record['images'][:2]])


def update(spread_id, **fields):
    fields['updated'] = time.time()
    with db() as con:
        con.execute(f"update spreads set {', '.join(f'{k} = ?' for k in fields)} where id = ?",
                    (*fields.values(), spread_id))


# --- guardrails -----------------------------------------------------------------------------------

def _salt():
    path = os.path.join(ROOT, 'build', 'visitor-salt')
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fh:
            fh.write(secrets.token_hex(16))
    return open(path).read().strip()


SALT = _salt()


def visitor(request):
    """A salted hash of the visitor's IP (uvicorn takes it from Caddy's X-Forwarded-For); never the IP itself."""
    ip = request.client.host if request.client else ''
    return hashlib.sha256((SALT + ip).encode()).hexdigest()[:16]


def too_many(con, who):
    """Why this visitor can't start a spread right now, or None."""
    now = time.time()
    count = lambda since: con.execute('select count(*) from spreads where visitor = ? and created > ?',  # noqa: E731
                                      (who, now - since)).fetchone()[0]
    if count(3600) >= PER_HOUR:
        return f"You've made {PER_HOUR} zines in the last hour, which is as many as we can press for one person. Please come back in an hour."
    if count(86400) >= PER_DAY:
        return f"You've made {PER_DAY} zines today, which is as many as we can press for one person. Please come back tomorrow."
    if con.execute("select count(*) from spreads where status in ('queued', 'fetching', 'waiting')").fetchone()[0] >= MAX_QUEUE:
        return 'The press is very busy right now. Please try again in a few minutes.'
    return None


def spent_today():
    with db() as con:
        return con.execute('select count(*) from fetch_pages where at > ?',
                           (time.time() // 86400 * 86400,)).fetchone()[0] * fetch.PAGE_COST


def before_page():
    """Stop fetching once today's budget is spent; otherwise note the page about to be billed."""
    if spent_today() + fetch.PAGE_COST > DAILY_BUDGET:
        log.warning('daily fetch budget of $%.2f reached', DAILY_BUDGET)
        raise fetch.FetchError("Autozine has fetched all the posts it can for today. Please come back tomorrow; "
                               "accounts someone made a zine of in the last week still work now.")
    with db() as con:
        con.execute('insert into fetch_pages (at) values (?)', (time.time(),))


EXEMPT_KEY = fetch.env('AUTOZINE_EXEMPT_KEY')
EXEMPT_COOKIE = 'autozine_exempt'


def _exempt_token():
    return hashlib.sha256(f'exempt:{EXEMPT_KEY}'.encode()).hexdigest()


def exempt(request):
    """True for a browser that opened the exempt link: it skips the per-visitor limits (not the budget)."""
    token = request.cookies.get(EXEMPT_COOKIE)
    return bool(EXEMPT_KEY and token and secrets.compare_digest(token, _exempt_token()))


async def make_exempt(request):
    if not (EXEMPT_KEY and secrets.compare_digest(request.query_params.get('key', ''), EXEMPT_KEY)):
        return Response('Not found', status_code=404)
    r = RedirectResponse(f'{BASE}/', status_code=303)
    r.set_cookie(EXEMPT_COOKIE, _exempt_token(), max_age=400 * 86400, path=BASE or '/', httponly=True,
                 secure=request.url.scheme == 'https', samesite='lax')
    return r


def limited(request, reason):
    return page('limited.html', request, status=429, headers=NOINDEX, reason=reason)


# --- the press: one spread at a time --------------------------------------------------------------

# Fetching is network-bound and rendering is CPU- and memory-bound, so they run separately: two
# fetchers fetch posts (a visitor's spread first, then prefetches started from the quiz), and one
# press makes spreads, so the next spread's fetch overlaps the current render.
fetch_jobs = queue.PriorityQueue()       # (priority, order, kind, spread id or handle)
press_jobs = queue.Queue()               # spread ids whose posts are fetched
FETCHERS = 2
_order = iter(range(10 ** 12)).__next__
_locks, _locks_guard = {}, threading.Lock()


def handle_lock(handle):
    """One fetch per handle at a time, so a prefetch and a visitor's spread never both pay for it."""
    with _locks_guard:
        return _locks.setdefault(handle.lower(), threading.Lock())


def get_posts(handle):
    with handle_lock(handle):
        return make_spread.get_tweets(handle, max_age=fetch.MAX_AGE, before_page=before_page)


def queue_spread(spread_id):
    fetch_jobs.put((0, _order(), 'spread', spread_id))


def fetcher():
    while True:
        _, _, kind, value = fetch_jobs.get()
        if kind == 'prefetch':
            try:
                get_posts(value)
            except Exception as e:           # a visitor's own request will report it properly
                log.info('prefetch of @%s stopped: %s', value, e)
            continue
        row = get(value)
        if row is None or row['status'] in ('done', 'failed'):
            continue
        try:
            update(value, status='fetching')
            get_posts(row['handle'])
            update(value, status='waiting')
            press_jobs.put(value)
        except fetch.FetchError as e:
            update(value, status='failed', error=str(e))
        except Exception:
            log.error('fetching for spread %s failed:\n%s', value, traceback.format_exc())
            update(value, status='failed', error='Something went wrong on our end. Please try again later.')


def press():
    while True:
        spread_id = press_jobs.get()
        row = get(spread_id)
        if row is None or row['status'] in ('done', 'failed'):
            continue
        answers = json.loads(row['answers'])
        try:
            path = get_posts(row['handle'])      # already fetched: read from the cache
            after = record_of(get(row['parent'])) if row['parent'] else None
            def progress(stage, picture=None):
                update(spread_id, status=stage, **({'picture': picture['id']} if picture else {}))
            out_dir, record = make_spread.make(path, **answers, after=after, uses=recent_uses(), progress=progress)
            note_uses(record)
            update(spread_id, status='done', out_dir=out_dir, handle=record['handle'])   # as the account spells it
        except fetch.FetchError as e:
            update(spread_id, status='failed', error=str(e))
        except make_spread.NotEnoughTweets as e:
            update(spread_id, status='failed', error=(
                f"@{row['handle']} doesn't have enough recent posts for {'a B-side' if row['parent'] else 'a zine'}: "
                f"we found {e.usable} we could use and need {e.needed}. Replies, reposts, and posts that need "
                "a picture or another post to make sense are left out."))
        except Exception:
            log.error('spread %s failed:\n%s', spread_id, traceback.format_exc())
            update(spread_id, status='failed', error='Something went wrong on our end. Please try again later.')


@contextlib.asynccontextmanager
async def lifespan(app):
    with db() as con:          # pick up anything left unfinished by a restart
        for row in con.execute("select id from spreads where status not in ('done', 'failed') order by created"):
            queue_spread(row['id'])          # posts already fetched come straight from the cache
    for n in range(FETCHERS):
        threading.Thread(target=fetcher, daemon=True, name=f'fetcher-{n}').start()
    threading.Thread(target=press, daemon=True, name='press').start()
    yield


# --- pages ----------------------------------------------------------------------------------------

templates = Environment(loader=FileSystemLoader(os.path.join(APP, 'templates')), autoescape=select_autoescape())


def static_version():
    """Changes whenever the stylesheet or script does, so browsers don't keep a stale copy after a deploy."""
    return str(int(max(os.path.getmtime(os.path.join(APP, 'static', f)) for f in ('site.css', 'site.js'))))


def page(name, request, status=200, headers=None, **context):
    html = templates.get_template(name).render(base=BASE, request=request, v=static_version(), **context)
    return HTMLResponse(html, status_code=status, headers=headers)


async def home(request):
    rng = secrets.SystemRandom()
    tokens = [(theme, *rng.choice(options)) for theme, options in TOKENS.items()]
    rng.shuffle(tokens)
    return page('home.html', request, tokens=tokens, inks=make_spread.INKS,
                handle=request.query_params.get('handle', ''), error=request.query_params.get('error'))


def fresh_cache(handle):
    """True when the handle's posts were fetched recently enough to reuse."""
    path = os.path.join(ROOT, 'build', 'tweets', handle.lower() + '.json')
    try:
        with open(path, encoding='utf8') as fh:
            fetched = json.load(fh).get('fetched_at', os.path.getmtime(path))
    except (OSError, ValueError):
        return False
    return time.time() - fetched < fetch.MAX_AGE


async def prefetch(request):
    """Start fetching a handle's posts as soon as it's entered, while the visitor answers the quiz.
    Silent either way. Limited per visitor, behind visitors' own spreads, and under the daily budget."""
    form = await request.form()
    handle = clean_handle(form.get('handle'))
    if handle and not fresh_cache(handle) and fetch_jobs.qsize() < MAX_QUEUE:
        who = visitor(request)
        with db() as con:
            recent = con.execute('select count(*) from prefetches where visitor = ? and at > ?',
                                 (who, time.time() - 3600)).fetchone()[0]
            if exempt(request) or recent < PREFETCH_PER_HOUR:
                con.execute('insert into prefetches (visitor, at) values (?, ?)', (who, time.time()))
                fetch_jobs.put((1, _order(), 'prefetch', handle))
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})


async def make(request):
    form = await request.form()
    handle = clean_handle(form.get('handle'))
    if not handle:
        return RedirectResponse(f"{BASE}/?error=handle", status_code=303)
    pick = lambda name, allowed: form.get(name) if form.get(name) in allowed else None   # noqa: E731
    token = pick('token', make_spread.TOKEN_THEME)
    answers = dict(theme=make_spread.TOKEN_THEME[token] if token else pick('theme', TOKENS),
                   ink=pick('ink', make_spread.INKS), type=None, layout=None, reroll=0)
    if token:                  # only when picked, so "surprise me" requests still match earlier ones
        answers['token'] = token
    key = json.dumps([handle.lower(), answers], sort_keys=True)
    with db() as con:
        row = con.execute("select id from spreads where key = ? and status != 'failed'", (key,)).fetchone()
        if row:
            return RedirectResponse(f'{BASE}/s/{row["id"]}', status_code=303)
        who = visitor(request)
        if not exempt(request) and (reason := too_many(con, who)):
            return limited(request, reason)
        spread_id = secrets.token_urlsafe(8)
        now = time.time()
        con.execute('insert into spreads (id, handle, key, answers, status, created, updated, visitor) '
                    'values (?,?,?,?,?,?,?,?)', (spread_id, handle, key, json.dumps(answers), 'queued', now, now, who))
    queue_spread(spread_id)
    return RedirectResponse(f'{BASE}/s/{spread_id}', status_code=303)


def _spread(request):
    spread_id = request.path_params['id']
    return get(spread_id) if re.fullmatch(r'[A-Za-z0-9_-]{6,20}', spread_id) else None


NOINDEX = {'X-Robots-Tag': 'noindex, nofollow'}


async def spread(request):
    row = _spread(request)
    if row is None:
        return page('missing.html', request, status=404, headers=NOINDEX)
    if row['status'] == 'done':
        record = record_of(row)
        origin = f'{request.url.scheme}://{request.url.netloc}'
        # The first spread offers a B-side when the account has tweets enough; the B-side links back.
        b_side = None if row['parent'] else b_side_of(row['id'])
        can_b_side = not row['parent'] and b_side is None and len(record['labels']) >= make_spread.MIN_B_SIDE
        return page('spread.html', request, headers=NOINDEX, row=row, record=record, b_side=b_side,
                    ink=make_spread.INKS[record['resolved']['ink']],
                    can_b_side=can_b_side, parent=row['parent'] and get(row['parent']),
                    url=f"{origin}{BASE}/s/{row['id']}", origin=origin)
    if row['status'] == 'failed':
        return page('failed.html', request, headers=NOINDEX, row=row)
    return page('making.html', request, headers=NOINDEX, row=row, stages=STAGES,
                shown=SHOWN_AS.get(row['status'], row['status']))


async def make_b_side(request):
    row = _spread(request)
    if row is None or row['status'] != 'done' or row['parent']:
        return Response('Not found', status_code=404)
    with db() as con:
        existing = con.execute("select id from spreads where parent = ? and status != 'failed'", (row['id'],)).fetchone()
        if existing:
            return RedirectResponse(f'{BASE}/s/{existing["id"]}', status_code=303)
        if len(record_of(row)['labels']) < make_spread.MIN_B_SIDE:
            return RedirectResponse(f'{BASE}/s/{row["id"]}', status_code=303)
        who = visitor(request)
        if not exempt(request) and (reason := too_many(con, who)):
            return limited(request, reason)
        spread_id = secrets.token_urlsafe(8)
        now = time.time()
        con.execute('insert into spreads (id, handle, key, answers, status, created, updated, parent, visitor) '
                    'values (?,?,?,?,?,?,?,?,?)',
                    (spread_id, row['handle'], row['key'] + ' b-side', row['answers'], 'queued', now, now, row['id'], who))
    queue_spread(spread_id)
    return RedirectResponse(f'{BASE}/s/{spread_id}', status_code=303)


async def status(request):
    row = _spread(request)
    if row is None:
        return JSONResponse({'status': 'missing'}, status_code=404)
    shown = SHOWN_AS.get(row['status'], row['status'])
    body = {'status': shown, 'label': STAGES.get(shown, '')}
    if row['picture'] and row['status'] not in ('done', 'failed'):
        body['picture'] = f"{BASE}/s/{row['id']}/picture.jpg"
    return JSONResponse(body, headers={'Cache-Control': 'no-store'})


LIBRARY = {}


def picture_thumb(image_id):
    """A 360 px JPEG of a library picture for the progress page, made once and kept in build/cache."""
    if not LIBRARY:
        LIBRARY.update({a['id']: a for a in make_spread.match.load_library()})
    asset = LIBRARY.get(image_id)
    if asset is None:
        return None
    out = os.path.join(ROOT, 'build', 'cache', f'thumb-{image_id}.jpg')
    if not os.path.exists(out):
        from PIL import Image
        im = Image.open(make_spread.cached_image(asset)[0])
        if im.mode in ('RGBA', 'LA', 'P'):
            im = im.convert('RGBA')
            paper = Image.new('RGBA', im.size, (251, 248, 241, 255))
            paper.alpha_composite(im)
            im = paper
        im = im.convert('RGB')
        im.thumbnail((360, 360), Image.LANCZOS)
        im.save(out, quality=82)
    return out


async def picture(request):
    """The spread's main picture, once chosen, for the progress page's slot machine."""
    row = _spread(request)
    path = row and row['picture'] and picture_thumb(row['picture'])
    if not path:
        return Response('Not found', status_code=404)
    return FileResponse(path, media_type='image/jpeg', headers={**NOINDEX, 'Cache-Control': 'public, max-age=86400'})


async def spread_file(request):
    row = _spread(request)
    name = request.path_params['name']
    if row is None or row['status'] != 'done' or name not in FILES:
        return Response('Not found', status_code=404)
    download = None
    if request.query_params.get('download'):
        download = f"{row['handle']}-zine{'-b-side' if row['parent'] else ''}.{name.rsplit('.', 1)[1]}"
    return FileResponse(os.path.join(row['out_dir'], name), media_type=FILES[name], filename=download,
                        content_disposition_type='attachment', headers={**NOINDEX, 'Cache-Control': 'public, max-age=86400'})


async def robots(request):
    return Response('User-agent: *\nDisallow: /s/\n', media_type='text/plain')


app = Starlette(routes=[Mount(BASE or '/', routes=[
    Route('/', home),
    Route('/make', make, methods=['POST']),
    Route('/prefetch', prefetch, methods=['POST']),
    Route('/s/{id}', spread),
    Route('/s/{id}/status', status),
    Route('/s/{id}/b-side', make_b_side, methods=['POST']),
    Route('/s/{id}/picture.jpg', picture),
    Route('/s/{id}/{name}', spread_file),
    Route('/robots.txt', robots),
    Route('/exempt', make_exempt),
    Mount('/static', StaticFiles(directory=os.path.join(APP, 'static')), name='static'),
    Mount('/fonts', StaticFiles(directory=os.path.join(APP, 'fonts')), name='fonts'),
])], lifespan=lifespan)


if __name__ == '__main__':
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host='127.0.0.1', port=int(fetch.env('PORT') or 3004), proxy_headers=True)
