#!/usr/bin/env python3
"""
yt-video-to-notes
-----------------
Downloads a YouTube video, captures frames per chapter (start + end by default),
and compiles them into a full-page slide PDF with a chapter-title caption.

Usage:
    python notes.py <youtube_url> [--capture startend] [--output notes.pdf]

Capture modes:
    chapter   — 1 frame, 1s before each chapter ends
    startend  — 2 frames per chapter: 1s after start, 1s before end (default)
    unique    — every unique frame via ffmpeg scene detection
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ── PDF ──────────────────────────────────────────────────────────────────────
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate
from PIL import Image as PILImage

# ─────────────────────────────────────────────────────────────────────────────
# 1. VIDEO DOWNLOAD & CHAPTER EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def download_video(url: str, out_dir: str) -> tuple[str, list[dict], str]:
    """
    Downloads the video and extracts chapter metadata via yt-dlp.
    Returns (video_path, chapters, title).
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
    return video_file, chapters, title


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

def _safe_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "notes"


class _Cover(Flowable):
    """A full-page cover showing the video title."""

    def __init__(self, title: str, subtitle: str, pw: float, ph: float):
        super().__init__()
        self.title = title
        self.subtitle = subtitle
        self.pw = pw
        self.ph = ph

    def wrap(self, avail_w, avail_h):
        return (self.pw, self.ph)

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(colors.white)
        c.rect(0, 0, self.pw, self.ph, fill=True, stroke=False)

        size = 28
        while size > 12 and c.stringWidth(self.title, "Helvetica-Bold", size) > self.pw - 4 * cm:
            size -= 1
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", size)
        c.drawCentredString(self.pw / 2, self.ph / 2, self.title)

        c.setFillColor(colors.HexColor("#666666"))
        c.setFont("Helvetica", 14)
        c.drawCentredString(self.pw / 2, self.ph / 2 - 1.5 * cm, self.subtitle)
        c.restoreState()


class _Slide(Flowable):
    """A full-page slide: the captured frame edge-to-edge with a caption bar."""

    def __init__(self, img_path: str, title: str, pw: float, ph: float, bar_h: float):
        super().__init__()
        with PILImage.open(img_path) as im:
            iw, ih = im.size
        # Scale to fit the full frame inside the page (no cropping); the
        # caption bar occupies the bottom strip.
        avail_h = ph - bar_h
        scale = min(pw / iw, avail_h / ih)
        self.img_x = (pw - iw * scale) / 2
        self.img_y = bar_h + (avail_h - ih * scale) / 2
        self.img_w = iw * scale
        self.img_h = ih * scale
        self.img_path = img_path
        self.title = title
        self.pw = pw
        self.ph = ph
        self.bar_h = bar_h

    def wrap(self, avail_w, avail_h):
        return (self.pw, self.ph)

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(colors.white)
        c.rect(0, 0, self.pw, self.ph, fill=True, stroke=False)
        c.drawImage(self.img_path, self.img_x, self.img_y, width=self.img_w, height=self.img_h)

        c.setFillColor(colors.black)
        c.setFillAlpha(0.55)
        c.rect(0, 0, self.pw, self.bar_h, fill=True, stroke=False)
        c.setFillAlpha(1)

        size = 16
        while size > 7 and c.stringWidth(self.title, "Helvetica-Bold", size) > self.pw - 2 * cm:
            size -= 1
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", size)
        c.drawCentredString(self.pw / 2, (self.bar_h - size) / 2, self.title)
        c.restoreState()


class _SlideDeck(BaseDocTemplate):
    """A document with a single full-bleed, zero-padding frame."""

    def __init__(self, filename, **kw):
        BaseDocTemplate.__init__(self, filename, **kw)
        pw, ph = self.pagesize
        frame = Frame(0, 0, pw, ph, id="main",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id="page", frames=[frame])])


def build_pdf(chapters_with_shots: list[dict], output_path: str, video_title: str):
    """
    Assembles a full-page slide deck: one slide per captured frame,
    preceded by a cover page. Landscape A4.
    """
    print("[3/3] Building PDF…")
    pw, ph = landscape(A4)
    bar_h = 1.2 * cm

    slides = []
    for ch in chapters_with_shots:
        for shot in ch["shots"]:
            slides.append((shot["screenshot"], ch["title"]))

    story = [_Cover(video_title, f"{len(slides)} slide{'s' if len(slides) != 1 else ''}", pw, ph)]
    for img_path, title in slides:
        story.append(_Slide(img_path, title, pw, ph, bar_h))

    doc = _SlideDeck(output_path, pagesize=landscape(A4), title=video_title)
    doc.build(story)
    print(f"  ✓ PDF saved: {output_path} ({len(slides)} slide(s))")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download a YouTube video and create a full-page slide PDF from its chapter screenshots."
    )
    parser.add_argument("url", nargs="?", help="YouTube video URL (prompted if omitted)")
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output PDF path (default: <video title>.pdf)",
    )
    parser.add_argument(
        "--capture",
        choices=["chapter", "startend", "unique"],
        default="startend",
        help="Which frames to capture (default: startend)",
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

    output = args.output
    tmpdir = tempfile.mkdtemp(prefix="yt_chapters_")
    try:
        video_path, chapters, video_title = download_video(url, tmpdir)
        chapters_with_shots = capture_screenshots(
            video_path, chapters, tmpdir, capture=args.capture, scene_threshold=args.scene_threshold
        )
        if not output:
            output = f"{_safe_filename(video_title)}.pdf"
        build_pdf(chapters_with_shots, output, video_title)
    finally:
        if args.keep_video and output:
            dest = Path(output).parent / Path(video_path).name
            shutil.copy2(video_path, dest)
            print(f"Video kept at: {dest}")
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\nDone! ✓")


if __name__ == "__main__":
    main()
