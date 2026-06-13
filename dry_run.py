"""
dry_run.py — Inspect chapter extraction without launching a browser.

Useful for verifying chapter count, headings, chunk estimates, and cover art
detection before committing to a multi-hour TTS run.

Usage
-----
    python dry_run.py "! Input/my_book.epub"
    python dry_run.py "! Input/my_book.epub" --stop-after "author's note"
    python dry_run.py "! Input/my_book.epub" --max-chunk 3000 --preview 500
    python dry_run.py "! Input/my_book.epub" --yes    # skip metadata confirmation
"""

import argparse
import sys
from pathlib import Path

import ebooklib
from ebooklib import epub

from epub_utils import (
    MAX_CHUNK_CHARS,
    extract_metadata,
    extract_chapters,
    chunk_text,
    extract_cover_bytes,
    parse_filename_metadata,
    confirm_metadata,
)


def main():
    parser = argparse.ArgumentParser(
        description="Dry-run EPUB chapter extraction (no browser, no TTS)."
    )
    parser.add_argument("epub",           type=Path,
                        help="Path to the .epub file")
    parser.add_argument("--stop-after",   type=str, default=None,
                        help="Stop after heading containing this text (case-insensitive)")
    parser.add_argument("--max-chunk",    type=int, default=MAX_CHUNK_CHARS,
                        help=f"Chunk size to use for estimates  (default: {MAX_CHUNK_CHARS})")
    parser.add_argument("--preview",      type=int, default=300,
                        help="Characters of first chapter to preview  (default: 300)")
    parser.add_argument("--yes", "-y",    action="store_true",
                        help="Accept parsed metadata without prompting")
    args = parser.parse_args()

    if not args.epub.exists():
        print(f"ERROR: File not found: {args.epub}")
        sys.exit(1)

    stop_after = [args.stop_after.lower()] if args.stop_after else None

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    print(f"\n{'='*62}")
    print(f"  DRY RUN: {args.epub.name}")
    print(f"{'='*62}\n")

    # ------------------------------------------------------------------
    # Metadata — filename parse + confirmation, then EPUB fallback
    # ------------------------------------------------------------------
    epub_meta     = extract_metadata(args.epub)
    filename_meta = parse_filename_metadata(args.epub)

    if filename_meta is not None:
        merged = {**epub_meta, **{k: v for k, v in filename_meta.items() if v is not None}}
        merged["cover_bytes"] = epub_meta["cover_bytes"]
        confirmed = confirm_metadata(merged, source="filename", yes=args.yes)
        if confirmed:
            confirmed["cover_bytes"] = epub_meta["cover_bytes"]
            meta = confirmed
        else:
            meta = epub_meta
    else:
        meta = epub_meta

    print(f"  Author:     {meta['author']}")
    print(f"  Title:      {meta['title']}")
    if meta.get("series"):
        print(f"  Series:     {meta['series']}  #{meta.get('series_number', '?')}")

    cover_status = "found in EPUB" if meta.get("cover_bytes") else "NOT FOUND — use --cover"
    print(f"  Cover art:  {cover_status}")

    # ------------------------------------------------------------------
    # Chapters
    # ------------------------------------------------------------------
    chapters, skipped, stopped_at = extract_chapters(args.epub, stop_after=stop_after)

    print(f"\n  Chapters extracted: {len(chapters)}")
    if stopped_at:
        print(f"  Stopped after:      '{stopped_at}'  (back matter excluded)")

    if skipped:
        print(f"  Skipped ({len(skipped)}):")
        for s in skipped:
            print(f"    - {s}")

    # ------------------------------------------------------------------
    # Chapter table
    # ------------------------------------------------------------------
    print(f"\n  {'#':<5} {'Heading':<46} {'Chars':>7}  {'Chunks':>6}")
    print(f"  {'-'*5} {'-'*46} {'-'*7}  {'-'*6}")

    total_chunks = 0
    for i, ch in enumerate(chapters, 1):
        chunks       = chunk_text(ch["text"], args.max_chunk)
        total_chunks += len(chunks)
        heading      = (ch["title"] or "(no heading)")[:45]
        print(f"  {i:<5} {heading:<46} {len(ch['text']):>7,}  {len(chunks):>6}")

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------
    est_min = total_chunks * 1.5 / 60
    print(f"\n  {'TOTAL TTS requests:':<54} {total_chunks:>5}")
    print(f"  {'Estimated run time (~90 s/chunk):':<54} ~{est_min:.0f} min")

    # ------------------------------------------------------------------
    # First-chapter preview
    # ------------------------------------------------------------------
    if chapters and args.preview > 0:
        preview = chapters[0]["text"][:args.preview]
        print(f"\n  First chapter preview ({args.preview} chars):")
        print(f"  {'-'*58}")
        print("  " + preview.replace("\n", "\n  "))

    print()


if __name__ == "__main__":
    main()
