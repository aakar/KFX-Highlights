#!/usr/bin/env python3
"""Convert Kindle's ksdk annotation database into krds-style JSON.

Firmware 5.19.x stopped writing highlights into the per-book .sdr/*.yjr
sidecars. Those files are still written, but the annotation container is
empty and every book gets a byte-identical 233-byte template. Highlights
now live in a single account-wide SQLite store on the device:

  system/ksdk/.annotations/amzn1.account.<ID>/ksdk_annotation_v1.db

This reads that database and emits the same JSON shape krds.py produced
from a .yjr, so extract_highlights_kfxlib.py can resolve positions to text
against the .kfx without any changes.
"""

import argparse
import datetime
import json
import sqlite3
import sys


# dataset ids used by the ksdk store
DATASET_HIGHLIGHT = 1
DATASET_NOTE = 3


def iso(epoch_ms):
    return datetime.datetime.fromtimestamp(epoch_ms / 1000.0).isoformat()


def position(side):
    """Rebuild a krds 'longPosition:shortPosition' string.

    extract_highlights_kfxlib.py only reads the integer after the colon,
    but keep the long form in place so the output matches krds.py.
    """
    return "%s:%d" % (side.get("longPosition", ""), side.get("shortPosition", 0))


# The same annotation can live in any of three tables: server_view is the
# synced copy, local_edit holds not-yet-synced changes, and
# nonsyncable_annotations holds device-only ones. Every read has to union all
# three — a highlight made seconds ago exists only in local_edit, and a book
# can be present in one table and absent from the others.
#
# Each entry is (table, column naming the book, LIKE pattern for one ASIN).
TABLES = (
    ("server_view", "dataset_id", "-%"),
    ("local_edit", "dataset_id", "-%"),
    ("nonsyncable_annotations", "book_id", "%"),
)

# Column holding the epoch-ms of the last change. Not every table has it.
MODIFIED_COLUMN = "modified_time"


def asin_of(book_id):
    """ASIN prefix of a dataset_id/book_id: 'B01ABCDEFG-ZZZZ' -> 'B01ABCDEFG'."""
    return (book_id or "").split("-", 1)[0]


def has_column(conn, table, column):
    try:
        return any(row[1] == column
                   for row in conn.execute("PRAGMA table_info(%s)" % table))
    except sqlite3.Error:
        return False


def modified_ms(payload):
    """Epoch-ms an annotation last changed, read from its own payload.

    The fallback for tables that keep no modified_time column of their own.
    """
    try:
        p = json.loads(payload)
    except ValueError:
        return None
    t = p.get("last_modified", p.get("created_time"))
    return t if isinstance(t, (int, float)) else None


def rows_for(conn, asin, dataset):
    out = {}
    for table, book_col, like in TABLES:
        sql = ("SELECT annotation_id, serialized_payload FROM %s "
               "WHERE dataset = ? AND %s LIKE ?" % (table, book_col))
        try:
            for ann_id, payload in conn.execute(sql, (dataset, asin + like)):
                out[ann_id] = json.loads(payload)
        except sqlite3.Error:
            continue
    return list(out.values())


def convert(db_path, asin):
    conn = sqlite3.connect(db_path)

    highlights = []
    for p in rows_for(conn, asin, DATASET_HIGHLIGHT):
        highlights.append({
            "startPosition": position(p.get("start_position", {})),
            "endPosition": position(p.get("end_position", {})),
            "creationTime": iso(p["created_time"]),
            "lastModificationTime": iso(p.get("last_modified", p["created_time"])),
            "template": "0￼0",
        })

    notes = []
    for p in rows_for(conn, asin, DATASET_NOTE):
        try:
            text = json.loads(p.get("json_metadata") or "{}").get("note_text", "")
        except ValueError:
            text = ""
        notes.append({
            "startPosition": position(p.get("start_position", {})),
            "endPosition": position(p.get("end_position", {})),
            "creationTime": iso(p["created_time"]),
            "lastModificationTime": iso(p.get("last_modified", p["created_time"])),
            "note": text,
        })

    conn.close()

    cache = {}
    if highlights:
        cache["annotation.personal.highlight"] = highlights
    if notes:
        cache["annotation.personal.note"] = notes
    return {"annotation.cache.object": cache}, len(highlights), len(notes)


def list_books(db_path, since_epoch=None, quiet=False):
    """List books with annotations, optionally only those touched since an epoch.

    since_epoch is in seconds; the store keeps milliseconds.

    This walks the same three tables convert() reads, not server_view alone:
    a book whose highlights are all still unsynced lives only in local_edit,
    so listing server_view would never offer it for export even though
    converting it by ASIN works fine. Notes are counted alongside highlights
    for the same reason — a book annotated with nothing but standalone notes
    is still worth exporting.
    """
    conn = sqlite3.connect(db_path)
    since_ms = None if since_epoch is None else int(since_epoch) * 1000

    counts = {}
    seen = set()  # annotation ids, so a row in two tables counts once
    for table, book_col, _ in TABLES:
        # Filter in SQL where the table has a modified_time, and fall back to
        # the timestamp inside the payload where it doesn't — skipping such a
        # table would lose books, and ignoring the filter would re-export
        # every one of them on every run.
        by_sql = since_ms is None or has_column(conn, table, MODIFIED_COLUMN)
        cols = "annotation_id, %s" % book_col
        if not by_sql:
            cols += ", serialized_payload"
        sql = "SELECT %s FROM %s WHERE dataset IN (?, ?)" % (cols, table)
        args = [DATASET_HIGHLIGHT, DATASET_NOTE]
        if since_ms is not None and by_sql:
            sql += " AND %s > ?" % MODIFIED_COLUMN
            args.append(since_ms)

        try:
            rows = conn.execute(sql, args).fetchall()
        except sqlite3.Error:
            continue

        for row in rows:
            ann_id, book_id = row[0], row[1]
            if not by_sql:
                ts = modified_ms(row[2])
                if ts is None or ts <= since_ms:
                    continue
            if ann_id in seen:
                continue
            seen.add(ann_id)
            asin = asin_of(book_id)
            if asin:
                counts[asin] = counts.get(asin, 0) + 1

    conn.close()

    for asin, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(asin if quiet else "%-36s %4d annotations" % (asin, n))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("database", help="ksdk_annotation_v1.db pulled from the Kindle")
    ap.add_argument("asin", nargs="?", help="book ASIN (the ID in the filename)")
    ap.add_argument("-o", "--output", help="write JSON here (default: <asin>.yjr.json)")
    ap.add_argument("--list", action="store_true", help="list books with highlights or notes")
    ap.add_argument("--since", type=int, metavar="EPOCH",
                    help="with --list, only books changed since this unix time")
    ap.add_argument("--quiet", action="store_true",
                    help="with --list, print bare ASINs (for scripting)")
    args = ap.parse_args()

    if args.list:
        list_books(args.database, args.since, args.quiet)
        return

    if not args.asin:
        ap.error("an ASIN is required unless --list is given")

    data, n_h, n_n = convert(args.database, args.asin)
    if not n_h and not n_n:
        print("No annotations found for %s" % args.asin, file=sys.stderr)
        sys.exit(1)

    out = args.output or ("%s.yjr.json" % args.asin)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("Wrote %d highlights and %d notes to %s" % (n_h, n_n, out))


if __name__ == "__main__":
    main()
