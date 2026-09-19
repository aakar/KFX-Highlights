#!/bin/bash
#
# Pull new Kindle highlights off the device and mail them to Readwise.
#
# Firmware 5.19.x moved annotations out of the per-book .sdr/*.yjr sidecars
# into one account-wide SQLite store. The sidecars are still written but are
# now empty 233-byte templates, so the old .yjr path finds nothing for any
# book read after ~2026-07-26. This script reads the new store first and
# falls back to .yjr for books that predate the move.

# Everything below is configurable. Copy config.example.sh to config.sh and
# edit that (it's gitignored) or set the KFX_* variables in the environment;
# the environment wins. Nothing here should need editing.

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NO_EMAIL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-email|--save-only)
      NO_EMAIL=1
      ;;
    -h|--help)
      cat <<USAGE
Usage: $(basename "$0") [--no-email]

Pull new Kindle highlights off the device, resolve them to text, and email
them. Highlights are always saved as HTML to the output directory.

  --no-email, --save-only   Save the HTML only; don't send anything.
  -h, --help                Show this message.

Settings live in config.sh (see config.example.sh) and can be overridden
with KFX_* environment variables.
USAGE
      exit 0
      ;;
    *)
      echo "Unknown option: $1 (try --help)" >&2
      exit 1
      ;;
  esac
  shift
done

[ -f "$WORK_DIR/config.sh" ] && . "$WORK_DIR/config.sh"

# Where to mount the Kindle. Created if missing.
MOUNT_POINT="${KFX_MOUNT_POINT:-$HOME/mnt/kindle}"

# Where to send highlights. Empty means don't email, just save the HTML.
# Note the "-" and not ":-": KFX_EMAIL="" is a deliberate "don't send".
READWISE_EMAIL="${KFX_EMAIL-add@readwise.io}"
[ "$NO_EMAIL" -eq 1 ] && READWISE_EMAIL=""

# Generated HTML is kept here.
OUTPUT_DIR="${KFX_OUTPUT_DIR:-$WORK_DIR/highlights}"

# ASINs whose .kfx is DRM-locked. The highlight positions are in the store,
# but resolving them to text needs the book contents, which we can't decode.
# Listed so they're skipped quietly instead of failing every run.
DRM_SKIP="${KFX_DRM_SKIP:-}"

# Usually discovered automatically; set KFX_KINDLE_DIR to override.
KINDLE_DIR="${KFX_KINDLE_DIR:-}"

LAST_RUN_FILE="$WORK_DIR/.last_run"
CONVERTER="$WORK_DIR/ksdk_to_krds.py"
KRDS="$WORK_DIR/krds.py"
EXTRACTOR="$WORK_DIR/extract_highlights_kfxlib.py"
LOCAL_DB="$WORK_DIR/.ksdk_annotations.db"

cd "$WORK_DIR" || exit 1
mkdir -p "$MOUNT_POINT" "$OUTPUT_DIR"

for cmd in go-mtpfs python3; do
  command -v "$cmd" >/dev/null || { echo "❌ $cmd not found on PATH"; exit 1; }
done

# krds.py is fetched from upstream rather than vendored here.
if [ ! -f "$KRDS" ]; then
  echo "❌ krds.py is missing. Fetch it from upstream first:"
  echo "     ./fetch-krds.sh"
  exit 1
fi

go-mtpfs "$MOUNT_POINT" &
MTP_PID=$!

cleanup() {
  rm -f changed_yjrs.txt "$LOCAL_DB"
  rm -f *.json
  umount "$MOUNT_POINT" 2>/dev/null
}
trap cleanup EXIT

