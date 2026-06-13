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
3. Before any audio is generated, the tool parses metadata from the input filename
   and asks you to confirm or correct it interactively.
4. Selenium drives a Chrome browser to the Perchance page, waits for the TTS model
   to finish loading, optionally selects a voice, then submits each chunk.
5. The generated audio is a **browser-side blob URL** — it never touches a server.
   The script reads it back via an async JavaScript `fetch()` call, base64-encodes it,
   and decodes it in Python.
6. Each MP3 is saved to `! Output/<Author — Title>/` and tagged with full ID3v2.3
   metadata (title, author, album, track number, and cover art).

---

## Project structure

```
ePub-to-Audio/
├── epub_utils.py    # shared EPUB parsing, chunking, filename parsing, and metadata helpers
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

This parses metadata from the filename, asks you to confirm it, then prints a
chapter table, chunk count, estimated run time, and cover art status — without
opening a browser or generating any audio.

```
============================================================
  DRY RUN: Jacka, Benedict — Alex Verus #12 — Risen_cln.epub
============================================================

  Parsed from filename:
    [1] Author           Jacka, Benedict
    [2] Title            Risen
    [3] Series           Alex Verus
    [4] Series number    12

  Are these correct?
  Press Enter to accept, a number [1-4] to correct a field,
  or [S] to skip filename parsing and use EPUB metadata instead.

  > 

  Author:     Jacka, Benedict
  Title:      Risen
  Series:     Alex Verus  #12
  Cover art:  found in EPUB
  Chapters extracted: 30
  ...
```

### 2. Run a headed test first

```bash
python epub2audio.py "! Input/Jacka, Benedict — Alex Verus #12 — Risen_cln.epub" --headed
```

`--headed` keeps the Chrome window visible so you can confirm the page loads,
the voice is selected, and the first chunk generates correctly before walking away.

### 3. Full headless run

```bash
python epub2audio.py "! Input/Jacka, Benedict — Alex Verus #12 — Risen_cln.epub"
```

Output lands in `! Output/Jacka, Benedict — Alex Verus #12 — Risen/`.

---

## Metadata confirmation

Before any audio is generated the tool parses the input filename and shows you
what it found:

```
  Parsed from filename:
    [1] Author           Jacka, Benedict
    [2] Title            Risen
    [3] Series           Alex Verus
    [4] Series number    12

  Are these correct?
  Press Enter to accept, a number [1-4] to correct a field,
  or [S] to skip filename parsing and use EPUB metadata instead.

  > 3
  Series [Alex Verus]: Alex Verus Series
```

After any correction the list is redisplayed so you can verify before continuing.
Entering **S** falls back to the EPUB's internal DC metadata.

### Filename patterns recognised

The parser is intentionally flexible.  All of the following work:

| Filename | Author | Series | # | Title |
|---|---|---|---|---|
| `Jacka, Benedict — Alex Verus #12 — Risen.epub` | Jacka, Benedict | Alex Verus | 12 | Risen |
| `Jacka, Benedict — Alex Verus 12 — Risen.epub` | Jacka, Benedict | Alex Verus | 12 | Risen |
| `Jacka, Benedict — Alex Verus Book 12 — Risen.epub` | Jacka, Benedict | Alex Verus | 12 | Risen |
| `AV12 - Jacka, Benedict — Alex Verus #12 — Risen_cln.epub` | Jacka, Benedict | Alex Verus | 12 | Risen |
| `Scalzi, John — The Kaiju Preservation Society.epub` | Scalzi, John | *(none)* | *(none)* | The Kaiju Preservation Society |

- Leading codes like `AV12 -` are stripped and ignored.
- Trailing suffixes like `_cln` or `_edit` are stripped and ignored.
- Em-dash `—`, en-dash `–`, and plain hyphen `-` are all accepted as field separators.
- Series numbers may be prefixed with `#`, `Book`, or left bare as a trailing number.

If the filename does not match any recognised pattern the tool falls back to
EPUB internal metadata automatically.

### Non-interactive / scripted use

Use `--yes` (or `-y`) to accept the parsed values without prompting:

```bash
python epub2audio.py book.epub --yes
```

### Overriding individual fields

CLI flags always win over filename parsing and the confirmation prompt:

```bash
python epub2audio.py book.epub --series "Alex Verus" --series-number 12
python epub2audio.py book.epub --author "Jacka, Benedict" --title "Risen"
```

---

## All options

```
usage: epub2audio.py [-h] [-o OUTPUT] [--cover COVER]
                     [--author AUTHOR] [--title TITLE]
                     [--series SERIES] [--series-number N] [--yes]
                     [--headed] [--max-chunk N]
                     [--stop-after TEXT] [--start-chapter N] [--start-chunk N]
                     [--voice VOICE] [--list-voices]
                     epub
```

| Argument | Default | Description |
|---|---|---|
| `epub` | *(required)* | Path to the `.epub` file |
| `-o / --output` | `! Output/` | Root directory for generated audiobooks |
| `--cover` | *(EPUB cover)* | Override cover image (JPEG or PNG) embedded in MP3s |
| `--author` | *(filename/EPUB)* | Author in `Last, First` format |
| `--title` | *(filename/EPUB)* | Book title |
| `--series` | *(filename/EPUB)* | Series name, e.g. `"Alex Verus"` |
| `--series-number` | *(filename/EPUB)* | Book number within the series, e.g. `12` |
| `--yes / -y` | prompt | Accept parsed metadata without confirmation |
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
└── Jacka, Benedict — Alex Verus #12 — Risen/
    ├── Jacka, Benedict — Alex Verus #12 — Risen - 001.mp3      ← Dedication
    ├── Jacka, Benedict — Alex Verus #12 — Risen - 002_1.mp3    ← Chapter 1, chunk 1
    ├── Jacka, Benedict — Alex Verus #12 — Risen - 002_2.mp3    ← Chapter 1, chunk 2
    ...
```

For books without a series:

```
! Output/
└── Scalzi, John — The Kaiju Preservation Society/
    ├── Scalzi, John — The Kaiju Preservation Society - 001.mp3
    ...
```

---

## ID3 tags written

| Tag | Content |
|---|---|
| Title | `001 Book Title — Chapter Name` (3-digit zero-padded track number prefix) |
| Artist | Author in `Last, First` format (e.g. `Jacka, Benedict`) |
| Album | Series name if present (e.g. `Alex Verus`), otherwise book title |
| Track | `N/Total` |
| Cover art | Extracted from EPUB, or supplied via `--cover` |

Author is stored in `Last, First` format throughout — in filenames, folder names,
and ID3 tags — so library searches and filesystem sorts all behave consistently.

The zero-padded track number prefix in the Title tag ensures tracks sort
correctly by title in media players where only the title is visible — for
example, when the display is too small to show the full track name.
Tracks 1, 10, and 100 sort as `001`, `010`, and `100` rather than `1`, `10`, `100`.

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
