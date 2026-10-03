#!/usr/bin/env python3
"""Extract highlight text from a sideloaded PDF using the annotation store.

The Kindle records a PDF highlight as a position only, never the text. A
position is a single integer, page << 16 | character offset into that page's
text, so resolving one means pulling the page text out of the PDF and slicing
it. Same output as extract_highlights_kfxlib.py: an .highlights.html next to
the book, plus the .highlights.json sidecar update_clippings.py reads.

Usage:
    python3 extract_highlights_pdf.py <annotations.json> <book.pdf>
"""
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

from extract_highlights_kfxlib import build_items, clean_title, generate_html

# The Kindle's offsets run a constant 4 characters ahead of the text pypdf
# extracts, so the highlight is [start - 4, end - 4). Measured on two
# highlights on different pages of one PDF, where it landed both exactly on
# the highlighted passage; the cause isn't known. If a PDF comes out shifted
# or clipped, this is the first thing to check.
OFFSET_SHIFT = 4


def split_position(pos):
    """(page index, character offset) from the store's integer position."""
    return pos >> 16, pos & 0xFFFF


def make_resolver(reader):
    cache = {}

    def page_text(i):
        if i not in cache:
            try:
                cache[i] = reader.pages[i].extract_text() or ""
            except IndexError:
                cache[i] = ""
        return cache[i]

    def resolve_text(start, end):
        p0, a = split_position(start)
        p1, b = split_position(end)
        a = max(0, a - OFFSET_SHIFT)
        b = max(0, b - OFFSET_SHIFT)
        if p0 == p1:
            text = page_text(p0)[a:b]
        else:
            # A highlight can run across a page break.
            parts = [page_text(p0)[a:]]
            parts += [page_text(p) for p in range(p0 + 1, p1)]
            parts.append(page_text(p1)[:b])
            text = " ".join(parts)
        return " ".join(text.split())

    return resolve_text


def book_details(reader, path):
    """Title, authors and year: from the PDF's metadata, else its filename.

    Sideloaded files are often named "Title -- Author -- hash -- source", so
    that's the fallback when the PDF carries no metadata of its own.
    """
    meta = reader.metadata or {}
    stem_parts = [s.strip() for s in Path(path).stem.split(" -- ")]
    title = (meta.get("/Title") or "").strip() or stem_parts[0]
    author = (meta.get("/Author") or "").strip()
    if not author and len(stem_parts) > 1:
        author = stem_parts[1]
    m = re.match(r"D:(\d{4})", meta.get("/CreationDate") or "")
    return clean_title(title, []), ([author] if author else []), (m.group(1) if m else "")


def main():
    if len(sys.argv) != 3:
        print("Usage: python extract_highlights_pdf.py <annotations.json> <book.pdf>")
        sys.exit(1)

    json_file, pdf_file = sys.argv[1], sys.argv[2]
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    ann_obj = data.get("annotation.cache.object", {})
    annotations = ann_obj.get("annotation.personal.highlight", [])
    notes = ann_obj.get("annotation.personal.note", [])
    if not annotations and not notes:
        print("No highlights or notes found in annotation data.")
        return

    reader = PdfReader(pdf_file)
    title, authors, year = book_details(reader, pdf_file)

    items = build_items(
        annotations, notes,
        make_resolver(reader),
        # The Kindle's page is the PDF's physical page, 1-based.
        lambda pos: str(split_position(pos)[0] + 1),
        # A PDF has no table of contents we can resolve positions against.
        lambda pos: (None, None))

    output_html = Path(pdf_file).with_suffix(".highlights.html")
    generate_html(title, authors, items, output_html, year)
    print(f"\nSaved HTML highlights to {output_html}")

    output_json = Path(pdf_file).with_suffix(".highlights.json")
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump({"title": title, "authors": authors, "highlights": items},
                  f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
