# Sideloaded books: PDF and AZW3 highlights

Working notes from 2026-10-02, for picking this up later. Nothing here is
finished except where it says so.

## The problem

The Kindle stores every highlight as a position only, never the text, in the
account-wide annotation database. For purchased/Send-to-Kindle books the
script resolves positions to text from the `.kfx` file. Books you copy onto
the Kindle yourself ("sideloaded") don't fit that path, for two reasons:

1. **No ASIN.** The database keys them by a UUID, not an ASIN, and the book
   filename doesn't contain it, so `find_book` can't match them.
2. **Different position formats.** `extract_highlights_kfxlib.py` only
   understands KFX.

`ksdk_to_krds.py` needs no change: `asin_of()` takes the text before the first
`-`, so a UUID becomes its first segment (`48d27460`, `7ec92461`) and the
existing queries already find the highlights.

## PDF: written, tested, not committed

State at the time of writing: code is in the working tree but **uncommitted**
(`extract_highlights_pdf.py`, edits to `extract-kindle-highlights.sh`, and
`*.pdf` in `.gitignore`). A real run against the Kindle resolved both test
highlights correctly.

How it works:

- **Finding the file.** The `.sdr` folder next to the PDF holds a subfolder
  named after the UUID (`48d27460-15b5-...`). `find_book` globs for
  `<sdr>/<asin>-*` and takes the `.pdf` that shares the `.sdr`'s name.
- **Position format.** `shortPosition = page << 16 | character offset`. The
  page is the 0-based index into the PDF, so Kindle page 10 is `pages[10]`
  and displays as "page 11".
- **Resolving text.** Slice `pypdf`'s page text at the offsets.
- **The 4-character shift.** The Kindle's offsets run a constant 4 characters
  ahead of `pypdf`'s text, so the slice is `[start - 4, end - 4)`. Constant
  `OFFSET_SHIFT` in `extract_highlights_pdf.py`.

Open questions:

- **Is the shift 4 for every PDF?** Measured on two highlights, on two pages,
  in one PDF. The cause is unknown. Another PDF may need a different value.
  **Test: highlight a passage in a second PDF and check the output.**
- A highlight comes out with one trailing character the user didn't select
  (a ".", in one case). Probably the end offset being inclusive. Minor.
- **Scanned PDFs** (no text layer) can't work without OCR.
- Multi-page highlights are handled in code but untested.

## AZW3 / MOBI: investigated, nothing written

Test book: a sideloaded `The Seventh Sense ... .azw3` sitting directly in
`documents/` (not in `Downloads/`). The script printed "No book file on
device for 7ec92461".

What I found:

- **Why it isn't found.** The database ID is
  `7ec92461-166e-42c9-968e-adcafc377216`. That UUID isn't in the filename or
  in the `.sdr` folder (which only holds `*.azw3f`, `*.azw3r`, `*.cache.db`).
  It is stored inside the book, in **EXTH record 113**. So matching means
  opening each `.azw3`/`.mobi` and comparing its EXTH 113 to the ID. The
  database book ID also contains the content type and a guid, e.g.
  `...-EBOK-0316285064_(N):E14865F0-0` (EXTH 503/504 area), which could be a
  second way to match.
- **Position format.** Positions are byte offsets into the book's
  decompressed text stream, which is raw HTML. The stored highlight was
  `4578`..`4704`. I decompressed the text (PalmDOC compression, compression
  type 2, trailing-entry flags `0x3`) and sliced there. It lands 10 to 16
  characters before the first sentence of the introduction. The offset is
  close but not exact: 10 off counting characters, 16 off counting UTF-8
  bytes, and there are non-ASCII characters (curly quotes) before it.
- **Tried and ruled out:** stripping the `aid="..."` attributes made the
  position land on completely different text.
- **Best guess at what was highlighted** (unconfirmed): "Today, a fresh hammer
  is cracking our world. The demands of constant, instant connection are
  tearing at old power arrangements." That is 127 characters against a stored
  span of 126.

What's needed to finish:

1. **More ground truth.** One highlight can't tell me whether the Kindle
   counts bytes or characters, or what it does with tags. Highlight 2 to 3
   passages spread through the book, including after quotes and special
   characters, and record the exact text of each.
2. **Pin down the offset rule** against those, then write the extractor
   (probably `extract_highlights_azw3.py`, same shape as the PDF one: reuse
   `build_items` and `generate_html`).
3. **Metadata lookup** in `find_book`: read EXTH 113 from each candidate
   `.azw3`/`.mobi` and match it to the ID. Reading just the header from a file
   over MTP is cheaper than copying the whole book.
4. Strip the HTML tags to get plain text, and decide how to get a page number
   (AZW3 from calibre usually has no page list; the clippings file writes
   "page x" in that case).

## Things that will differ per book

The Kindle's rules are fixed per file format, but my reproduction of them is
only as good as the books I have checked it against.

- PDF: the Kindle's text extraction vs `pypdf`'s can disagree on ligatures,
  hyphenation, headers and odd encodings.
- AZW3: drift on non-ASCII characters grows with how many come before the
  highlight.

Mitigations worth building along with either format:

- Snap the start and end to word or sentence boundaries so a small error
  doesn't cut a word in half.
- Warn when a resolved highlight starts or ends mid-word, rather than writing
  it silently.
- Keep a short list of books checked against known highlights.

## Housekeeping

- Add the PDF support to the README once it is committed. The Python section
  already lists `pypdf`.
- A sideloaded book in `documents/` (like the azw3 above) works with the
  multi-folder scan already in the script. It was found in the right folder;
  only the ID match failed.
- A failed test run can leave the Kindle's MTP session open. If `go-mtpfs`
  reports `LIBUSB_ERROR_NOT_FOUND`, unplug and replug the Kindle, and avoid
  `pkill` or Ctrl-Z on `go-mtpfs`.
