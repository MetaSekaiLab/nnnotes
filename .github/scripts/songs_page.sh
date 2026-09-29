#!/usr/bin/env bash
# The chart data page of ournotes-player ($PLAYER_REPOSITORY at $PLAYER_REF: examples/songs, the page that reads
# music-data.json) into $1, for the smoke test of music_data.py check. Only that directory is checked out and nothing
# is built: the test imports the page's pure modules (catalog.js, ranking.js) in Node.js.
set -euo pipefail
dir="$1"
if [ -z "${PLAYER_REF:-}" ]; then
  echo "::error::set the repository variable MUSIC_DATA_PLAYER_REF: the ournotes-player commit whose chart data page reads this music data (.github/MUSIC_DATA.md)"
  exit 1
fi
rm -rf "$dir"
git clone --quiet --filter=blob:none --no-checkout "https://github.com/$PLAYER_REPOSITORY.git" "$dir"
git -C "$dir" sparse-checkout set examples/songs
git -C "$dir" checkout --quiet "$PLAYER_REF"
for f in catalog.js ranking.js; do
  [ -f "$dir/examples/songs/$f" ] || { echo "::error::$PLAYER_REPOSITORY $PLAYER_REF has no examples/songs/$f"; exit 1; }
done
echo "ournotes-player $(git -C "$dir" rev-parse --short HEAD): examples/songs"
