#!/bin/sh
# Deploys the committed state of HEAD to the studio Droplet and restarts Autozine.
# Uncommitted changes are not sent. The server's build/ folder (database, tweet and
# Jev caches, finished spreads) and its settings in /srv/autozine/env are never touched.
#
# The library's pictures aren't in git: tools/slim_library.py makes server-sized
# copies in build/library-slim/, and this script sends them alongside the code.
# The one-time server setup is in HOSTING.md (private).
#
#   sh tools/deploy.sh
set -eu

APP="/srv/autozine/app"

cd "$(dirname "$0")/.."
# The server's SSH address (user@host) comes from STUDIO_HOST, set in the
# environment or in .env, so it stays out of the repo.
HOST="${STUDIO_HOST:-$(sed -n 's/^STUDIO_HOST=//p' .env 2>/dev/null | tail -n 1)}"
[ -n "$HOST" ] || { echo "Set STUDIO_HOST=user@host in .env first." >&2; exit 1; }
if [ -n "$(git status --porcelain)" ]; then
  echo "Note: uncommitted changes are not deployed." >&2
fi

.venv/bin/python tools/slim_library.py build/library-slim

tmp=$(mktemp -d) && chmod 755 "$tmp"
trap 'rm -rf "$tmp"' EXIT
git archive HEAD | tar -x -C "$tmp"
chmod -R a+rX "$tmp" build/library-slim

rsync -rlptz --delete --exclude /build/ --exclude '/library/*/' "$tmp/" "$HOST:$APP/"
rsync -rlptz --delete --exclude /manifest.csv --exclude /sync_manifest.py \
  build/library-slim/library/ "$HOST:$APP/library/"
ssh "$HOST" "/srv/autozine/venv/bin/pip install -q --disable-pip-version-check -r $APP/requirements.txt \
  && sudo systemctl restart autozine && sleep 3 && systemctl is-active autozine \
  && curl -fsS -o /dev/null -w 'autozine answers %{http_code}\n' http://127.0.0.1:3005/autozine/"
echo "Deployed $(git rev-parse --short HEAD)."
