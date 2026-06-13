# backlog.md — epub-to-audio future work

---

## Completed this session

### METADATA-01 — Filename metadata parser
- Added `parse_filename_metadata()` to `epub_utils.py`
- Handles em/en/plain dash separators, optional leading codes (`AV12 -`),
  optional trailing suffixes (`_cln`), series number formats (`#12`, `Book 12`, bare `12`)
- Falls back gracefully to EPUB DC metadata if filename doesn't match

### METADATA-02 — Interactive metadata confirmation prompt
- Added `confirm_metadata()` to `epub_utils.py`
- Numbered list display, Enter to accept, 1-4 to correct a field, S to skip to EPUB fallback
- `--yes / -y` flag for non-interactive / scripted use

### METADATA-03 — Author format standardised to Last, First
- `extract_metadata()` now normalises EPUB DC creator to `Last, First`
- Removed `author_first` / `author_last` split; single `author` field throughout
- All of `epub2audio.py`, `dry_run.py`, `epub_utils.py` updated consistently

### METADATA-04 — Series info in ID3 Album tag
- Album tag now `BookTitle (Series #NN)` instead of just series name
- Title tag format: `NNN BookTitle (Series #NN) — ChapterTitle`
- Both built from shared `build_album_tag()` helper — guaranteed consistent

### RETAG-01 — retag.py — fix already-generated MP3s
- New script for retroactively fixing tags and filenames on existing output
- Parses metadata from output folder name (handles old parenthetical format
  `Fated (Alex Verus Book 1)` as well as new `Alex Verus #01` format)
- Derives chapter title from filename suffix (`013_10` → `Chapter 13, Part 10`)
- Preserves track number from existing tag; rebuilds everything else
- `--dry-run` flag to preview all changes before committing
- `--yes` flag for batch processing multiple folders

---

## Open / future work

### VOICE-01 — Verify voice list against live Perchance DOM
- The `VOICES` dict in `epub2audio.py` was compiled from DevTools at time of writing
- Perchance may add/remove voices; worth re-checking periodically
- Could add a `--refresh-voices` flag that scrapes the live dropdown

### COVER-01 — Cover art embedding for retag.py
- `retag.py` preserves existing cover art if present but cannot add new art
- Could add `--cover` flag mirroring `epub2audio.py` behaviour
- Low priority — most already-done books have no cover anyway

### RECOVER-01 — Better recovery UX
- `--start-chapter` / `--start-chunk` works but requires looking up chapter numbers
  from `dry_run.py` output manually
- Could add a `--resume` flag that auto-detects the last successfully written MP3
  and picks up from the next chunk

### BATCH-01 — Batch processing multiple EPUBs
- Currently processes one EPUB per invocation
- Could add a `--batch` mode that walks `! Input/` and processes all EPUBs,
  prompting for metadata confirmation on each one before starting TTS

### QUALITY-01 — Post-generation audio quality check
- No validation that generated MP3s are non-corrupt / non-silent
- Could add a `--verify` pass that checks file size and optionally decodes
  a few seconds of audio to confirm it's not a failed generation
