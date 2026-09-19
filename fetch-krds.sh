#!/bin/bash
#
# Fetch krds.py from upstream instead of vendoring a copy in this repo.
#
# krds.py is jhowell's KRDS parser (GPL v3), maintained at
# https://github.com/K-R-D-S/KRDS. It decodes the legacy .yjr sidecars.
#
# The version is pinned to a commit and checked against a known SHA-256, so
# this fetches the same bytes every time rather than whatever main happens to
# hold. To move to a newer upstream release, run:
#
#     ./fetch-krds.sh --latest
#
# which updates the pin in this file after downloading, and shows you the
# diff to review before you commit it.

set -e

REPO="K-R-D-S/KRDS"
FILE="krds.py"

# Pinned upstream version: 2026-06-18, "Tolerate unknown trailing fields from
# newer firmware" — needed for Kindle firmware 5.18+.
PINNED_REF="9c8a0b0ec9cb6af72fba900a6f9b09f92de477de"
PINNED_SHA256="921ea44e31e872afc38283b9cdfc3d167b11dbad1831379e8fa28ac822d835b2"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$DIR/$FILE"

REF="$PINNED_REF"
UPDATE_PIN=0
if [ "$1" = "--latest" ]; then
  echo "Resolving latest upstream commit for $FILE..."
  REF=$(curl -fsSL "https://api.github.com/repos/$REPO/commits?path=$FILE&per_page=1" \
        | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['sha'])")
  UPDATE_PIN=1
  echo "Latest: $REF"
fi

URL="https://raw.githubusercontent.com/$REPO/$REF/$FILE"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

echo "Fetching $FILE from $REPO @ ${REF:0:12}..."
curl -fsSL -o "$TMP" "$URL"

GOT=$(shasum -a 256 "$TMP" | awk '{print $1}')

if [ "$UPDATE_PIN" -eq 0 ]; then
  if [ "$GOT" != "$PINNED_SHA256" ]; then
    echo "❌ Checksum mismatch — refusing to install."
    echo "   expected $PINNED_SHA256"
    echo "   got      $GOT"
    echo "   Upstream may have force-pushed. Review, then use --latest."
    exit 1
  fi
  echo "Checksum verified."
else
  echo "Checksum: $GOT"
fi

if [ -f "$DEST" ] && ! diff -q "$DEST" "$TMP" >/dev/null; then
  echo
  echo "Changes from the copy you have now:"
  diff "$DEST" "$TMP" || true
  echo
fi

cp "$TMP" "$DEST"
echo "Installed $DEST"

if [ "$UPDATE_PIN" -eq 1 ]; then
  # Rewrite the pin in this file so the new version is what gets verified next.
  sed -i '' \
    -e "s|^PINNED_REF=.*|PINNED_REF=\"$REF\"|" \
    -e "s|^PINNED_SHA256=.*|PINNED_SHA256=\"$GOT\"|" \
    "${BASH_SOURCE[0]}"
  echo
  echo "Updated the pin in $(basename "${BASH_SOURCE[0]}") — review and commit it:"
  echo "  PINNED_REF=\"$REF\""
  echo "  PINNED_SHA256=\"$GOT\""
fi
