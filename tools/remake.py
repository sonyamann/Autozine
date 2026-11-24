"""Remake existing spreads in place, keeping their share links. Run on the server as the app's user:

    cd /srv/autozine/app && sudo -u autozine sh -c 'set -a; . /srv/autozine/env; set +a; \
        XDG_CACHE_HOME=build/.cache /srv/autozine/venv/bin/python tools/remake.py ID [ID ...] [--refetch]'

Pass a spread's id (from its /s/<id> link); a failed one is retried in place. Its B-side, if any, is remade after it so the two still
share nothing. --refetch discards the cached posts first (a paid fetch). The spread keeps its id,
answers and seed, so with the same posts it comes out the same; new posts or code give a new one.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app'))
import server        # noqa: E402
import make_spread   # noqa: E402
import json          # noqa: E402


def remake(spread_id):
    row = server.get(spread_id)
    if row is None or row['status'] not in ('done', 'failed'):
        print(spread_id, 'is missing or still being made; skipped')
        return
    path = server.get_posts(row['handle'])
    after = server.record_of(server.get(row['parent'])) if row['parent'] else None
    try:
        out_dir, record = make_spread.make(path, **json.loads(row['answers']), after=after, uses=server.recent_uses())
    except make_spread.NotEnoughTweets as e:
        print(spread_id, f'not remade: only {e.usable} usable posts, needs {e.needed}; the old version stays')
        return
    server.update(spread_id, status='done', error=None, out_dir=out_dir, picture=record['images'][0], handle=record['handle'])
    print(spread_id, f"remade: @{record['handle']}, layout {record['resolved']['layout']}, headline {record['tweets_used'][0]}")


def main():
    ids = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--refetch' in sys.argv:
        for handle in {server.get(i)['handle'].lower() for i in ids if server.get(i)}:
            path = os.path.join(server.ROOT, 'build', 'tweets', handle + '.json')
            if os.path.exists(path):
                os.remove(path)
    for spread_id in ids:
        remake(spread_id)
        b = server.b_side_of(spread_id)
        if b and b['id'] not in ids:
            remake(b['id'])


if __name__ == '__main__':
    main()
