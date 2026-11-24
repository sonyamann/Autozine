# Autozine

The app is live here: https://studio.sonyasupposedly.com/autozine/

Working files for the tweet zine described in `SPEC.md`. The spec's "Build status" section says what exists and what doesn't.

The current goal is to release the free two-page spread on its own. The paid 8-page zine is tabled; its plans are kept at the end of the spec.

## What's here

| Path | What it is |
| --- | --- |
| `SPEC.md` | The product plan, with decisions made so far |
| `app/` | Prototype that renders the free spread |
| `library/` | Images sorted into theme folders, with `manifest.csv` |
| `corpus/` | Public domain texts and the snippets split from them |
| `build/` | Generated output and caches; not in git, safe to delete |

## Setup

- Pango, which WeasyPrint needs: `brew install pango python@3.13` on a Mac; `apt install libpango-1.0-0 libpangoft2-1.0-0` on Debian or Ubuntu. Emoji in tweets need a color emoji font; Macs have one, and on Linux it's `fonts-noto-color-emoji`.
- A Python environment with the packages in `requirements.txt` (WeasyPrint and Pillow for rendering, Starlette and Uvicorn for the web app), kept in `.venv/` (not in git):

      /opt/homebrew/bin/python3.13 -m venv .venv
      .venv/bin/pip install -r requirements.txt

  Run the scripts with `.venv/bin/python`.
- A `.env` file in the project root (not in git):

      TWITTERAPI_KEY=...       # twitterapi.io, for fetching tweets
      TYPESAFE_API_KEY=...     # typesafe.ai, for Jev tagging and matching

  Both are optional. Without the TypeSafe key the prototype falls back to keyword rules; without the TwitterAPI.io key it can only render from a tweets file.
- The image files are not in git (about 4 GB). `library/manifest.csv` is, but rendering needs the images on disk.

## Run the web app

    .venv/bin/python app/server.py

Then open http://127.0.0.1:3004/autozine/. Enter a handle, answer the quiz (or don't), and the spread appears on its own share page, with PNG and PDF downloads. In the Claude desktop app, `.claude/launch.json` starts the same server.

- **While a spread is made**, its page shows the stages and a slot machine of library pictures that lands on the spread's main picture once it's chosen. The reel is 40 thumbnails in `app/static/reel/`; `tools/make_reel.py` remakes them.
- **Fetching and making are separate:** two fetcher threads fetch posts (a visitor's spread first, then prefetches), and one press thread makes spreads one at a time, so the next spread's fetch overlaps the current render.
- **Prefetch:** when a visitor leaves the handle field, the quiz page asks `/prefetch` to start fetching that account while they answer the rest. It's limited per visitor (`AUTOZINE_PREFETCH_PER_HOUR`, default 10) and counts against the daily budget.
- **Spreads are made one at a time** by the press. A row in `build/autozine.db` gives each spread a random, unlisted id and points to its folder in `build/out/`.
- **B-sides:** under a finished spread, accounts with at least 12 usable tweets get a "Make another page spread" button for a B-side: a second spread with new tweets and pictures and a dream-book card. It's linked to and from the first.
- **Repeat requests are free:** the same handle and answers always make the same spread, so the app reuses it. Fetched tweets are reused for 7 days.
- **Testing costs money for new handles:** entering a handle that hasn't been fetched in the last 7 days buys a fresh fetch (about 1.5–3 cents). The accounts in `build/tweets/` can be tried for free while their files are recent.
- **Settings**, from the environment or `.env`: the two API keys above, `AUTOZINE_BASE` (the URL path, default `/autozine`) `PORT` (default 3004), and the limits below.
- **Limits**, since every new handle buys a fetch: each visitor (a salted hash of their IP, never the IP itself) can start `AUTOZINE_PER_HOUR` zines an hour (default 5) and `AUTOZINE_PER_DAY` a day (default 20), B-sides included. No new zines are taken while 20 are waiting. Tweet fetches stop for the UTC day once they reach `AUTOZINE_DAILY_BUDGET` dollars (default 30, about 1,000–2,000 accounts); accounts fetched in the last week still work then. Visitors who hit a limit get a "Not right now" page. A browser that opens `/autozine/exempt?key=<AUTOZINE_EXEMPT_KEY>` gets a cookie (good for 400 days) that skips the per-visitor limits, though not the daily budget; without the key set, the link does nothing.

## The server

Live at https://studio.sonyasupposedly.com/autozine/, on a small server beside the other Supposedly Studio experiments, behind Caddy.

    sh tools/deploy.sh

sends the committed code and the library's pictures, then restarts the service. It needs `STUDIO_HOST` (the server's `user@host`) in `.env`. The pictures aren't in git: `tools/slim_library.py` writes copies at most 2000 px on the long side to `build/library-slim/` (about 1 GB, against 5 GB of originals), and only new or changed ones are redone or sent. The service and nightly backup are `tools/autozine.service` and `tools/backup-autozine`; the server's setup notes (`HOSTING.md`) are private.

