# yt-video-to-notes

Turn any YouTube video into a PDF of screenshot notes, one per chapter.

The tool downloads a video with `yt-dlp`, extracts its chapter metadata, captures a frame near the end of each chapter with `ffmpeg`, and assembles everything into a formatted PDF with `reportlab`.

## Features

- Auto-detects chapters from the video metadata
- Captures a screenshot 1 second before the end of each chapter
- Customizable layout via a free-text prompt (columns, background color, title placement, timestamps, page numbers, orientation)
- Supports playlist URLs (only the single video is processed)

## Requirements

- Python 3.10+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [ffmpeg](https://ffmpeg.org/)
- Python packages: `reportlab`, `Pillow`

## Installation

```bash
git clone https://github.com/ssgalib/yt-video-to-notes.git
cd yt-video-to-notes

python -m venv .venv
source .venv/bin/activate
pip install yt-dlp reportlab pillow
```

Make sure `ffmpeg` and `yt-dlp` are on your `PATH`.

## Usage

```bash
python notes.py <youtube_url> [options]
```

If you omit the URL, the script will prompt for it and for the output filename.

### Options

| Option | Description |
| ------ | ----------- |
| `-f, --format` | Layout prompt string (see examples below) |
| `-o, --output` | Output PDF path |
| `--capture` | Frame selection mode: `chapter`, `startend`, or `unique` (default: `chapter`) |
| `--scene-threshold` | Scene-detection sensitivity for `--capture unique`, 0–1 (lower = more frames, default: `0.3`) |
| `--keep-video` | Keep the downloaded video in the output directory |

### Capture modes

| Mode | Behavior |
| ---- | -------- |
| `chapter` (default) | One frame per chapter, 1 second before the chapter ends |
| `startend` | Two frames per chapter: 1 second after it starts and 1 second before it ends |
| `unique` | Every unique frame per chapter, detected via ffmpeg scene detection (also keeps the chapter's opening frame) |

```bash
python notes.py <url> --capture startend
python notes.py <url> --capture unique
python notes.py <url> --capture unique --scene-threshold 0.2   # more sensitive
```

A lower `--scene-threshold` keeps more frames; a higher one keeps fewer.

### Layout prompt examples

```bash
python notes.py <url> -f "2 columns, dark background, chapter title below each image, page numbers"
python notes.py <url> -f "1 column, white background, chapter title above image in bold, timestamps shown"
python notes.py <url> -f "3 columns, grey background, compact, no page numbers" -o notes.pdf
```

Supported keywords: `1/2/3 column(s)`, `dark`/`black`/`grey`/`blue` background, `above`/`below` title placement, `timestamps`, `no page numbers`, `compact`, `landscape`.

### Example

```bash
python notes.py "https://www.youtube.com/watch?v=PDxUDYEE-Sk" -o graph_terminology.pdf
```

## How it works

1. **Metadata & download** — `yt-dlp --dump-json` reads the chapter list, then the video is downloaded to a temporary directory.
2. **Screenshots** — `ffmpeg` grabs frames according to the selected `--capture` mode (chapter-end, chapter start+end, or every scene change).
3. **PDF** — `reportlab` lays out the images with chapter titles according to the parsed format prompt.

The temporary directory is cleaned up automatically unless `--keep-video` is passed.

## License

MIT