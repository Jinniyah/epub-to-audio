# CLAUDE.md — epub-to-audio session continuity reference

This file is for future Claude instances picking up work on this project.
Read it before making any changes.

---

## Project overview

**epub-to-audio** converts EPUB books into fully-tagged MP3 audiobooks using the
free, browser-based Perchance Text-to-Audiobook TTS engine — no API key, no
subscription. Selenium drives Chrome; audio is extracted from browser-side blob
URLs via JavaScript fetch().

**Repo location:** `C:\Users\jinni\source\repos\epub-to-audio`

---

## File structure

```
epub-to-audio/
├── epub_utils.py    # shared parsing, chunking, filename metadata, confirm prompt
├── epub2audio.py    # main TTS pipeline — Selenium + ID3 tagging
├── dry_run.py       # diagnostic — chapter table without browser/TTS
├── retag.py         # one-off fixer for already-generated MP3s
├── docs/
│   ├── CLAUDE.md    # this file
│   └── backlog.md   # ticketed future work
├── ! Input/         # drop EPUBs here
└── ! Output/        # generated audiobook folders land here
```

---

## Key design decisions (locked in)

### Author format
Always `Last, First` — in filenames, folder names, and ID3 tags — for
consistent library search and filesystem sorting. The EPUB DC `creator` field
is normalised to this format in `extract_metadata()`.

### Metadata resolution order (epub2audio.py)
1. EPUB internal DC metadata (baseline — always read)
2. Filename parse + interactive user confirmation (fills gaps, user can correct)
3. CLI flags `--author / --title / --series / --series-number` (always win)

### Filename patterns recognised by `parse_filename_metadata()`
The parser in `epub_utils.py` handles:
- Optional leading codes: `AV12 -`
- Em-dash `—`, en-dash `–`, plain hyphen `-` as field separators
- Series number formats: `#12`, `Book 12`, bare trailing `12`
- Optional trailing suffixes: `_cln`, `_edit`, etc.

### ID3 tags written by `epub2audio.py`
| Tag | Content |
|---|---|
| Title | `NNN BookTitle (Series #NN) — ChapterHeading` |
| Artist | `Last, First` |
| Album | `BookTitle (Series #NN)` |
| Track | `N/Total` |
| Cover | Extracted from EPUB or `--cover` |

Chapter headings in new output come from EPUB `<h1>`/`<h2>`/`<h3>` elements
(e.g. "Prologue", "Dedication", "Author's Note") — not just "Chapter N".

### retag.py — chapter title derivation
For already-generated files the chapter title is rebuilt from the **filename
suffix** (not the old tag), since old tags had unreliable chapter text:
- `013_10.mp3` → `Chapter 13, Part 10`
- `003.mp3` → `Chapter 3`

---

## Shared utilities in epub_utils.py

- `parse_filename_metadata(epub_path)` — parses author/title/series/number from filename
- `parse_folder_metadata(folder)` — same but for output folder names (retag.py)
- `confirm_metadata(meta, source, yes)` — interactive numbered confirmation prompt
- `extract_metadata(epub_path)` — reads EPUB DC metadata, normalises to Last, First
- `extract_chapters(epub_path, stop_after)` — spine-order chapter extraction
- `chunk_text(text, max_chars)` — splits text for TTS requests
- `build_basename(meta)` — builds `Last, First — Series #NN — Title` folder/file name
- `sanitize(s)` — strips illegal filename characters

---

## Common CLI patterns

```bash
# Inspect before committing
python dry_run.py "! Input/Jacka, Benedict — Alex Verus #12 — Risen_cln.epub"

# Full run
python epub2audio.py "! Input/Jacka, Benedict — Alex Verus #12 — Risen_cln.epub"

# Non-interactive
python epub2audio.py book.epub --yes

# Resume after interruption
python epub2audio.py book.epub --start-chapter 5 --start-chunk 2

# Fix already-generated MP3s
python retag.py "! Output/AV01 - Benedict, Jacka — Fated (Alex Verus Book 1)" --dry-run
python retag.py "! Output/AV01 - Benedict, Jacka — Fated (Alex Verus Book 1)" --yes
```

---

## Dependencies

```bash
pip install ebooklib beautifulsoup4 mutagen selenium webdriver-manager
```

Python 3.10+ required (uses `X | Y` union type hints).

---

## Known gotchas

- All interactive elements on the Perchance page live inside `iframe#outputIframeEl` —
  driver context must be switched into the frame before every interaction.
- Blob URLs are browser-memory-only; must be fetched via `execute_async_script`
  with a JavaScript `fetch()` + `FileReader` → base64 chain.
- Voice selection must happen after `#statusEl` shows "ready to generate"
  (~5–15 s after page load) — the dropdown isn't populated before that.
- `webdriver-manager` handles ChromeDriver automatically; no manual download needed.
