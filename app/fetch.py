"""Fetch a handle's recent tweets from TwitterAPI.io and cache them.

Uses the advanced search endpoint with `from:<handle> -filter:replies -filter:retweets`, so
reposts and replies, which the spread never uses, aren't fetched or billed (Nov 20: a page of
the user-timeline endpoint for a repost-heavy account held 19 reposts of 20). Quote posts are
kept: thin accounts fall back on them.

    python3 app/fetch.py somehandle            # 5 pages of 20, cached in build/tweets/
    python3 app/fetch.py somehandle --pages 1

Reads TWITTERAPI_KEY from the environment or .env. Each tweet returned is billed,
so a cached fetch is reused unless it is older than max_age or --refresh is passed.
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = 'https://api.twitterapi.io/twitter/tweet/advanced_search'
SOURCE = 'search'                # caches made with the older user-timeline endpoint aren't continued
MAX_AGE = 7 * 24 * 3600          # a cached fetch older than this is fetched fresh


class FetchError(Exception):
    """The account couldn't be fetched; the message is safe to show a visitor."""


PAGE_COST = 20 * 0.00015         # dollars per page of 20 tweets, at TwitterAPI.io's stated price


def env(name):
    """A setting from the environment, else from .env in the project root."""
    if os.environ.get(name):
        return os.environ[name]
    path = os.path.join(ROOT, '.env')
    if os.path.exists(path):
        for line in open(path):
            key, _, value = line.strip().partition('=')
            if key == name and value:
                return value.strip().strip('"\'')
    return None


def api_key():
    key = env('TWITTERAPI_KEY')
    if not key:
        raise RuntimeError('TWITTERAPI_KEY is not set')
    return key


class Unavailable(Exception):
    """TwitterAPI.io kept failing (busy, or a search it couldn't complete), after retries."""


def get_json(url, key):
    """GET a TwitterAPI.io URL, retrying for about 30 s on busy or failed responses (which aren't billed)."""
    req = urllib.request.Request(url, headers={'X-API-Key': key})
    for attempt in range(6):
        try:
            return json.load(urllib.request.urlopen(req, timeout=45))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503):
                raise RuntimeError(f'TwitterAPI.io returned HTTP {e.code}: {e.read().decode()[:200]}')
        except OSError:                        # dropped connection or timeout
            pass
        if attempt < 5:
            time.sleep(2 + attempt * 2)        # 2, 4, 6, 8, 10 s
    raise Unavailable(url)


