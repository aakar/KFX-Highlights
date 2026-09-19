# KFX Highlights

Pull Kindle highlights off the device over USB, resolve them to real text, and
mail them to Readwise.

Most of the documents I read on my Kindle are sent via "Send to Kindle" so that
I can read them on other devices. There's no built-in way to export those
synced highlights, so this does it directly from the device's own storage.

## How it works

The Kindle stores a highlight as a *position range*, not as text. Getting
readable output means combining two things:

1. **The annotations** — where each highlight starts and ends.
2. **The book** — the `.kfx` file, so those positions can be resolved to words.

`extract-kindle-highlights.sh` mounts the Kindle, finds books whose highlights
changed since the last run, copies the annotations and the book off the device,
resolves the text, and emails the result to `add@readwise.io` via Mail.app.

### Where annotations live

This is the part that changed, and it's worth understanding because it will
probably change again.

**Before firmware ~5.19 (roughly up to July 2026)** each book had its own
sidecar file next to it:

```
documents/Downloads/Items01/<Title>_<ASIN>.sdr/<Title>_<ASIN><hash>.yjr
```

That `.yjr` is a Kindle Reader Data Store file, decoded by `krds.py`.

**After that**, annotations moved into a single account-wide SQLite database:

```
system/ksdk/.annotations/amzn1.account.<ACCOUNT_ID>/ksdk_annotation_v1.db
```

The `.yjr` sidecars are *still written*, but their annotation container is
empty — every book now gets a byte-identical 233-byte template holding only
font preferences and sync state. So the old approach kept running and kept
finding nothing, for every book read after the switch.

`ksdk_to_krds.py` reads the new database and emits the same JSON shape
`krds.py` produced from a `.yjr`, so the downstream extractor didn't need to
change. The script reads the database first and falls back to `.yjr` sidecars
for older books, skipping any book it already handled.

**If highlights silently stop appearing again**, the giveaway is
`documents/My Clippings.txt`. It's the oldest and simplest writer on the
device, and it stopped on the same day the sidecars did. When it goes quiet at
the same moment, the storage location moved rather than something breaking.
Check whether `ksdk_annotation_v1.db` still exists at that path or whether
there's now a `_v2`.

## Requirements

### Mounting the Kindle on macOS

macOS has no native MTP support, so this needs two pieces:

1. **[macFUSE](https://macfuse.io/)** — filesystem support. Install the
   package, then approve the system extension in
   *System Settings → Privacy & Security* and reboot. Tested with 5.2.0.

2. **[go-mtpfs](https://github.com/hanwen/go-mtpfs)** — the MTP filesystem
   itself:

   ```
   go install github.com/hanwen/go-mtpfs@latest
   ```

   Put the binary somewhere on your `PATH` (this setup uses
   `/usr/local/bin/go-mtpfs`).

3. **A mount point:**

   ```
   mkdir -p ~/mnt/temp
   ```

The Kindle must be **plugged in, powered on, and past its lock screen** —
MTP does not expose storage while the device is locked.

To mount and unmount by hand:

```
go-mtpfs ~/mnt/temp &
umount ~/mnt/temp
```

### Python

Python 3 (tested on 3.13). Install the libraries `kfxlib` needs:

```
pip install pillow pypdf lxml beautifulsoup4
```

`kfxlib` itself comes from jhowell's KFX Input plugin and is bundled here as
`KFX Input.zip` — nothing to install. `sqlite3` is in the standard library.

### Mail

The script sends through **Mail.app** via AppleScript, so Mail must be
configured with a working account. The first run will ask for automation
permission. Highlights go to `add@readwise.io` — change the address near the
bottom of `extract-kindle-highlights.sh` to send somewhere else.

## Usage

Plug in the Kindle, unlock it, and run:

```
./extract-kindle-highlights.sh
```

It handles mounting and unmounting itself. Output looks like:

```
Mounted (18 books visible)
Annotation store: ksdk_annotation_v1.db
Processing: The Creative Act_ A Way of Being - Rick Rubin_ERVDD...
Found 72 highlights:
Emailing: The Creative Act_ ... .highlights.html

===== Summary =====
Emailed:       1
No highlights: 0
```

`.last_run` records when the last successful run finished; only books with
highlights newer than that are processed. Delete it to re-export everything,
or set it back to a specific Unix timestamp to re-send a narrower window.

### Doing it by hand

Inspect the annotation database without sending anything — copy it off the
device first:

```
python3 ksdk_to_krds.py ksdk_annotation_v1.db --list
```

Convert one book's annotations, then resolve them against the `.kfx`:

```
python3 ksdk_to_krds.py ksdk_annotation_v1.db <ASIN> -o book.json
python3 extract_highlights_kfxlib.py book.json book.kfx
```

That writes `<book>.highlights.html` and prints every highlight.

For a legacy `.yjr` sidecar, the original path still works:

```
python3 extract_highlights.py <book.kfx> <annotations.yjr>
```

## Limitations

**DRM.** Highlight *positions* are readable for every book, but turning them
into text requires decoding the `.kfx`, and purchased books are encrypted.
`kfxlib` raises `KFXDRMError` and the book is skipped. Books sent via
"Send to Kindle" are not affected. Known-DRM ASINs are listed in `DRM_SKIP` at
the top of the script so they're skipped quietly instead of failing each run.

**MTP is flaky.** Copies occasionally fail with `Bad file descriptor`, and the
tree sometimes isn't walkable right after mounting. The script retries copies,
verifies sizes, waits for the tree to enumerate, and refuses to advance
`.last_run` if the device never became readable — so a bad run is retried
rather than silently skipped.

## Troubleshooting

**`panic: runtime error: index out of range [0] with length 0`** from
`go-mtpfs` — no MTP device found. The Kindle is asleep, locked, on a
charge-only cable, or still booting.

**"Mounted, but no .yjr files are visible"** — the mount came up but the tree
didn't enumerate. Just run it again.

**"No highlights recorded"** for a book you know you highlighted — the
annotations aren't in the sidecar. Expected for anything read after the
firmware change; the database path handles it. If it happens for a book the
database doesn't cover either, see the `My Clippings.txt` note above.

## Credits

jhowell's [KRDS parser](https://www.mobileread.com/forums/showthread.php?t=322172)
([GitHub](https://github.com/K-R-D-S/KRDS)) decodes the `.yjr` format, and the
KFX Input plugin's `kfxlib` decodes the books. `krds.py` here tracks upstream.
