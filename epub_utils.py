"""
epub_utils.py — Shared EPUB parsing and text utilities for epub2audio.

Imported by both epub2audio.py (the TTS pipeline) and dry_run.py (the
diagnostic tool). Any change to extraction logic happens here once.
"""

import re
import logging
from pathlib import Path

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Max characters fed to Perchance per TTS request.
MAX_CHUNK_CHARS = 4_000

# Headingless documents at or below this length are labelled "Dedication"
# (covers dedications, epigraphs, etc. that have no <h1>/<h2>/<h3>).
DEDICATION_MAX_CHARS = 300

# Filename fragments that identify front/back-matter documents to skip entirely.
SKIP_FILENAMES = ("nav", "toc", "ncx", "copyright", "cover")

# Headings that signal the end of the main narrative.
# Processing stops AFTER the chapter whose normalised heading contains one of
# these substrings (case-insensitive).  The matching chapter IS included.
DEFAULT_STOP_AFTER = [
    "author's note",
    "acknowledgment",
    "acknowledgement",
    "about the author",
    "also by",
    "excerpt",
    "preview",
]

# ---------------------------------------------------------------------------
# Heading normalisation
# ---------------------------------------------------------------------------

def normalise_heading(raw: str) -> str:
    """
    Normalise an EPUB heading string for display and ID3 tagging.

    Examples
    --------
    'CHAPTER1'      -> 'Chapter 1'
    'CHAPTER 1'     -> 'Chapter 1'
    'chapter10'     -> 'Chapter 10'
    "AUTHOR'S NOTE" -> "Author's Note"   (letter after apostrophe NOT uppercased)
    'Prologue'      -> 'Prologue'

    Uses word-by-word capitalisation rather than str.title() to avoid the
    well-known Python behaviour where title() uppercases every letter that
    follows a non-alphanumeric character, including apostrophes
    (e.g. "it's" -> "It'S").
    """
    # Insert a space between a letter run and an immediately following digit
    spaced = re.sub(r'([A-Za-z])(\d)', r'\1 \2', raw.strip())

    def _cap_word(word: str) -> str:
        # Split on apostrophe; capitalise only the part before it
        parts = word.split("'")
        parts[0] = parts[0].capitalize()
        return "'".join(parts)

    return " ".join(_cap_word(w) for w in spaced.split())


# ---------------------------------------------------------------------------
# Cover extraction
# ---------------------------------------------------------------------------

def extract_cover_bytes(book) -> bytes | None:
    """
    Return raw image bytes for the cover art embedded in an ebooklib Book.

    Tries three strategies in order:
      1. Any ITEM_IMAGE whose filename contains 'cover'.
      2. The item referenced by the OPF <meta name="cover"> tag.
      3. The first <img src="..."> found inside any cover.xhtml document.

    Returns None if no cover image is found.
    """
    # Strategy 1: image item whose name contains "cover"
    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        if "cover" in item.get_name().lower():
            return item.get_content()

    # Strategy 2: OPF meta pointer
    cover_meta = book.get_metadata("OPF", "cover")
    if cover_meta:
        cover_id = cover_meta[0][1].get("content", "")
        try:
            item = book.get_item_with_id(cover_id)
            if item:
                return item.get_content()
        except Exception:
            pass

    # Strategy 3: parse cover.xhtml and follow the <img src="...">
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        if "cover" in item.get_name().lower():
            soup = BeautifulSoup(item.get_content(), "html.parser")
            img = soup.find("img")
            if img and img.get("src"):
                img_name = img["src"].lstrip("./")
                for img_item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                    if img_item.get_name().endswith(img_name):
                        return img_item.get_content()

    return None


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def extract_metadata(epub_path: Path) -> dict:
    """
    Return a metadata dict for the EPUB at epub_path.

    Keys
    ----
    title         : str
    author_first  : str
    author_last   : str
    series        : None  (populated later via CLI --series)
    series_number : None  (populated later via CLI --series-number)
    cover_bytes   : bytes | None
    """
    book = epub.read_epub(str(epub_path))

    def dc(tag: str) -> str:
        items = book.get_metadata("DC", tag)
        return items[0][0].strip() if items else ""

    title       = dc("title")   or "Unknown Title"
    author_full = dc("creator") or "Unknown Author"

    parts = author_full.split()
    if len(parts) >= 2:
        author_first = " ".join(parts[:-1])
        author_last  = parts[-1]
    else:
        author_first = author_full
        author_last  = ""

    return {
        "title":         title,
        "author_first":  author_first.strip(),
        "author_last":   author_last.strip(),
        "series":        None,
        "series_number": None,
        "cover_bytes":   extract_cover_bytes(book),
    }


