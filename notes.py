#!/usr/bin/env python3
"""
yt_chapter_pdf.py
-----------------
Downloads a YouTube video, captures a screenshot 1 second before the end of
each chapter (time block), then compiles them into a formatted PDF.

Usage:
    python yt_chapter_pdf.py <youtube_url> [--format "<layout prompt>"] [--output output.pdf] [--keep-video]

Layout prompt examples:
    "2 columns, dark background, chapter title below each image, page numbers"
    "1 column, white background, chapter title above image in bold, timestamps shown"
    "3 columns, grey background, compact, no page numbers"
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# ── PDF ──────────────────────────────────────────────────────────────────────
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)
from PIL import Image as PILImage

# ─────────────────────────────────────────────────────────────────────────────
# 1. FORMAT PROMPT PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_format_prompt(prompt: str) -> dict:
    """
    Parses a free-text layout prompt into a config dict.
    Supports: columns (1/2/3), background colour, title placement,
              timestamps, page numbers, page orientation.
    """
    p = prompt.lower()

    # Columns
    cols = 1
    m = re.search(r'(\d)\s*col', p)
    if m:
        cols = max(1, min(3, int(m.group(1))))

    # Background
    bg = colors.white
    if any(w in p for w in ['dark', 'black']):
        bg = colors.HexColor('#1a1a2e')
    elif any(w in p for w in ['grey', 'gray']):
        bg = colors.HexColor('#f0f0f0')
    elif 'blue' in p:
        bg = colors.HexColor('#1b2a4a')

    # Text colour (auto-contrast)
    text_color = colors.black if bg == colors.white or bg == colors.HexColor('#f0f0f0') else colors.white

    # Title placement
    title_above = 'above' in p
    title_below = not title_above  # default: below

    # Extras
    show_timestamps = 'timestamp' in p
    show_page_numbers = 'no page' not in p  # default: show
    compact = 'compact' in p
    orient = landscape(A4) if 'landscape' in p else A4

    return dict(
        cols=cols, bg=bg, text_color=text_color,
        title_above=title_above, title_below=title_below,
        show_timestamps=show_timestamps,
        show_page_numbers=show_page_numbers,
        compact=compact, pagesize=orient,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. VIDEO DOWNLOAD & CHAPTER EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def download_video(url: str, out_dir: str) -> tuple[str, list[dict]]:
    """
    Downloads the video and extracts chapter metadata via yt-dlp.
    Returns (video_path, chapters).
    chapters = [{"title": str, "start_time": float, "end_time": float}, ...]
    """
    print(f"[1/3] Fetching metadata for: {url}")
    meta_cmd = [
        "yt-dlp", "--dump-json", "--no-download", "--no-playlist", url
    ]
    result = subprocess.run(meta_cmd, capture_output=True, text=True, check=True)
    meta = json.loads(result.stdout)

    chapters = meta.get("chapters") or []
    title = meta.get("title", "video")
    duration = float(meta.get("duration", 0))

    if not chapters:
        print("  ⚠  No chapters found — treating the whole video as one block.")
        chapters = [{"title": title, "start_time": 0.0, "end_time": duration}]

    # Fill missing end_times
    for i, ch in enumerate(chapters):
        if "end_time" not in ch or ch["end_time"] is None:
            if i + 1 < len(chapters):
                ch["end_time"] = chapters[i + 1]["start_time"]
            else:
                ch["end_time"] = duration

    print(f"  ✓ Found {len(chapters)} chapter(s):")
    for ch in chapters:
        print(f"    [{_fmt_time(ch['start_time'])} → {_fmt_time(ch['end_time'])}] {ch['title']}")

    print("[1/3] Downloading video…")
    video_path = os.path.join(out_dir, "video.%(ext)s")
    dl_cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", video_path,
        url,
    ]
    subprocess.run(dl_cmd, check=True)

    # Find the downloaded file
    matches = list(Path(out_dir).glob("video.*"))
    if not matches:
        raise FileNotFoundError("yt-dlp did not produce a video file.")
    video_file = str(matches[0])
    print(f"  ✓ Saved to: {video_file}")
    return video_file, chapters


def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. SCREENSHOT CAPTURE
# ─────────────────────────────────────────────────────────────────────────────

def _extract_frame(video_path: str, ts: float, img_path: str) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(ts),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        img_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _scene_change_times(video_path: str, start: float, end: float, threshold: float) -> list[float]:
    """Absolute timestamps where ffmpeg detects a scene change in [start, end)."""
    cmd = [
        "ffmpeg", "-ss", str(start), "-to", str(end), "-i", video_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-an", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    times = []
    for line in result.stderr.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            times.append(start + float(m.group(1)))
    return times


def capture_screenshots(
    video_path: str,
    chapters: list[dict],
    out_dir: str,
    capture: str = "chapter",
    scene_threshold: float = 0.3,
) -> list[dict]:
    """
    Captures frames for each chapter according to the capture mode.
    Returns chapters grouped as {"title", "shots": [{"screenshot", "ts", "label"}]}.

    Modes:
      "chapter"  — 1 frame, 1s before chapter end.
      "startend" — 2 frames per chapter: 1s after start and 1s before end.
      "unique"   — every unique frame detected by ffmpeg scene detection.
    """
    print(f"[2/3] Capturing screenshots (mode: {capture})…")
    result = []
    for i, ch in enumerate(chapters):
        start = float(ch["start_time"])
        end = float(ch["end_time"])
        shots = []

        if capture == "startend":
            ts_start = min(start + 1.0, end)
            ts_end = max(end - 1.0, start)
            if ts_end - ts_start < 1.0:
                points = [(start + (end - start) / 2, None)]
            else:
                points = [(ts_start, "start"), (ts_end, "end")]
        elif capture == "unique":
            times = [start + 0.5] + _scene_change_times(video_path, start, end, scene_threshold)
            times = sorted(set(round(t, 2) for t in times))
            kept = []
            for t in times:
                if not kept or t - kept[-1] >= 1.5:
                    kept.append(t)
            points = [(t, None) for t in kept]
        else:
            points = [(max(start, end - 1.0), None)]

        for j, (ts, label) in enumerate(points):
            img_path = os.path.join(out_dir, f"chapter_{i:03d}_{j:02d}.jpg")
            _extract_frame(video_path, ts, img_path)
            tag = f" [{label}]" if label else ""
            print(f"  ✓ [{i+1}/{len(chapters)}] {ch['title']}{tag} @ {_fmt_time(ts)}")
            shots.append({"screenshot": img_path, "ts": ts, "label": label})

        result.append({"title": ch["title"], "shots": shots})

    total = sum(len(c["shots"]) for c in result)
    print(f"  ✓ Captured {total} frame(s) total.")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 4. PDF GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def _img_size(path: str, max_w: float, max_h: float):
    """Return (w, h) scaled to fit within max_w × max_h, preserving aspect."""
    with PILImage.open(path) as im:
        iw, ih = im.size
    ratio = min(max_w / iw, max_h / ih)
    return iw * ratio, ih * ratio


def build_pdf(chapters_with_shots: list[dict], cfg: dict, output_path: str, video_title: str,
              capture: str = "chapter"):
    """
    Assembles the PDF according to the parsed format config.
    """
    print("[3/3] Building PDF…")
    pagesize = cfg["pagesize"]
    pw, ph = pagesize
    margin = 1.5 * cm if cfg["compact"] else 2 * cm
    cols = cfg["cols"]
    bg = cfg["bg"]
    text_color = cfg["text_color"]
    show_ts = cfg["show_timestamps"]
    show_pg = cfg["show_page_numbers"]

    # ── Styles ────────────────────────────────────────────────────────────────
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ChTitle", parent=styles["Normal"],
        fontSize=9 if cfg["compact"] else 11,
        leading=13,
        textColor=text_color,
        fontName="Helvetica-Bold",
        alignment=1,  # centre
        spaceAfter=2 * mm,
        spaceBefore=2 * mm,
    )
    ts_style = ParagraphStyle(
        "TS", parent=styles["Normal"],
        fontSize=7,
        textColor=text_color,
        fontName="Helvetica-Oblique",
        alignment=1,
        spaceAfter=1 * mm,
    )
    header_style = ParagraphStyle(
        "Header", parent=styles["Heading1"],
        fontSize=16,
        textColor=text_color,
        fontName="Helvetica-Bold",
        alignment=1,
        spaceAfter=4 * mm,
    )

    # ── Page background callback ──────────────────────────────────────────────
    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(bg)
        canvas.rect(0, 0, pw, ph, fill=True, stroke=False)
        if show_pg:
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(text_color)
            canvas.drawCentredString(pw / 2, margin / 2, f"Page {doc.page}")
        canvas.restoreState()

    # ── Layout calculations ───────────────────────────────────────────────────
    usable_w = pw - 2 * margin
    usable_h = ph - 2 * margin
    gap = 0.5 * cm
    cell_w = (usable_w - gap * (cols - 1)) / cols
    img_max_h = usable_h * 0.35 if cols >= 2 else usable_h * 0.45

    # ── Story ─────────────────────────────────────────────────────────────────
    story = []

    # Cover title
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(video_title, header_style))
    story.append(Spacer(1, 0.5 * cm))

    def make_shot_flowables(shot, per_img_h):
        """Timestamp label (optional) + image for one captured frame."""
        items = []
        iw, ih = _img_size(shot["screenshot"], cell_w, per_img_h)
        img = Image(shot["screenshot"], width=iw, height=ih)
        if show_ts:
            lbl = f"{shot['label']} " if shot.get("label") else ""
            items.append(Paragraph(f"{lbl}@ {_fmt_time(shot['ts'])}", ts_style))
        items.append(img)
        return items

    def make_stacked_cell(ch):
        """One cell per chapter: title once, all shots stacked."""
        per_img_h = img_max_h / max(1, len(ch["shots"]))
        title_para = Paragraph(ch["title"], title_style)
        body = []
        for shot in ch["shots"]:
            body.extend(make_shot_flowables(shot, per_img_h))
        if cfg["title_above"]:
            return [title_para] + body
        return body + [title_para]

    def make_frame_cell(ch, shot):
        """One cell per frame: chapter title + timestamp repeated."""
        title_para = Paragraph(ch["title"], title_style)
        body = make_shot_flowables(shot, img_max_h)
        if cfg["title_above"]:
            return [title_para] + body
        return body + [title_para]

    # Build the flat list of cells (each a list of flowables)
    cells = []
    for ch in chapters_with_shots:
        if capture == "unique":
            for shot in ch["shots"]:
                cells.append(make_frame_cell(ch, shot))
        else:
            cells.append(make_stacked_cell(ch))

    if cols == 1:
        for cell in cells:
            for item in cell:
                story.append(item)
            story.append(Spacer(1, 0.8 * cm))
    else:
        # Build rows of `cols` cells
        rows = [cells[i:i+cols] for i in range(0, len(cells), cols)]
        for row in rows:
            # Pad short rows
            while len(row) < cols:
                row.append(None)

            table_data = [[cell if cell else [] for cell in row]]
            col_widths = [cell_w + gap * (i < cols - 1) for i in range(cols)]
            tbl = Table(table_data, colWidths=col_widths)
            tbl.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN",  (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING",  (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), gap),
                ("TOPPADDING",   (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 0.6 * cm))

    # ── Build ─────────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_path,
        pagesize=pagesize,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin,
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(f"  ✓ PDF saved: {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download a YouTube video and create a chapter-screenshot PDF."
    )
    parser.add_argument("url", nargs="?", help="YouTube video URL (prompted if omitted)")
    parser.add_argument(
        "--format", "-f", dest="fmt",
        default="1 column, white background, chapter title below image, page numbers",
        help="Layout prompt (e.g. '2 columns, dark background, title above, timestamps')",
    )
    parser.add_argument("--output", "-o", default=None, help="Output PDF path (prompted if omitted)")
    parser.add_argument(
        "--capture",
        choices=["chapter", "startend", "unique"],
        default="chapter",
        help="Which frames to capture (default: chapter)",
    )
    parser.add_argument(
        "--scene-threshold", dest="scene_threshold", type=float, default=0.3,
        help="Scene-detection sensitivity for --capture unique, 0-1 (lower = more frames)",
    )
    parser.add_argument("--keep-video", action="store_true", help="Keep the downloaded video file")
    args = parser.parse_args()

    url = args.url or input("YouTube video URL: ").strip()
    if not url:
        sys.exit("No URL provided.")
    output = args.output or input("Output file name (e.g. my_notes.pdf): ").strip()
    if not output:
        output = "chapters.pdf"

    cfg = parse_format_prompt(args.fmt)
    print(f"Layout config: {cfg}\n")

    tmpdir = tempfile.mkdtemp(prefix="yt_chapters_")
    try:
        video_path, chapters = download_video(url, tmpdir)
        chapters_with_shots = capture_screenshots(
            video_path, chapters, tmpdir, capture=args.capture, scene_threshold=args.scene_threshold
        )
        # Get video title for PDF header
        meta_result = subprocess.run(
            ["yt-dlp", "--get-title", "--no-download", "--no-playlist", url],
            capture_output=True, text=True
        )
        video_title = meta_result.stdout.strip() or "YouTube Video Chapters"
        build_pdf(chapters_with_shots, cfg, output, video_title, capture=args.capture)
    finally:
        if args.keep_video:
            dest = Path(output).parent / Path(video_path).name
            shutil.copy2(video_path, dest)
            print(f"Video kept at: {dest}")
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\nDone! ✓")


if __name__ == "__main__":
    main()
