"""
retag.py — Fix ID3 tags and filenames for already-generated audiobook MP3s.

Parses metadata from the output folder name, confirms with the user, then
rewrites every MP3's tags and filename to match the current epub2audio format.

Usage
-----
    python retag.py "! Output/AV01 - Benedict, Jacka — Fated (Alex Verus Book 1)"
    python retag.py "! Output/AV01 - Benedict, Jacka — Fated (Alex Verus Book 1)" --yes
    python retag.py "! Output/AV01 - Benedict, Jacka — Fated (Alex Verus Book 1)" --dry-run

What gets fixed per MP3
-----------------------
    Filename  : Benedict, Jacka — Fated (Alex Verus Book 1) - 013_10.mp3
             -> Jacka, Benedict — Alex Verus #01 — Fated - 013_10.mp3

    Title tag : 100 Fated (Alex Verus Book 1) — Chapter 100
             -> 100 Fated (Alex Verus #01) — Chapter 13, Part 10

    Title tag : 003 Fated (Alex Verus Book 1) — Dedication
             -> 003 Fated (Alex Verus #01) — Chapter 3     (single-chunk, no Part)

    Artist    : Jacka Benedict  ->  Jacka, Benedict
    Album     : Fated (Alex Verus Book 1)  ->  Fated (Alex Verus #01)

The track number prefix (NNN) comes from the existing Title tag.
The chapter title is rebuilt from the filename suffix (013_10 -> Chapter 13, Part 10).
"""

import argparse
import re
import sys
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, APIC

from epub_utils import (
    parse_filename_metadata,
    confirm_metadata,
    sanitize,
)


# ---------------------------------------------------------------------------
# Folder name parsing
# ---------------------------------------------------------------------------

def parse_folder_metadata(folder: Path) -> dict | None:
    """
    Parse author / title / series / series_number from an audiobook output
    folder name.

    Handles both old and new naming formats:

      Old:  "AV01 - Benedict, Jacka — Fated (Alex Verus Book 1)"
      New:  "Jacka, Benedict — Alex Verus #01 — Fated"
      Also: "Benedict, Jacka — Risen (An Alex Verus Novel)"
    """
    name = folder.name

    # Strip optional leading code like "AV01 - "
    name = re.sub(r"^[A-Za-z]+\d+\s*[—–-]\s*", "", name).strip()

    # Check for parenthetical series in title:
    #   "Fated (Alex Verus Book 1)"  or  "Risen (An Alex Verus Novel)"
    paren_match = re.search(
        r"^(.*?)\s*\((?:An?\s+)?(.+?)\s+(?:Book|Novel)\s*(\d+)?\s*\)$",
        name,
        re.IGNORECASE,
    )

    if paren_match:
        before_paren = paren_match.group(1).strip()
        series_name  = paren_match.group(2).strip()
        series_num   = paren_match.group(3)

        parts = re.split(r"\s*[—–]\s*|\s+-\s+", before_paren, maxsplit=1)
        if len(parts) == 2:
            author, title = parts[0].strip(), parts[1].strip()
            num_str = f" #{series_num}" if series_num else ""
            synthetic = f"{author} — {series_name}{num_str} — {title}.epub"
            return parse_filename_metadata(Path(synthetic))

    return parse_filename_metadata(Path(name + ".epub"))


# ---------------------------------------------------------------------------
# Filename suffix parsing  ->  chapter title
# ---------------------------------------------------------------------------

# Matches the trailing suffix of an MP3 stem:
#   "... - 013_10"  ->  chapter=13, part=10
#   "... - 003"     ->  chapter=3,  part=None
_SUFFIX_RE = re.compile(r"-\s*(\d+)(?:_(\d+))?$")


def chapter_title_from_stem(stem: str) -> str:
    """
    Derive a human-readable chapter title from the MP3 filename stem.

    Examples
    --------
    "Benedict, Jacka — Alex Verus #01 — Fated - 013_10"  ->  "Chapter 13, Part 10"
    "Benedict, Jacka — Alex Verus #01 — Fated - 003"     ->  "Chapter 3"
    """
    m = _SUFFIX_RE.search(stem)
    if not m:
        return "Chapter ?"

    chapter = int(m.group(1))
    part    = int(m.group(2)) if m.group(2) else None

    if part is not None:
        return f"Chapter {chapter}, Part {part}"
    return f"Chapter {chapter}"


