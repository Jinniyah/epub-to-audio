"""
epub2audio.py — Convert an EPUB to audiobook MP3s via Perchance Text-to-Audiobook.

Usage
-----
    python epub2audio.py book.epub
    python epub2audio.py book.epub -o ./my-audiobooks
    python epub2audio.py book.epub --series "Dune" --series-number 1
    python epub2audio.py book.epub --headed             # show browser (good for debugging)
    python epub2audio.py book.epub --cover cover.jpg    # override cover art
    python epub2audio.py book.epub --stop-after "author's note"
    python epub2audio.py book.epub --voice af_nova      # choose TTS voice
    python epub2audio.py book.epub --list-voices        # print all available voices

    # Recovery — resume after an interruption:
    python epub2audio.py book.epub --start-chapter 5
    python epub2audio.py book.epub --start-chapter 5 --start-chunk 2

    --start-chapter and --start-chunk are 1-based and match the chapter/chunk
    numbers shown in the dry_run.py output.  All earlier tracks are skipped
    without opening a browser.  The track counter still starts from 1 so that
    existing ID3 TRCK tags on already-completed files remain consistent.

Install dependencies
--------------------
    pip install ebooklib beautifulsoup4 mutagen selenium webdriver-manager

Note: `requests` is NOT needed — the generated MP3 is a browser-side blob URL
and must be retrieved via JavaScript executed inside the Selenium session.
"""

import argparse
import base64
import logging
import sys
import time
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, APIC, TIT2, TPE1, TALB, TRCK

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

