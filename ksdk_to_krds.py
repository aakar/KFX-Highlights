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


def rows_for(conn, asin, dataset):
    # Rows live in server_view; local_edit holds not-yet-synced changes and
    # nonsyncable_annotations holds device-only ones. Union all three so a
    # highlight made seconds ago is not missed.
    out = {}
    queries = [
        ("SELECT annotation_id, serialized_payload FROM server_view "
         "WHERE dataset = ? AND dataset_id LIKE ?", (dataset, asin + "-%")),
        ("SELECT annotation_id, serialized_payload FROM local_edit "
         "WHERE dataset = ? AND dataset_id LIKE ?", (dataset, asin + "-%")),
        ("SELECT annotation_id, serialized_payload FROM nonsyncable_annotations "
         "WHERE dataset = ? AND book_id LIKE ?", (dataset, asin + "%")),
    ]
    for sql, args in queries:
        try:
            for ann_id, payload in conn.execute(sql, args):
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
    """List books with highlights, optionally only those touched since an epoch.

    since_epoch is in seconds; the store keeps milliseconds.
    """
    conn = sqlite3.connect(db_path)
    sql = ("SELECT substr(dataset_id, 1, instr(dataset_id, '-') - 1) asin, "
           "COUNT(*) n FROM server_view WHERE dataset = ?")
    args = [DATASET_HIGHLIGHT]
    if since_epoch is not None:
        sql += " AND modified_time > ?"
        args.append(int(since_epoch) * 1000)
    sql += " GROUP BY asin ORDER BY n DESC"
    rows = conn.execute(sql, args).fetchall()
    conn.close()

    for asin, n in rows:
        print(asin if quiet else "%-36s %4d highlights" % (asin, n))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("database", help="ksdk_annotation_v1.db pulled from the Kindle")
    ap.add_argument("asin", nargs="?", help="book ASIN (the ID in the filename)")
    ap.add_argument("-o", "--output", help="write JSON here (default: <asin>.yjr.json)")
    ap.add_argument("--list", action="store_true", help="list books with highlights")
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