# ---------------------------------------------------------------------------
# Chapter extraction
# ---------------------------------------------------------------------------

def extract_chapters(
    epub_path: Path,
    stop_after: list[str] | None = None,
) -> tuple[list[dict], list[str], str | None]:
    """
    Extract readable chapters from an EPUB in spine order.

    Parameters
    ----------
    epub_path  : Path to the .epub file.
    stop_after : List of lowercase substrings.  Processing stops AFTER the
                 first chapter whose normalised heading contains any of them.
                 Defaults to DEFAULT_STOP_AFTER.

    Returns
    -------
    chapters   : list of {"title": str, "text": str}
    skipped    : list of human-readable skip reason strings (for diagnostics)
    stopped_at : heading string that triggered the stop, or None
    """
    if stop_after is None:
        stop_after = DEFAULT_STOP_AFTER

    book      = epub.read_epub(str(epub_path))
    spine_ids = [item_id for item_id, _ in book.spine]

    chapters:  list[dict] = []
    skipped:   list[str]  = []
    stopped_at: str | None = None

    for item_id in spine_ids:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue

        name = item.get_name().lower()
        if any(frag in name for frag in SKIP_FILENAMES):
            skipped.append(name)
            continue

        soup       = BeautifulSoup(item.get_content(), "html.parser")
        heading_el = soup.find(re.compile(r"^h[1-3]$"))
        raw_title  = heading_el.get_text(strip=True) if heading_el else ""
        ch_title   = normalise_heading(raw_title) if raw_title else ""

        text = soup.get_text(separator="\n")
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()

        if len(text) < 50:
            skipped.append(f"{name} (too short: {len(text)} chars)")
            continue

        # Headingless short documents are dedications / epigraphs
        if not ch_title and len(text) <= DEDICATION_MAX_CHARS:
            ch_title = "Dedication"

        chapters.append({"title": ch_title, "text": text})

        # Stop AFTER including this chapter if its heading hits a stop marker
        if ch_title and any(marker in ch_title.lower() for marker in stop_after):
            stopped_at = ch_title
            log.info("Stop marker '%s' — back matter excluded.", ch_title)
            break

    return chapters, skipped, stopped_at


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """
    Split *text* into chunks of at most *max_chars* characters.

    Breaks preferentially on paragraph boundaries (double newline), falling
    back to sentence boundaries (after .  !  ?) when a single paragraph
    exceeds *max_chars*.
    """
    paragraphs = text.split("\n\n")
    chunks:  list[str] = []
    current: str       = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Oversized paragraph — split on sentence boundaries
        if len(para) > max_chars:
            for sentence in re.split(r"(?<=[.!?])\s+", para):
                if len(current) + len(sentence) + 2 > max_chars:
                    if current:
                        chunks.append(current.strip())
                    current = sentence
                else:
                    current = (current + " " + sentence).strip()
            continue

        if len(current) + len(para) + 2 > max_chars:
            if current:
                chunks.append(current.strip())
            current = para
        else:
            current = (current + "\n\n" + para).strip()

    if current:
        chunks.append(current.strip())

    return chunks


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def sanitize(s: str) -> str:
    """Remove characters illegal in Windows/macOS/Linux filenames."""
    return re.sub(r'[\\/:*?"<>|]', "_", s).strip()


def build_basename(meta: dict) -> str:
    """
    Build the output folder / file base name from book metadata.

    Format (no series):  "Last, First — Title"
    Format (series):     "Last, First — Series #NN — Title"
    """
    author_last   = sanitize(meta.get("author_last",   ""))
    author_first  = sanitize(meta.get("author_first",  ""))
    title         = sanitize(meta.get("title",         "Unknown"))
    series        = meta.get("series")
    series_number = meta.get("series_number")

    if series and not series_number:
        series_number = "ZZ"
    elif series_number:
        try:
            series_number = f"{int(series_number):02}"
        except (ValueError, TypeError):
            pass

    if series:
        return f"{author_last}, {author_first} — {sanitize(series)} #{series_number} — {title}"
    return f"{author_last}, {author_first} — {title}"
