# yt-video-to-notes

Turn any YouTube video into a PDF slide deck, one full-page slide per captured frame.

The tool downloads a video with `yt-dlp`, extracts its chapter metadata, captures frames with `ffmpeg`, and assembles them into a full-page slide PDF with `reportlab`.

## Features

- Auto-detects chapters from the video metadata
- Full-page slides: each frame is scaled to fit a landscape A4 page (never cropped), with the chapter title in a caption bar
- Cover page with the video title
- Three capture modes: one frame per chapter, two per chapter (start + end), or every unique frame (ffmpeg scene detection)
- Output defaults to `<video title>.pdf`
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

If you omit the URL, the script will prompt for it. The output PDF is named after the video title unless you pass `--output`.

### Options

| Option | Description |
| ------ | ----------- |
| `-o, --output` | Output PDF path (default: `<video title>.pdf`) |
| `--capture` | Frame selection mode: `chapter`, `startend`, or `unique` (default: `startend`) |
| `--scene-threshold` | Scene-detection sensitivity for `--capture unique`, 0–1 (lower = more frames, default: `0.3`) |
| `--keep-video` | Keep the downloaded video in the output directory |

### Capture modes

| Mode | Behavior |
| ---- | -------- |
| `chapter` | One frame per chapter, 1 second before the chapter ends |
| `startend` (default) | Two frames per chapter: 1 second after it starts and 1 second before it ends |
| `unique` | Every unique frame per chapter, detected via ffmpeg scene detection (also keeps the chapter's opening frame) |

```bash
python notes.py <url> --capture startend
python notes.py <url> --capture unique
python notes.py <url> --capture unique --scene-threshold 0.2   # more sensitive
```

A lower `--scene-threshold` keeps more frames; a higher one keeps fewer.

### Example

```bash
python notes.py "https://www.youtube.com/watch?v=PDxUDYEE-Sk"
```

Creates `Discrete Math - 10.2.1 Graph Terminology.pdf` with a cover page and one full-page slide per captured frame.

## How it works

1. **Metadata & download** — `yt-dlp --dump-json` reads the chapter list, then the video is downloaded to a temporary directory.
2. **Screenshots** — `ffmpeg` grabs frames according to the selected `--capture` mode (chapter-end, chapter start+end, or every scene change).
3. **PDF** — `reportlab` renders each frame scaled to fit a full-page landscape slide (letterboxed on white, never cropped), with the chapter title in a caption bar, preceded by a cover page.

The temporary directory is cleaned up automatically unless `--keep-video` is passed.

## License

MIT