## Render a spread

    .venv/bin/python app/make_spread.py --tweets app/fixtures/sample_tweets.json
    .venv/bin/python app/make_spread.py --handle somehandle

| Option | Values | If omitted |
| --- | --- | --- |
| `--theme` | `nature`, `domestic`, `high_techne`, `cosmopolitan` | Jev picks from the tweets |
| `--ink` | `vermilion`, `ultramarine`, `forest`, `plum` | chosen by the seed |
| `--type` | `bookish`, `machine`, `poster` | Jev picks from the tweets' voice (the seed picks without Jev); the web app doesn't ask |
| `--layout` | `a`, `b`, `c` | chosen by the seed |
| `--reroll` | a number | 0; changing it gives a different spread |
| `--no-jev` | | Jev is used when its key is set |

Output goes to `build/out/<handle>-<seed>/`:

- `spread.pdf`: the spread, 11" × 8.5"
- `spread.png`: 2200 × 1700, made from the PDF
- `spread.html`: the page as rendered, with fitted font sizes written in
- `share-1200x630.jpg`: link preview
- `share-2048.jpg`: the spread, for attaching to a post
- `record.json`: the seed, answers, tweet labels, every choice made, and any text that still overflows its slot at the smallest size

The same handle and options always give the same spread.

**Costs.** `--handle` fetches original posts 20 at a time, one page after another, until 40 are usable (usually 3–9 pages, about 1–3 cents) and caches them in `build/tweets/`; run `python3 app/fetch.py <handle> --refresh` to fetch again. Jev costs about a tenth of a cent per spread, and its answers are cached in `build/jev_cache/`.

The sample tweets in `app/fixtures/` are invented, for a fictional account.

## How the prototype works

| File | Job |
| --- | --- |
| `app/fetch.py` | Fetches a handle's tweets from TwitterAPI.io |
| `app/pipeline.py` | Filters tweets (retweets, link-only, too short, personal information), ranks them against the account's median engagement, and picks a varied set |
| `app/jev.py` | Asks Jev which tweets are too raw to joke beside, which stand alone, which theme fits, and how well each candidate definition, quote, and image suits the tweets |
| `app/match.py` | Builds keyword shortlists for Jev to score; also the keyword-only fallback |
| `app/make_spread.py` | Chooses the content and lays out one of three templates in HTML |
| `app/render.py` | Fits text to its slots, tints ornaments, bakes shadows, and renders the PDF and PNG with WeasyPrint |
| `app/server.py` | The web app: handle entry and quiz, the press that makes one spread at a time, progress and share pages, downloads |
| `app/templates/`, `app/static/` | The web app's pages, stylesheet and script |
| `app/fonts/` | Open-licensed fonts (SIL Open Font License) |

## Image library

`library/manifest.csv` has one row per image: theme, role, source, license, flags, and for images used in matching, a written description.

- **Theme folders hold only images the spread can pick.** Decorative pieces (borders, medallions, patterns) live in `ornament/`. Images set aside as too faint, too small, cropped badly or containing nudity are in `unused/<theme>/`; move one back into a theme folder to use it anyway.
- **Curating:** delete a file to remove an image; move it to another theme folder to reclassify it. Then run:

      python3 library/sync_manifest.py --dry-run
      python3 library/sync_manifest.py

- **Adding:** every new image needs a manifest row with its source and license before it can be used. File names start with the manifest ID (`A072_...`).
- `library/review/` holds contact sheets per theme. They are not regenerated automatically.

## Text corpus

- `corpus/texts/`: the source texts as downloaded, with Project Gutenberg's header and license removed.
- `corpus/sources.csv`: edition, source, and rights for each text.
- `corpus/snippets.csv`: every snippet (definition, proverb, fragment, dream reading, highlight), with a status of `ok`, `review` or `drop`. Only `ok` snippets are used.
- `corpus/overrides.csv`: decisions made by hand, such as dropped Devil's Dictionary entries.
- `corpus/theme_tags.csv`: a theme for each snippet, written by `corpus/tag_themes.py` (uses Jev).
- `corpus/kindle/`: the Kindle highlights export and the cleaned, theme-tagged snippets made from it.

To rebuild the snippets after changing a text or an override:

    python3 corpus/build_snippets.py

## License

The code is under the MIT License (see `LICENSE`). Bundled fonts keep their own licenses, which sit beside them.
