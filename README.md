# ePub-to-Audio

Convert EPUB books into fully-tagged MP3 audiobooks using the free, browser-based
[Perchance Text-to-Audiobook](https://perchance.org/text-to-audiobook) TTS engine —
no API key, no subscription, no upload to a server.

> **Portfolio note:** This project demonstrates AI-assisted Python development,
> Selenium browser automation (including iframe context switching and in-browser
> blob URL extraction), EPUB parsing, and ID3 audio metadata tagging.

---

## How it works

1. The EPUB is parsed in spine order — chapters are extracted as clean plain text,
   dedications and back matter are handled automatically.
2. Each chapter is split into chunks (≤ 4 000 characters by default) that fit within
   Perchance's generation window.
3. Selenium drives a Chrome browser to the Perchance page, waits for the TTS model
   to finish loading, optionally selects a voice, then submits each chunk.
4. The generated audio is a **browser-side blob URL** — it never touches a server.
   The script reads it back via an async JavaScript `fetch()` call, base64-encodes it,
   and decodes it in Python.
5. Each MP3 is saved to `! Output/<Author — Title>/` and tagged with full ID3v2.3
   metadata (title, author, album, track number, and cover art).

---

## Project structure

```
ePub-to-Audio/
├── epub_utils.py    # shared EPUB parsing, chunking, and filename helpers
├── epub2audio.py    # main pipeline — Selenium + TTS + ID3 tagging
├── dry_run.py       # diagnostic tool — inspect chapters without opening a browser
├── ! Input/         # drop .epub files here
└── ! Output/        # generated audiobook folders appear here
```

---

## Requirements

- Python 3.10+
- Google Chrome (any recent version)
- The following Python packages:

```bash
pip install ebooklib beautifulsoup4 mutagen selenium webdriver-manager
```

> `webdriver-manager` handles ChromeDriver installation automatically —
> no manual driver download needed.

---

## Quick start

### 1. Inspect the book first (recommended)

```bash
python dry_run.py "! Input/My Book.epub"
```

This prints a chapter table, chunk count, estimated run time, and cover art
status — without opening a browser or generating any audio.

```
============================================================
  DRY RUN: The Kaiju Preservation Society.epub
============================================================
  Title:      The Kaiju Preservation Society
  Author:     John Scalzi
  Cover art:  found in EPUB
  Chapters extracted: 30
  Stopped after: 'Author's Note and Acknowledgments'

  #     Heading                                  Chars   Chunks
  ----- ---------------------------------------- -------  ------
  1     Dedication                                    84       1
  2     Chapter 1                               12,931       4
  ...
  TOTAL TTS requests:                              129
  Estimated run time (~90 s/chunk):              ~3 min
```

### 2. Run a headed test first

```bash
python epub2audio.py "! Input/My Book.epub" --headed
```

`--headed` keeps the Chrome window visible so you can confirm the page loads,
the voice is selected, and the first chunk generates correctly before walking away.

### 3. Full headless run

```bash
python epub2audio.py "! Input/My Book.epub"
```

Output lands in `! Output/Scalzi, John — The Kaiju Preservation Society/`.

---

## All options

```
usage: epub2audio.py [-h] [-o OUTPUT] [--cover COVER] [--series SERIES]
                     [--series-number N] [--headed] [--max-chunk N]
                     [--stop-after TEXT] [--start-chapter N] [--start-chunk N]
                     [--voice VOICE] [--list-voices]
                     epub
```

| Argument | Default | Description |
|---|---|---|
| `epub` | *(required)* | Path to the `.epub` file |
| `-o / --output` | `! Output/` | Root directory for generated audiobooks |
| `--cover` | *(EPUB cover)* | Override cover image (JPEG or PNG) embedded in MP3s |
| `--series` | — | Series name, e.g. `"Dune"` |
| `--series-number` | — | Book number within the series, e.g. `1` |
| `--headed` | headless | Show the Chrome window (recommended for first run) |
| `--max-chunk` | `4000` | Maximum characters per TTS request |
| `--stop-after` | *(see below)* | Stop after the first chapter whose heading contains this text |
| `--start-chapter` | `1` | Resume from this chapter number (1-based) |
| `--start-chunk` | `1` | Resume from this chunk within `--start-chapter` |
| `--voice` | `af_heart` | TTS voice — key or name substring (see `--list-voices`) |
| `--list-voices` | — | Print all available voices and exit |

### Back-matter truncation (`--stop-after`)

By default the script stops **after** the first chapter whose heading contains any of:

`author's note` · `acknowledgment` · `acknowledgement` · `about the author` · `also by` · `excerpt` · `preview`

This automatically strips publisher samplers bundled at the end of many EPUBs.
Override with your own phrase:

```bash
python epub2audio.py book.epub --stop-after "epilogue"
```

---

## Voices

```bash
python epub2audio.py --list-voices
```

```
  Key           Label
  ----------    ------------------------------
  af_heart      Heart (Female, en-us)  ← default
  af_alloy      Alloy (Female, en-us)
  af_nova       Nova (Female, en-us)
  am_puck       Puck (Male, en-us)
  bm_george     George (Male, en-gb)
  bf_alice      Alice (Female, en-gb)
  ...
```

The `--voice` argument accepts an exact key or a case-insensitive substring:

```bash
python epub2audio.py book.epub --voice nova        # matches af_nova
python epub2audio.py book.epub --voice af_nova     # exact key
python epub2audio.py book.epub --voice "male, en-gb"  # matches all British male voices
```

---

## Recovery after interruption

If a run is interrupted, the script can resume where it left off in two ways:

**Automatic** — on restart, any MP3 file that already exists and is larger than 1 KB
is skipped automatically. Just re-run the same command.

**Manual** — use the chapter and chunk numbers from `dry_run.py` output to jump
directly to a specific point:

```bash
# Resume from chapter 5, chunk 1
python epub2audio.py book.epub --start-chapter 5

# Resume from chapter 5, chunk 2 specifically
python epub2audio.py book.epub --start-chapter 5 --start-chunk 2
```

The global track counter is maintained correctly across recovery runs, so ID3
`TRCK` tags on all files (old and new) remain consistent.

---

## Output file naming

Files are named using a sortable, library-friendly convention:

```
! Output/
└── Scalzi, John — The Kaiju Preservation Society/
    ├── Scalzi, John — The Kaiju Preservation Society - 001.mp3      ← Dedication
    ├── Scalzi, John — The Kaiju Preservation Society - 002_1.mp3    ← Chapter 1, chunk 1
    ├── Scalzi, John — The Kaiju Preservation Society - 002_2.mp3    ← Chapter 1, chunk 2
    ...
```

For books in a series:

```
Sanderson, Brandon — The Stormlight Archive #01 — The Way of Kings - 001.mp3
```

---

## ID3 tags written

| Tag | Content |
|---|---|
| Title | `Book Title — Chapter Name` |
| Artist | Author full name |
| Album | Series name (or book title if no series) |
| Track | `N/Total` |
| Cover art | Extracted from EPUB, or supplied via `--cover` |

---

## Known limitations

- **Perchance is GPU-dependent.** Generation speed varies with server load and your
  machine's hardware. Budget 2–5 hours for a full novel at default chunk sizes.
- **Headless mode may behave differently** on some systems. If a run fails
  immediately, try `--headed` to observe what the browser is doing.
- **Voice selection requires the model to finish loading** (~5–15 s after page open).
  The script polls `#statusEl` and waits for "Ready to generate" before touching
  the voice dropdown.
- **Cover art** is not always embedded in every EPUB. Use `--cover` to supply one
  manually if `dry_run.py` reports "NOT FOUND".

---

## License

MIT — do whatever you like with it. Commercial use of audio generated via Perchance
is explicitly permitted by the Perchance tool itself.