# The Kindle usually exposes a single volume ("Internal Storage") holding
# documents/ and system/, but the name varies, so look for it rather than
# assuming. Sets STORAGE_ROOT.
find_storage_root() {
  local d
  [ -d "$MOUNT_POINT/documents" ] && { STORAGE_ROOT="$MOUNT_POINT"; return 0; }
  for d in "$MOUNT_POINT"/*/; do
    [ -d "$d/documents" ] && { STORAGE_ROOT="${d%/}"; return 0; }
  done
  return 1
}

# Books live in documents/ on some devices and documents/Downloads/ItemsNN on
# others. Whichever it is, it's the folder holding the .sdr sidecar folders.
# Pick the folder with the most of them: documents/ itself usually has a lone
# "My Clippings.sdr", which would otherwise win just by being shallower.
find_kindle_dir() {
  KINDLE_DIR=$(find "$STORAGE_ROOT/documents" -maxdepth 4 -type d -name "*.sdr" 2>/dev/null \
    | sed 's|/[^/]*\.sdr$||' \
    | sort | uniq -c | sort -rn | head -1 \
    | sed 's|^ *[0-9]* ||')
  [ -n "$KINDLE_DIR" ]
}

# Wait for the mount to come up. The mount point appearing is not enough — MTP
# can expose the book folder before it will walk the .sdr subfolders, and a
# find that runs in that window returns nothing at all. So wait until we can
# actually see .yjr files, and treat "still none" as a failure, not "no news".
STORAGE_ROOT=""
TOTAL_YJR=0
for _ in $(seq 1 20); do
  [ -n "$STORAGE_ROOT" ] || find_storage_root
  if [ -n "$STORAGE_ROOT" ]; then
    [ -n "$KINDLE_DIR" ] || find_kindle_dir
    if [ -n "$KINDLE_DIR" ] && [ -d "$KINDLE_DIR" ]; then
      TOTAL_YJR=$(find "$KINDLE_DIR" -name "*.yjr" 2>/dev/null | wc -l | tr -d ' ')
      [ "$TOTAL_YJR" -gt 0 ] && break
    fi
  fi
  sleep 1
done

if [ -z "$STORAGE_ROOT" ]; then
  echo "❌ Nothing mounted at $MOUNT_POINT — is the Kindle plugged in and unlocked?"
  echo "   MTP does not expose storage while the device is locked."
  kill "$MTP_PID" 2>/dev/null
  exit 1
fi

if [ -z "$KINDLE_DIR" ] || [ ! -d "$KINDLE_DIR" ]; then
  echo "❌ Mounted at $STORAGE_ROOT, but found no book folder (nothing with"
  echo "   .sdr sidecars under documents/). Set KFX_KINDLE_DIR to override."
  exit 1
fi

if [ "$TOTAL_YJR" -eq 0 ]; then
  echo "❌ Mounted, but no .yjr files are visible — the device tree did not"
  echo "   enumerate. Leaving .last_run alone so nothing is skipped. Retry."
  exit 1
fi
echo "Mounted ($TOTAL_YJR books visible)"

LAST_RUN_EPOCH=$( [ -f "$LAST_RUN_FILE" ] && cat "$LAST_RUN_FILE" || echo 0 )

SENT=0
EMPTY=0
SKIPPED=""

# ---------------------------------------------------------------------------
# Locate a book file on the device from its ASIN. Filenames end in _<ASIN>.<ext>
# ---------------------------------------------------------------------------
BOOK_FILE=""
BOOK_EXT=""
find_book() {
  BOOK_FILE=""
  BOOK_EXT=""
  local asin="$1" ext f
  for ext in kfx azw3 azw mobi; do
    for f in "$KINDLE_DIR"/*_"$asin"."$ext"; do
      if [ -f "$f" ]; then
        BOOK_FILE="$f"
        BOOK_EXT="$ext"
        return 0
      fi
    done
  done
  return 1
}

# ---------------------------------------------------------------------------
# Given an ASIN and a krds-style JSON file, resolve the text and mail it.
# ---------------------------------------------------------------------------
process_book() {
  local asin="$1" json="$2" base copied attempt html

  if ! find_book "$asin"; then
    echo "⚠️ No book file on device for $asin"
    SKIPPED="$SKIPPED\n  - $asin (book file not on device)"
    return
  fi

  base=$(basename "$BOOK_FILE" ".$BOOK_EXT")
  echo "Processing: $base"

  # MTP copies fail intermittently ("Bad file descriptor"), which yields a
  # truncated book and a confusing downstream error — so retry and verify size.
  copied=0
  for attempt in 1 2 3; do
    if cp "$BOOK_FILE" "$WORK_DIR/" 2>/dev/null &&
       [ "$(stat -f%z "$WORK_DIR/$base.$BOOK_EXT" 2>/dev/null)" = "$(stat -f%z "$BOOK_FILE")" ]; then
      copied=1
      break
    fi
    echo "  copy attempt $attempt failed, retrying..."
    sleep 2
  done

  if [ "$copied" -eq 0 ]; then
    echo "❌ Could not copy $base.$BOOK_EXT off the device"
    SKIPPED="$SKIPPED\n  - $base (copy failed)"
    return
  fi

  if ! python3 "$EXTRACTOR" "$json" "$base.$BOOK_EXT"; then
    echo "❌ Extraction failed for $base"
    SKIPPED="$SKIPPED\n  - $base (extraction failed)"
    rm -f "$base.$BOOK_EXT"
    return
  fi

  html="${base}.highlights.html"
  if [ -f "$html" ]; then
    mv -f "$html" "$OUTPUT_DIR/$html"
    if [ -n "$READWISE_EMAIL" ]; then
      echo "Emailing: $html"
      osascript <<EOF
tell application "Mail"
  set newMessage to make new outgoing message with properties {subject:"Kindle Highlights", content:"See attached.", visible:true}
  tell newMessage
    make new to recipient at end of to recipients with properties {address:"$READWISE_EMAIL"}
    make new attachment with properties {file name:"$OUTPUT_DIR/$html"} at after the last word of the last paragraph
    send
  end tell
end tell
EOF
      SENT=$((SENT + 1))
    else
      echo "Saved: $OUTPUT_DIR/$html"
      SENT=$((SENT + 1))
    fi
  else
    echo "ℹ️ No highlights resolved for $base — nothing to send"
    EMPTY=$((EMPTY + 1))
  fi

  rm -f "$base.$BOOK_EXT"
}

# ---------------------------------------------------------------------------
# Primary path: the account-wide annotation store.
# ---------------------------------------------------------------------------
DB_ASINS=""
# One store per Amazon account, under a directory named for the account id.
DEVICE_DB=$(find "$STORAGE_ROOT/system/ksdk/.annotations" \
              -name "ksdk_annotation_v1.db" 2>/dev/null | head -1)

if [ -n "$DEVICE_DB" ] && cp "$DEVICE_DB" "$LOCAL_DB" 2>/dev/null; then
  echo "Annotation store: $(basename "$DEVICE_DB")"
  # Space-separated: the dedup check below matches on " $ASIN ", so newlines
  # from the converter would make every lookup miss and double-send the book.
  DB_ASINS=$(python3 "$CONVERTER" "$LOCAL_DB" --list --since "$LAST_RUN_EPOCH" --quiet 2>/dev/null | tr '\n' ' ')

  for ASIN in $DB_ASINS; do
    case " $DRM_SKIP " in
      *" $ASIN "*)
        echo "Skipping $ASIN (known DRM-locked book)"
        continue
        ;;
    esac

    JSON="$WORK_DIR/$ASIN.yjr.json"
    if python3 "$CONVERTER" "$LOCAL_DB" "$ASIN" -o "$JSON" >/dev/null 2>&1; then
      process_book "$ASIN" "$JSON"
    else
      echo "⚠️ No annotations to convert for $ASIN"
    fi
    rm -f "$JSON"
  done
else
  echo "⚠️ No ksdk annotation store found — falling back to .yjr sidecars only."
fi

# ---------------------------------------------------------------------------
# Fallback: legacy .yjr sidecars, for books the store doesn't cover.
# ---------------------------------------------------------------------------
REF="$WORK_DIR/.last_run_ref"
touch -am -t "$(date -u -r "$LAST_RUN_EPOCH" +%Y%m%d%H%M.%S)" "$REF"
find "$KINDLE_DIR" -name "*.yjr" -newer "$REF" > changed_yjrs.txt
rm -f "$REF"

while IFS= read -r YJR_FILE; do
  [ -n "$YJR_FILE" ] || continue

  # The .sdr folder is named after the book file, and the ASIN is its suffix.
  SDR_DIR=$(basename "$(dirname "$YJR_FILE")")
  BASE="${SDR_DIR%.sdr}"
  ASIN="${BASE##*_}"

  # Already handled via the annotation store, or known-DRM: skip.
  case " $DB_ASINS $DRM_SKIP " in
    *" $ASIN "*) continue ;;
  esac

  cp "$YJR_FILE" "$WORK_DIR/" || continue
  YJR_LOCAL="$WORK_DIR/$(basename "$YJR_FILE")"

  if ! python3 "$KRDS" "$YJR_LOCAL" >/dev/null 2>&1; then
    echo "⚠️ Could not decode $(basename "$YJR_FILE")"
    rm -f "$YJR_LOCAL"
    continue
  fi

  JSON="$YJR_LOCAL.json"
  # The post-5.19 sidecars are empty templates; don't bother with the book.
  if ! grep -q "annotation.personal.highlight" "$JSON" 2>/dev/null; then
    rm -f "$YJR_LOCAL" "$JSON"
    continue
  fi

  echo "(legacy sidecar) $BASE"
  process_book "$ASIN" "$JSON"
  rm -f "$YJR_LOCAL" "$JSON"
done < changed_yjrs.txt

echo
echo "===== Summary ====="
if [ -n "$READWISE_EMAIL" ]; then
  echo "Emailed:       $SENT"
else
  echo "Saved:         $SENT  ($OUTPUT_DIR)"
fi
echo "No highlights: $EMPTY"
if [ -n "$SKIPPED" ]; then
  echo "Skipped:"
  printf "%b\n" "$SKIPPED"
fi

# Save timestamp for next run
date +%s > "$LAST_RUN_FILE"