def fetch_timeline(handle, pages, path, key, before_page=None, data=None):
    """Fallback when search is down: the user-timeline endpoint (the app's source before Nov 20). Same price,
    but reposts come too and are filtered out later. Returns the cache path."""
    data = data or {'tweets': [], 'pages': 0, 'next_cursor': '', 'fetched_at': time.time(), 'source': 'timeline'}
    while data['pages'] < pages:
        if before_page:
            before_page()
        query = urllib.parse.urlencode({'userName': handle, 'cursor': data['next_cursor']})
        try:
            body = get_json(f'https://api.twitterapi.io/twitter/user/last_tweets?{query}', key)
        except Unavailable:
            if data['tweets']:
                break
            raise RuntimeError('TwitterAPI.io is unavailable right now (search and timeline)')
        if body.get('status') != 'success':
            msg = body.get('msg') or body.get('message') or ''
            if any(w in msg.lower() for w in ('not found', 'suspend', 'protected', 'unavailable')):
                raise FetchError(f"Couldn't read @{handle}: the account may not exist, or may be private or suspended.")
            raise RuntimeError(f'TwitterAPI.io error: {msg}')
        seen = {t['id'] for t in data['tweets']}
        data['tweets'] += [t for t in (body.get('data') or {}).get('tweets') or [] if t['id'] not in seen]
        data['pages'] += 1
        data['next_cursor'] = body.get('next_cursor') or ''
        if not body.get('has_next_page') or not data['next_cursor']:
            data['done'] = True
            break
    if not data['tweets']:
        raise FetchError(f"Couldn't find any posts from @{handle}. The account may not exist, or may be private or suspended.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    return path


def oldest_time(tweets):
    """Unix time of the oldest post, for continuing a search with until_time."""
    from datetime import datetime
    return min(int(datetime.strptime(t['createdAt'], '%a %b %d %H:%M:%S %z %Y').timestamp()) for t in tweets)


def fetch(handle, pages=5, refresh=False, max_age=None, before_page=None):
    """Return the path of a JSON file holding {"tweets": [...]} for the handle.

    A cached fetch is reused unless it is older than max_age seconds. Asking for
    more pages than the cache holds continues from where it stopped, so nothing is
    fetched (or billed) twice. Raises FetchError for accounts that can't be read.
    `before_page` is called before each billed request and may raise FetchError to stop
    (the web app uses it for its daily spending cap).
    """
    handle = handle.lstrip('@')
    path = os.path.join(ROOT, 'build', 'tweets', handle.lower() + '.json')
    fresh = {'tweets': [], 'pages': 0, 'next_cursor': '', 'fetched_at': time.time(), 'source': SOURCE}
    data = fresh
    if os.path.exists(path) and not refresh:
        with open(path, encoding='utf8') as fh:
            data = json.load(fh)
        if max_age is not None and time.time() - data.get('fetched_at', os.path.getmtime(path)) > max_age:
            data = fresh
        data.setdefault('pages', 5)
        if data.get('done') and not data.get('exhausted') and data.get('source') == SOURCE and data['tweets']:
            # Before Nov 23 a search that stopped early was taken as the account's end; pick up from its oldest post.
            data['done'], data['next_cursor'], data['until'] = False, '', oldest_time(data['tweets'])
        if data['pages'] >= pages or data.get('done'):
            return path
        if data.get('source') == 'timeline' and 'next_cursor' in data:  # made while search was down: carry it on
            return fetch_timeline(handle, pages, path, api_key(), before_page, data)
        if 'next_cursor' not in data or data.get('source') != SOURCE:   # can't continue an older cache
            data = fresh
    key = api_key()
    while data['pages'] < pages:
        if before_page:
            before_page()
        q = f'from:{handle} -filter:replies -filter:retweets'
        if data.get('until'):                  # continuing a search that stopped early
            q += f' until_time:{data["until"]}'
        query = urllib.parse.urlencode({'query': q, 'queryType': 'Latest', 'cursor': data['next_cursor']})
        try:
            body = get_json(f'{URL}?{query}', key)
        except Unavailable:
            if not data['tweets']:
                return fetch_timeline(handle, pages, path, key, before_page)
            # Search broke midway: top up from the timeline and keep both (the pages already paid for too).
            try:
                tl = json.load(open(fetch_timeline(handle, pages, path, key, before_page), encoding='utf8'))
            except (RuntimeError, FetchError):
                data['done'] = True            # the timeline's down too: keep what there is
                break
            # Carry on as a timeline fetch (later pages continue it), keeping the search's posts too.
            seen = {t['id'] for t in tl['tweets']}
            tl['tweets'] += [t for t in data['tweets'] if t['id'] not in seen]
            data = tl
            break
        if 'tweets' not in body:
            msg = body.get('message') or body.get('msg') or str(body)[:200]
            raise RuntimeError(f'TwitterAPI.io error: {msg}')
        seen = {t['id'] for t in data['tweets']}
        new = [t for t in body.get('tweets') or [] if t['id'] not in seen]
        data['tweets'] += new
        data['pages'] += 1
        data['next_cursor'] = body.get('next_cursor') or ''
        if not body.get('has_next_page') or not data['next_cursor']:
            # Search can stop early on a long account (its docs say so): carry on from just before the oldest
            # post so far. Only a continuation that finds nothing new is the account's real end.
            until = oldest_time(data['tweets']) if data['tweets'] else None
            if not new or not until or until == data.get('until'):
                data['done'] = data['exhausted'] = True
                break
            data['until'], data['next_cursor'] = until, ''
    if not data['tweets']:
        raise FetchError(f"Couldn't find any posts from @{handle}. The account may not exist, or may be private or suspended.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    return path


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('handle')
    ap.add_argument('--pages', type=int, default=5)
    ap.add_argument('--refresh', action='store_true')
    a = ap.parse_args()
    try:
        p = fetch(a.handle, a.pages, a.refresh)
    except FetchError as e:
        raise SystemExit(str(e))
    print(len(json.load(open(p))['tweets']), 'tweets ->', p)