from epub_utils import (
    MAX_CHUNK_CHARS,
    DEFAULT_STOP_AFTER,
    extract_metadata,
    extract_chapters,
    chunk_text,
    build_basename,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Perchance constants  (confirmed from DevTools screenshots)
# ---------------------------------------------------------------------------

PERCHANCE_URL = "https://perchance.org/text-to-audiobook"

IFRAME_ID       = "outputIframeEl"   # outer page iframe that wraps the app
TEXTAREA_ID     = "inputEl"          # text input inside the iframe
BTN_ID          = "generateBtn"      # generate button inside the iframe
STATUS_ID       = "statusEl"         # status / progress text
AUDIO_DIV_ID    = "finalAudioDiv"    # container that appears after generation
VOICE_SELECT_ID = "voiceSelectEl"    # voice <select> dropdown (confirmed)

# Confirmed DOM after generation:
#   #finalAudioDiv > table > tbody
#     > tr > td > <audio src="blob:...">
#     > tr > td > <a href="blob:..." download="audiobook.mp3">
DOWNLOAD_SEL = 'a[download="audiobook.mp3"]'
AUDIO_SEL    = f"#{AUDIO_DIV_ID} audio"

SUCCESS_TEXT  = "Success! Download the audiobook"  # substring of #statusEl after done
READY_TEXT    = "ready to generate"                # substring of #statusEl after init

WAIT_PAGE     = 30    # seconds — page / iframe load
WAIT_READY    = 60    # seconds — model initialisation ceiling
WAIT_AUDIO    = 600   # seconds — TTS generation ceiling (slow GPU = slow render)
POLL_SECS     = 1     # seconds between ready-check polls
READY_FALLBACK = 10   # seconds to wait if ready text never appears

# ---------------------------------------------------------------------------
# Available voices  (confirmed from DevTools — select#voiceSelectEl options)
# ---------------------------------------------------------------------------
# Keys are the option value= attributes; values are human-readable labels.
# Used for --list-voices and for fuzzy matching --voice input.

VOICES: dict[str, str] = {
    # Female  en-us
    "af_heart":    "Heart (Female, en-us)",
    "af_alloy":    "Alloy (Female, en-us)",
    "af_aoede":    "Aoede (Female, en-us)",
    "af_bella":    "Bella (Female, en-us)",
    "af_jessica":  "Jessica (Female, en-us)",
    "af_kore":     "Kore (Female, en-us)",
    "af_nicole":   "Nicole (Female, en-us)",
    "af_nova":     "Nova (Female, en-us)",
    "af_river":    "River (Female, en-us)",
    "af_sarah":    "Sarah (Female, en-us)",
    "af_sky":      "Sky (Female, en-us)",
    # Male  en-us
    "am_adam":     "Adam (Male, en-us)",
    "am_echo":     "Echo (Male, en-us)",
    "am_eric":     "Eric (Male, en-us)",
    "am_fenrir":   "Fenrir (Male, en-us)",
    "am_liam":     "Liam (Male, en-us)",
    "am_michael":  "Michael (Male, en-us)",
    "am_onyx":     "Onyx (Male, en-us)",
    "am_puck":     "Puck (Male, en-us)",
    "am_santa":    "Santa (Male, en-us)",
    # Female  en-gb
    "bf_alice":    "Alice (Female, en-gb)",
    "bf_emma":     "Emma (Female, en-gb)",
    "bf_isabella": "Isabella (Female, en-gb)",
    "bf_lily":     "Lily (Female, en-gb)",
    # Male  en-gb
    "bm_daniel":   "Daniel (Male, en-gb)",
    "bm_fable":    "Fable (Male, en-gb)",
    "bm_george":   "George (Male, en-gb)",
    "bm_lewis":    "Lewis (Male, en-gb)",
}

DEFAULT_VOICE = "af_heart"   # Perchance default


def resolve_voice(raw: str) -> str:
    """
    Accept either an exact voice key (e.g. 'af_nova') or a case-insensitive
    substring of the label (e.g. 'nova', 'george', 'male en-gb').

    Returns the exact option value string, or raises ValueError with a hint.
    """
    key = raw.strip().lower()

    # Exact match first
    if key in VOICES:
        return key

    # Substring match against key or label
    matches = [
        v for v, label in VOICES.items()
        if key in v or key in label.lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        options = ", ".join(f"{v} ({VOICES[v]})" for v in matches)
        raise ValueError(
            f"'{raw}' is ambiguous — matches: {options}\n"
            "Use a more specific name or the exact voice key."
        )
    raise ValueError(
        f"Unknown voice '{raw}'.  Run with --list-voices to see all options."
    )


# ---------------------------------------------------------------------------
# Selenium wrapper
# ---------------------------------------------------------------------------

class PerchanceTTS:
    """
    Drives the Perchance Text-to-Audiobook page via Selenium.

    All interactive elements live inside iframe#outputIframeEl — we must
    switch the driver context into the frame before every interaction.
    """

    def __init__(self, headed: bool = False, voice: str = DEFAULT_VOICE):
        self.voice = voice

        opts = webdriver.ChromeOptions()
        if not headed:
            opts.add_argument("--headless=new")

        # Anti-bot-detection
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        # Stability
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1280,900")

        self.driver = webdriver.Chrome(
            service=ChromeService(ChromeDriverManager().install()),
            options=opts,
        )
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
        )
        # Blob downloads can be large — give async JS up to 2 minutes
        self.driver.set_script_timeout(120)
        self._outer = WebDriverWait(self.driver, WAIT_PAGE)

    # ------------------------------------------------------------------
    # Frame helpers
    # ------------------------------------------------------------------

    def _enter_frame(self):
        self.driver.switch_to.default_content()
        frame = self._outer.until(EC.presence_of_element_located((By.ID, IFRAME_ID)))
        self.driver.switch_to.frame(frame)

    def _wait(self, timeout: int = WAIT_PAGE) -> WebDriverWait:
        return WebDriverWait(self.driver, timeout)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self):
        log.info("Opening Perchance...")
        self.driver.get(PERCHANCE_URL)
        time.sleep(3)          # allow iframe DOM to exist before switching
        self._enter_frame()
        self._wait().until(EC.presence_of_element_located((By.ID, TEXTAREA_ID)))

        # Wait for model initialisation before touching any controls
        self._wait_for_ready()

        # Set voice now that the page is fully ready
        if self.voice != DEFAULT_VOICE:
            self._set_voice(self.voice)

        log.info("Page ready.  Voice: %s", VOICES.get(self.voice, self.voice))

    def _wait_for_ready(self):
        """
        Poll #statusEl until it contains 'ready to generate', which Perchance
        sets after its TTS model finishes loading (typically 5–15 s).

        Falls back to a hard READY_FALLBACK-second sleep if the status text
        never matches — so we never hang on an unexpected message.
        """
        log.info("  Waiting for model to initialise...")
        deadline = time.time() + WAIT_READY

        while time.time() < deadline:
            try:
                status = self.driver.find_element(By.ID, STATUS_ID).text.lower()
                if READY_TEXT in status:
                    log.info("  Model ready.")
                    return
            except NoSuchElementException:
                pass
            time.sleep(POLL_SECS)

        # Status never matched — fall back to a timed wait
        log.warning(
            "  'Ready' status not detected within %ds — "
            "waiting %ds as fallback before continuing.",
            WAIT_READY, READY_FALLBACK,
        )
        time.sleep(READY_FALLBACK)

    def _set_voice(self, voice_key: str):
        """Select the requested voice in the dropdown and fire a change event."""
        try:
            el = self._wait().until(
                EC.presence_of_element_located((By.ID, VOICE_SELECT_ID))
            )
            Select(el).select_by_value(voice_key)
            self.driver.execute_script(
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", el
            )
            log.info("  Voice set: %s", VOICES.get(voice_key, voice_key))
        except Exception as exc:
            log.warning("  Could not set voice '%s': %s", voice_key, exc)

    def close(self):
        self.driver.quit()

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_mp3_bytes(self, text: str, retries: int = 3) -> bytes:
        """Submit *text*, wait for audio, return MP3 bytes.  Retries on failure."""
        for attempt in range(1, retries + 1):
            try:
                return self._do_generate(text)
            except Exception as exc:
                log.warning("Attempt %d/%d failed: %s", attempt, retries, exc)
                if attempt == retries:
                    raise
                time.sleep(10)
                # Full page reload — wait for ready and re-apply voice
                self.driver.get(PERCHANCE_URL)
                time.sleep(3)
                self._enter_frame()
                self._wait().until(EC.presence_of_element_located((By.ID, TEXTAREA_ID)))
                self._wait_for_ready()
                if self.voice != DEFAULT_VOICE:
                    self._set_voice(self.voice)

    def _do_generate(self, text: str) -> bytes:
        # Populate textarea via JS (send_keys is extremely slow for long text)
        ta = self._wait().until(EC.presence_of_element_located((By.ID, TEXTAREA_ID)))
        self.driver.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input',  {bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            ta, text,
        )

        btn = self._wait().until(EC.element_to_be_clickable((By.ID, BTN_ID)))
        btn.click()
        log.info("  Generation started...")

        blob_url = self._poll_for_blob()
        return self._fetch_blob(blob_url)

    def _poll_for_blob(self) -> str:
        """Poll until the download blob URL appears; return it."""
        deadline = time.time() + WAIT_AUDIO

        while time.time() < deadline:
            time.sleep(POLL_SECS)

            # Check status text for errors or completion
            try:
                status_text = self.driver.find_element(By.ID, STATUS_ID).text
                if "error" in status_text.lower():
                    raise RuntimeError(f"Perchance error: {status_text}")
                if SUCCESS_TEXT.lower() in status_text.lower():
                    log.info("  Status: %s", status_text.strip())
            except NoSuchElementException:
                pass

            # Primary: confirmed <a download="audiobook.mp3" href="blob:...">
            try:
                href = self.driver.find_element(
                    By.CSS_SELECTOR, DOWNLOAD_SEL
                ).get_attribute("href")
                if href and href.startswith("blob:"):
                    log.info("  Blob URL found.")
                    return href
            except NoSuchElementException:
                pass

            # Fallback: <audio src="blob:..."> inside #finalAudioDiv
            try:
                src = self.driver.find_element(
                    By.CSS_SELECTOR, AUDIO_SEL
                ).get_attribute("src")
                if src and src.startswith("blob:"):
                    log.info("  Blob URL found (audio element).")
                    return src
            except NoSuchElementException:
                pass

            log.info("    Still generating...")

        raise TimeoutException(f"Audio not ready after {WAIT_AUDIO}s")

    def _fetch_blob(self, blob_url: str) -> bytes:
        """
        Blob URLs exist only in browser memory — fetch() inside the page is the
        only way to read them.  We base64-encode the result and decode in Python.
        """
        log.info("  Downloading blob...")
        js = """
        const [url, cb] = arguments;
        fetch(url)
          .then(r => r.blob())
          .then(b => {
            const fr = new FileReader();
            fr.onloadend = () => cb(fr.result.split(',')[1]);
            fr.readAsDataURL(b);
          })
          .catch(e => cb('ERROR:' + e));
        """
        b64 = self.driver.execute_async_script(js, blob_url)
        if not b64 or b64.startswith("ERROR:"):
            raise RuntimeError(f"Blob fetch failed: {b64}")
        return base64.b64decode(b64)