# ---------------------------------------------------------------------------
# Title tag — track number extraction
# ---------------------------------------------------------------------------

_TRACK_NUM_RE = re.compile(r"^(\d+)\s+")


def track_number_from_tag(title_text: str) -> str | None:
    """
    Extract the zero-padded track number string from an existing Title tag.

    "100 Fated (Alex Verus Book 1) — Chapter 100"  ->  "100"
    Returns None if no leading number is found.
    """
    m = _TRACK_NUM_RE.match(title_text.strip())
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Tag builders
# ---------------------------------------------------------------------------

def build_album_tag(
    book_title: str,
    series: str | None,
    series_number: str | None,
) -> str:
    """
    Build the Album tag.  Format:  BookTitle (Series #NN)
    Example: Fated (Alex Verus #01)
    """
    if series and series_number:
        try:
            num = f"{int(series_number):02}"
        except (ValueError, TypeError):
            num = series_number
        return f"{book_title} ({series} #{num})"
    elif series:
        return f"{book_title} ({series})"
    return book_title


def build_title_tag(
    track_str: str,
    book_title: str,
    series: str | None,
    series_number: str | None,
    chapter_title: str,
) -> str:
    """
    Build the Title tag.

    Format:  NNN BookTitle (Series #NN) — ChapterTitle
    Example: 100 Fated (Alex Verus #01) — Chapter 13, Part 10
    """
    return f"{track_str} {build_album_tag(book_title, series, series_number)} — {chapter_title}"


# ---------------------------------------------------------------------------
# Filename builder
# ---------------------------------------------------------------------------

def build_new_filename(meta: dict, old_stem: str) -> str:
    """
    Build the new MP3 filename stem preserving the trailing track/chunk suffix.

    Old: "Benedict, Jacka — Fated (Alex Verus Book 1) - 013_10"
    New: "Jacka, Benedict — Alex Verus #01 — Fated - 013_10"
    """
    suffix_match = _SUFFIX_RE.search(old_stem)
    if suffix_match:
        raw = suffix_match.group(0).strip()   # e.g. "- 013_10"
        # Normalise to "- NNN" or "- NNN_M" (strip any extra spaces)
        suffix = " - " + raw.lstrip("- ").strip()
    else:
        suffix = ""

    author        = sanitize(meta.get("author", "Unknown"))
    title         = sanitize(meta.get("title",  "Unknown"))
    series        = meta.get("series")
    series_number = meta.get("series_number")

    if series and series_number:
        try:
            num = f"{int(series_number):02}"
        except (ValueError, TypeError):
            num = series_number
        base = f"{author} — {sanitize(series)} #{num} — {title}"
    elif series:
        base = f"{author} — {sanitize(series)} — {title}"
    else:
        base = f"{author} — {title}"

    return base + suffix


# ---------------------------------------------------------------------------
# Core retagging logic
# ---------------------------------------------------------------------------

