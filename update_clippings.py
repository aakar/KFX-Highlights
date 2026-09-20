#!/usr/bin/env python3
"""Merge extracted highlights into a My Clippings.txt file.

The Kindle stopped appending to My Clippings.txt when annotations moved into
the account-wide database (see the README). This rebuilds the missing entries
from what we extract, in the Kindle's own format, so the file stays a complete
record of everything you've highlighted.

Existing entries are read first and used to skip duplicates, so running this
repeatedly is safe — nothing is rewritten or reordered, new entries are only
appended.

Usage:
    python3 update_clippings.py "My Clippings.txt" book.highlights.json [...]
"""

import argparse
import datetime
import json
import os
import re
import sys


SEPARATOR = "=========="
# The Kindle writes CRLF and sometimes a BOM at the start of an entry.
NEWLINE = "\r\n"
BOM = "﻿"


def parse_existing(path):
    """Return a set of dedup keys for entries already in the file.

    Tolerant on purpose: anything it can't parse is skipped rather than
    treated as an error, since we only ever append.
    """
    keys = set()
    if not os.path.exists(path):
        return keys

    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        raw = f.read()

    for entry in raw.split(SEPARATOR):
        lines = [l.strip("\r\n") for l in entry.splitlines()]
        lines = [l for l in lines if l.strip()]
        if len(lines) < 3:
            continue
        title = lines[0].lstrip(BOM).strip()
        meta = lines[1]
        text = " ".join(lines[2:]).strip()
        kind = "note" if "Your Note" in meta else "highlight"
        keys.add(dedup_key(title, kind, text))

    return keys


def normalize_heading(heading):
    """Collapse the Kindle's heading style and ours onto the same value.

    The Kindle writes a personal document's mangled filename-title, e.g.
        Hacking Growth_ How Today's Fastest-Growin - Sean Ellis (Sean Ellis)
    while we clean that up before writing:
        Hacking Growth: How Today's Fastest-Growin (Sean Ellis)

    Both must key the same, or every entry the Kindle already wrote looks new.
    """
    h = (heading or "").strip()
    m = re.match(r"^(.*) \(([^()]*)\)$", h)
    title, author = (m.group(1), m.group(2)) if m else (h, "")

    if author and title.endswith(" - " + author):
        title = title[: -len(" - " + author)].rstrip()
    title = title.replace("_ ", ": ")
    if title.endswith("_"):
        title = title[:-1]

    return (title.strip(), author.strip())


def dedup_key(heading, kind, text):
    """Key on the text, deliberately not the location.

    The Kindle's own entries use Kindle *locations* (small numbers), while we
    only have raw KFX character positions — different units for the same
    highlight. Including either in the key would make every entry the Kindle
    already wrote look new, and re-add the whole back catalogue.

    Whitespace is normalised so a reflowed copy still matches.
    """
    return (normalize_heading(heading), kind, " ".join(text.split())[:200])


def format_added(iso):
    """Kindle style: 'Monday, July 21, 2025 3:49:26 PM'.

    Built by hand rather than with strftime because the Kindle zero-pads
    neither the day nor the hour, and %-d / %-I aren't portable.
    """
    if not iso:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return ""
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return "%s, %s %d, %d %d:%02d:%02d %s" % (
        dt.strftime("%A"), dt.strftime("%B"), dt.day, dt.year,
        hour, dt.minute, dt.second, ampm)


def heading_for(title, authors):
    """The first line of an entry: 'Title (Author)'.

    Dedup keys must be built from this, not the bare title — the heading is
    what gets parsed back on the next run, and a mismatch means every entry
    is appended again every time.
    """
    who = ", ".join(authors) if authors else ""
    return f"{title} ({who})" if who else title


def build_entry(title, authors, h):
    """One My Clippings.txt entry, without the trailing separator."""
    heading = heading_for(title, authors)

    kind = "Your Note" if h.get("type") == "note" else "Your Highlight"
    page = h.get("page")
    page_part = f"on page {page} " if page else "on page x "

    # No "| Location N" field: the Kindle records Kindle locations and all we
    # have is a raw KFX character position, so printing ours would put a
    # wrong-looking number in a familiar-looking slot. Page is accurate.
    added = format_added(h.get("creationTime", ""))
    added_part = f"| Added on {added}" if added else ""

    meta = f"- {kind} {page_part}{added_part}".rstrip()
    return [heading, meta, "", h.get("text", "").strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clippings", help="My Clippings.txt to update (created if absent)")
    ap.add_argument("json_files", nargs="+", help="one or more .highlights.json files")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="report what would be added without writing")
    args = ap.parse_args()

    existing = parse_existing(args.clippings)
    print(f"{len(existing)} entries already in {os.path.basename(args.clippings)}")

    new_entries = []
    seen = set(existing)

    for jf in args.json_files:
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print(f"  skipping {os.path.basename(jf)}: {e}", file=sys.stderr)
            continue

        title = data.get("title", "")
        authors = data.get("authors", [])
        heading = heading_for(title, authors)
        added_here = 0

        for h in data.get("highlights", []):
            text = h.get("text", "")
            if not text.strip():
                continue
            key = dedup_key(heading, h.get("type", "highlight"), text)
            if key in seen:
                continue
            seen.add(key)
            new_entries.append(build_entry(title, authors, h))
            added_here += 1

        if added_here:
            print(f"  +{added_here:4d}  {title}")

    if not new_entries:
        print("Nothing new to add.")
        return

    if args.dry_run:
        print(f"\nWould append {len(new_entries)} entries (dry run, nothing written).")
        return

    # Append only; never rewrite what's already there. Make sure we start on a
    # clean entry boundary if the existing file didn't end with one.
    needs_sep = False
    size = os.path.getsize(args.clippings) if os.path.exists(args.clippings) else 0
    if size > 0:
        with open(args.clippings, "rb") as f:
            f.seek(max(0, size - 24))
            tail = f.read().decode("utf-8", errors="replace")
        needs_sep = SEPARATOR not in tail

    with open(args.clippings, "a", encoding="utf-8", newline="") as f:
        if needs_sep:
            f.write(SEPARATOR + NEWLINE)
        for lines in new_entries:
            for line in lines:
                f.write(line + NEWLINE)
            f.write(SEPARATOR + NEWLINE)

    print(f"\nAppended {len(new_entries)} entries to {args.clippings}")


if __name__ == "__main__":
    main()