# ---------------------------------------------------------------------------
# ID3 tagging
# ---------------------------------------------------------------------------

def apply_tags(
    mp3_path: Path,
    meta: dict,
    track_number: int,
    total_tracks: int,
    chapter_title: str = "",
    cover_bytes: bytes | None = None,
    cover_path: Path | None = None,
):
    """Write ID3v2.3 tags (title, artist, album, track, cover) to an MP3."""
    try:
        tags = ID3(str(mp3_path))
    except ID3NoHeaderError:
        tags = ID3()

    display = chapter_title or f"Chapter {track_number}"
    author  = f"{meta['author_first']} {meta['author_last']}".strip()
    album   = meta.get("series") or meta.get("title", "")

    tags.add(TIT2(encoding=3, text=f"{track_number:03} {meta['title']} — {display}"))
    tags.add(TPE1(encoding=3, text=author))
    tags.add(TALB(encoding=3, text=album))
    tags.add(TRCK(encoding=3, text=f"{track_number}/{total_tracks}"))

    art: bytes | None = None
    mime = "image/jpeg"
    if cover_path and cover_path.exists():
        art  = cover_path.read_bytes()
        mime = "image/png" if cover_path.suffix.lower() == ".png" else "image/jpeg"
    elif cover_bytes:
        art = cover_bytes

    if art:
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=art))

    tags.save(str(mp3_path), v2_version=3)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def process_epub(
    epub_path: Path,
    output_dir: Path,
    cover_path: Path | None = None,
    series: str | None = None,
    series_number: str | None = None,
    headed: bool = False,
    stop_after: list[str] | None = None,
    max_chunk: int = MAX_CHUNK_CHARS,
    start_chapter: int = 1,
    start_chunk: int = 1,
    voice: str = DEFAULT_VOICE,
):
    meta = extract_metadata(epub_path)
    meta["series"]        = series
    meta["series_number"] = series_number

    chapters, _, _ = extract_chapters(epub_path, stop_after=stop_after)
    if not chapters:
        raise RuntimeError("No chapters extracted from EPUB.")

    base_name = build_basename(meta)
    book_dir  = output_dir / base_name
    book_dir.mkdir(parents=True, exist_ok=True)

    log.info("Book:     %s", epub_path.name)
    log.info("Output:   %s", book_dir)
    log.info("Chapters: %d", len(chapters))
    log.info("Voice:    %s", VOICES.get(voice, voice))

    cover_bytes = None if cover_path else meta.get("cover_bytes")
    if cover_bytes:
        log.info("Cover:    found in EPUB")
    elif cover_path:
        log.info("Cover:    %s", cover_path)
    else:
        log.warning("Cover:    none — MP3s will have no artwork")

    total_tracks = sum(len(chunk_text(ch["text"], max_chunk)) for ch in chapters)
    log.info("Chunks:   %d total TTS requests", total_tracks)

    if start_chapter > len(chapters):
        raise ValueError(
            f"--start-chapter {start_chapter} exceeds chapter count ({len(chapters)})"
        )

    if start_chapter > 1 or start_chunk > 1:
        log.info(
            "Recovery mode: starting at chapter %d, chunk %d",
            start_chapter, start_chunk,
        )

    # Pre-count track_num to the start point for consistent TRCK tags
    track_num = 0
    for ch_idx, chapter in enumerate(chapters, start=1):
        chunks = chunk_text(chapter["text"], max_chunk)
        for ck_idx in range(1, len(chunks) + 1):
            track_num += 1
            if ch_idx == start_chapter and ck_idx == start_chunk:
                track_num -= 1
                break
        else:
            continue
        break

    tts_opened = False
    tts: PerchanceTTS | None = None

    try:
        for ch_idx, chapter in enumerate(chapters, start=1):
            chunks = chunk_text(chapter["text"], max_chunk)

            for ck_idx, chunk in enumerate(chunks, start=1):
                track_num += 1

                if ch_idx < start_chapter or (
                    ch_idx == start_chapter and ck_idx < start_chunk
                ):
                    log.debug("  Skipping ch %d ck %d (before start point)", ch_idx, ck_idx)
                    continue

                filename = (
                    f"{base_name} - {ch_idx:03}_{ck_idx}.mp3"
                    if len(chunks) > 1
                    else f"{base_name} - {ch_idx:03}.mp3"
                )
                mp3_path = book_dir / filename

                if mp3_path.exists() and mp3_path.stat().st_size > 1024:
                    log.info("  Skipping (exists): %s", filename)
                    continue

                if not tts_opened:
                    tts = PerchanceTTS(headed=headed, voice=voice)
                    tts.open()
                    tts_opened = True

                log.info(
                    "  [ch %d/%d  ck %d/%d  track %d/%d]  %s",
                    ch_idx, len(chapters),
                    ck_idx, len(chunks),
                    track_num, total_tracks,
                    filename,
                )
                mp3_bytes = tts.generate_mp3_bytes(chunk)

                mp3_path.write_bytes(mp3_bytes)
                apply_tags(
                    mp3_path, meta,
                    track_number=track_num,
                    total_tracks=total_tracks,
                    chapter_title=chapter["title"],
                    cover_bytes=cover_bytes,
                    cover_path=cover_path,
                )
                log.info("  Saved: %s", filename)

    finally:
        if tts is not None:
            tts.close()

    log.info("Done. %d track(s) -> %s", track_num, book_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT = Path(__file__).parent / "! Output"


def main():
    parser = argparse.ArgumentParser(
        description="Convert an EPUB to audiobook MP3s via Perchance TTS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voice examples:
  --voice af_nova          exact voice key
  --voice nova             name substring (unambiguous)
  --voice "male, en-gb"    label substring
  --list-voices            print all available voices and exit

Recovery examples:
  --start-chapter 5                resume from chapter 5, chunk 1
  --start-chapter 5 --start-chunk 2   resume from chapter 5, chunk 2

Chapter and chunk numbers match the dry_run.py output.
        """,
    )
    parser.add_argument("epub",              type=Path,
                        help="Path to the .epub file")
    parser.add_argument("-o", "--output",    type=Path, default=DEFAULT_OUTPUT,
                        help=f"Output root directory  (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--cover",           type=Path, default=None,
                        help="Cover image (JPEG/PNG) to embed — overrides EPUB cover")
    parser.add_argument("--series",          type=str,  default=None,
                        help="Series name  (e.g. 'Dune')")
    parser.add_argument("--series-number",   type=str,  default=None,
                        help="Book number within the series  (e.g. '1')")
    parser.add_argument("--headed",          action="store_true",
                        help="Show the Chrome window  (recommended for first run)")
    parser.add_argument("--max-chunk",       type=int,  default=MAX_CHUNK_CHARS,
                        help=f"Max chars per TTS chunk  (default: {MAX_CHUNK_CHARS})")
    parser.add_argument("--stop-after",      type=str,  default=None,
                        help="Stop after the first chapter whose heading contains "
                             "this text (case-insensitive).")
    parser.add_argument("--start-chapter",   type=int,  default=1,
                        help="Chapter number to resume from (1-based).  Default: 1")
    parser.add_argument("--start-chunk",     type=int,  default=1,
                        help="Chunk within --start-chapter to resume from (1-based).  "
                             "Default: 1")
    parser.add_argument("--voice",           type=str,  default=DEFAULT_VOICE,
                        help=f"TTS voice key or name substring  "
                             f"(default: {DEFAULT_VOICE} — {VOICES[DEFAULT_VOICE]}).  "
                             f"Use --list-voices to see all options.")
    parser.add_argument("--list-voices",     action="store_true",
                        help="Print all available voices and exit.")

    args = parser.parse_args()

    if args.list_voices:
        col_w = max(len(k) for k in VOICES) + 2
        print(f"\n  {'Key':<{col_w}}  Label")
        print(f"  {'-'*col_w}  {'-'*30}")
        for key, label in VOICES.items():
            marker = "  ← default" if key == DEFAULT_VOICE else ""
            print(f"  {key:<{col_w}}  {label}{marker}")
        print()
        sys.exit(0)

    if not args.epub.exists():
        parser.error(f"File not found: {args.epub}")

    if args.start_chunk > 1 and args.start_chapter == 1:
        parser.error("--start-chunk requires --start-chapter to also be set")

    if args.start_chapter < 1 or args.start_chunk < 1:
        parser.error("--start-chapter and --start-chunk must be >= 1")

    try:
        voice = resolve_voice(args.voice)
    except ValueError as exc:
        parser.error(str(exc))

    process_epub(
        epub_path     = args.epub,
        output_dir    = args.output,
        cover_path    = args.cover,
        series        = args.series,
        series_number = args.series_number,
        headed        = args.headed,
        stop_after    = [args.stop_after.lower()] if args.stop_after else None,
        max_chunk     = args.max_chunk,
        start_chapter = args.start_chapter,
        start_chunk   = args.start_chunk,
        voice         = voice,
    )


if __name__ == "__main__":
    main()