def retag_folder(folder: Path, meta: dict, dry_run: bool = False) -> int:
    """
    Walk *folder*, fix every MP3's tags and filename.
    Returns the number of files processed.
    """
    mp3_files = sorted(folder.glob("*.mp3"))
    if not mp3_files:
        print(f"  No MP3 files found in: {folder}")
        return 0

    author        = meta["author"]
    title         = meta["title"]
    series        = meta.get("series")
    series_number = meta.get("series_number")
    album         = build_album_tag(title, series, series_number)

    processed = 0

    for mp3_path in mp3_files:
        # ------------------------------------------------------------------
        # Read existing tag — we only need the track number from it
        # ------------------------------------------------------------------
        try:
            tags = ID3(str(mp3_path))
        except ID3NoHeaderError:
            tags = ID3()

        existing_title = tags.get("TIT2")
        title_text     = existing_title.text[0] if existing_title else ""
        track_str      = track_number_from_tag(title_text)

        if not track_str:
            # Fallback: derive from filename position in sorted list
            track_str = f"{processed + 1:03}"
            print(f"  WARNING: No track number in tag for {mp3_path.name!r} — using {track_str}")

        # ------------------------------------------------------------------
        # Derive chapter title from filename (always — ignores old tag text)
        # ------------------------------------------------------------------
        chapter_title = chapter_title_from_stem(mp3_path.stem)

        # ------------------------------------------------------------------
        # Build new values
        # ------------------------------------------------------------------
        new_title    = build_title_tag(track_str, title, series, series_number, chapter_title)
        new_filename = build_new_filename(meta, mp3_path.stem) + ".mp3"
        new_path     = mp3_path.parent / new_filename

        # ------------------------------------------------------------------
        # Report
        # ------------------------------------------------------------------
        old_title  = title_text or "(none)"
        old_artist = tags.get("TPE1").text[0] if tags.get("TPE1") else "(none)"
        old_album  = tags.get("TALB").text[0] if tags.get("TALB") else "(none)"

        print(f"\n  {mp3_path.name}")
        if mp3_path.name != new_filename:
            print(f"    Rename  -> {new_filename}")
        else:
            print(f"    Rename  (no change)")
        print(f"    Title   {old_title!r}")
        print(f"         -> {new_title!r}")
        print(f"    Artist  {old_artist!r} -> {author!r}")
        print(f"    Album   {old_album!r} -> {album!r}")

        if dry_run:
            processed += 1
            continue

        # ------------------------------------------------------------------
        # Write tags
        # ------------------------------------------------------------------
        existing_apic = tags.get("APIC:")

        tags.delall("TIT2")
        tags.delall("TPE1")
        tags.delall("TALB")
        tags.add(TIT2(encoding=3, text=new_title))
        tags.add(TPE1(encoding=3, text=author))
        tags.add(TALB(encoding=3, text=album))
        if existing_apic:
            tags.add(existing_apic)

        tags.save(str(mp3_path), v2_version=3)

        # ------------------------------------------------------------------
        # Rename (after tags saved to original path)
        # ------------------------------------------------------------------
        if mp3_path != new_path:
            if new_path.exists():
                print(f"    WARNING: target exists, skipping rename: {new_filename}")
            else:
                mp3_path.rename(new_path)

        processed += 1

    return processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fix ID3 tags and filenames for already-generated audiobook MP3s.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python retag.py "! Output/AV01 - Benedict, Jacka — Fated (Alex Verus Book 1)"
  python retag.py "! Output/AV01 - Benedict, Jacka — Fated (Alex Verus Book 1)" --yes
  python retag.py "! Output/AV01 - Benedict, Jacka — Fated (Alex Verus Book 1)" --dry-run
        """,
    )
    parser.add_argument("folder",      type=Path,
                        help="Path to the audiobook output folder to fix")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Accept parsed metadata without prompting")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Show what would change without writing anything")
    parser.add_argument("--author",        type=str, default=None,
                        help="Override author (Last, First format)")
    parser.add_argument("--title",         type=str, default=None,
                        help="Override book title")
    parser.add_argument("--series",        type=str, default=None,
                        help="Override series name")
    parser.add_argument("--series-number", type=str, default=None,
                        help="Override series number")

    args = parser.parse_args()

    if not args.folder.exists() or not args.folder.is_dir():
        parser.error(f"Folder not found: {args.folder}")

    parsed = parse_folder_metadata(args.folder)

    if parsed is not None:
        confirmed = confirm_metadata(parsed, source="folder name", yes=args.yes)
        meta = confirmed if confirmed else {}
    else:
        print(f"\n  Could not parse metadata from folder name: {args.folder.name!r}")
        print("  Please supply values via --author / --title / --series / --series-number\n")
        meta = {}

    if args.author:        meta["author"]        = args.author
    if args.title:         meta["title"]         = args.title
    if args.series:        meta["series"]        = args.series
    if args.series_number: meta["series_number"] = args.series_number

    if not meta.get("author") or not meta.get("title"):
        print("ERROR: Could not determine author and title.")
        print("       Use --author and --title to supply them manually.")
        sys.exit(1)

    if args.dry_run:
        print("\n  *** DRY RUN — no files will be changed ***")

    print(f"\n  Folder : {args.folder}")
    print(f"  Author : {meta['author']}")
    print(f"  Title  : {meta['title']}")
    if meta.get("series"):
        print(f"  Series : {meta['series']}  #{meta.get('series_number', '?')}")
    print()

    count = retag_folder(args.folder, meta, dry_run=args.dry_run)

    print()
    if args.dry_run:
        print(f"  Dry run complete — {count} file(s) would be updated.")
    else:
        print(f"  Done — {count} file(s) updated.")
    print()


if __name__ == "__main__":
    main()
