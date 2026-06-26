"""
Processing pipeline — ported from notepal-mobile/scripts/process-mascot.py.
Frame extraction, AI background removal, cleanup, crop, and sprite-sheet
stitching.  All functions operate on file paths so they work inside Django
views.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

MAX_DIM = 4096


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ── Cleanup ─────────────────────────────────────────────────────────

def clean_frame(img: Image.Image) -> Image.Image:
    """Two-pass cleanup after AI background removal (see SKILL.md)."""
    arr = np.array(img)
    if arr.shape[-1] != 4:
        return img

    a = arr[:, :, 3].astype(np.float32)
    r = arr[:, :, 0].astype(np.float32)
    g = arr[:, :, 1].astype(np.float32)
    b = arr[:, :, 2].astype(np.float32)

    # ── Pass 0: kill fully-opaque green-screen remnants ──────────
    # Run colour gate BEFORE alpha thresholding so opaque green
    # patches (e.g. between fingers) are caught while still visible.
    green_screen = (
        (a > 80) & (g > 185) & (g - b > 45) & (g - r > 35)
    )
    a[green_screen] = 0

    # ── Pass 1: soft alpha cleanup with edge feathering ─────────
    # Lower threshold keeps more semi-transparent edge pixels for
    # natural anti-aliasing.  Then a tiny Gaussian blur smooths the
    # alpha mask so edges don't look jagged.
    THRESHOLD = 130.0
    a = np.where(
        a < THRESHOLD, 0,
        ((a - THRESHOLD) / (255.0 - THRESHOLD) * 255.0).clip(0, 255),
    )

    # Light Gaussian blur on alpha channel for edge anti-aliasing
    from scipy.ndimage import gaussian_filter
    a_smooth = gaussian_filter(a, sigma=0.6)
    # Only soften edges — keep fully-transparent and fully-opaque areas intact
    edge = (a_smooth > 5) & (a_smooth < 250)
    a[edge] = a_smooth[edge]

    arr[:, :, 3] = a.clip(0, 255).astype(np.uint8)
    return Image.fromarray(arr)


# ── Pipeline steps ──────────────────────────────────────────────────

def extract_frames(video_path: Path, out_dir: Path,
                   width: int = 500, fps: int = 10) -> list[Path]:
    """Extract scaled frames from video via ffmpeg."""
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = f"scale={width}:-1:flags=lanczos,fps={fps}"
    r = run(["ffmpeg", "-y", "-i", str(video_path), "-vf", vf,
             str(out_dir / "frame_%04d.png")])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr}")
    return sorted(out_dir.glob("frame_*.png"))


def remove_backgrounds(frames: list[Path], out_dir: Path) -> list[Path]:
    """AI background removal (rembg) + clean_frame on every frame."""
    from rembg import remove, new_session
    session = new_session("u2net")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_paths = []

    for i, fp in enumerate(frames):
        img = Image.open(fp).convert("RGB")
        out = remove(img, session=session)
        out = clean_frame(out)
        dest = out_dir / fp.name
        out.save(dest, "PNG")
        out_paths.append(dest)

    return sorted(out_paths)


def find_union_bbox(frames: list[Path]) -> tuple[int, int, int, int]:
    """Union bounding box of non-transparent content across all frames."""
    ml, mt, mr, mb = 99999, 99999, 0, 0
    for fp in frames:
        arr = np.array(Image.open(fp))
        alpha = arr[:, :, 3]
        opaque = alpha > 20
        if not opaque.any():
            continue
        rows = np.any(opaque, axis=1)
        cols = np.any(opaque, axis=0)
        ml = min(ml, int(np.argmax(cols)))
        mt = min(mt, int(np.argmax(rows)))
        mr = max(mr, int(alpha.shape[1] - np.argmax(cols[::-1])))
        mb = max(mb, int(alpha.shape[0] - np.argmax(rows[::-1])))
    pad_x = max(1, int((mr - ml) * 0.02))
    pad_y = max(1, int((mb - mt) * 0.02))
    return max(0, ml - pad_x), max(0, mt - pad_y), mr + pad_x, mb + pad_y


def fit_grid(n: int, fw: int, fh: int) -> tuple[int, int, int, int]:
    """Find cols×rows ≤ MAX_DIM². Scales down if needed."""
    for c in range(int(math.ceil(math.sqrt(n))), n + 1):
        r = int(math.ceil(n / c))
        if c * fw <= MAX_DIM and r * fh <= MAX_DIM:
            return c, r, c * fw, r * fh
    for c in range(int(math.ceil(math.sqrt(n))), n + 1):
        r = int(math.ceil(n / c))
        s = min(MAX_DIM / (c * fw), MAX_DIM / (r * fh))
        if s >= 0.5:
            nfw, nfh = int(fw * s), int(fh * s)
            return c, r, c * nfw, r * nfh
    raise RuntimeError(f"Cannot fit {n} frames {fw}×{fh} in {MAX_DIM}²")


def stitch_sheet(frames: list[Path], output_base: Path,
                 fps: int = 10) -> tuple[Path, Path]:
    """Stitch frames into a sprite sheet + metadata JSON.  Returns (png, json)."""
    n = len(frames)
    fw, fh = Image.open(frames[0]).size

    # Crop
    left, top, right, bottom = find_union_bbox(frames)
    cw, ch = right - left, bottom - top
    cropped = []
    for fp in frames:
        img = Image.open(fp).crop((left, top, right, bottom))
        cropped.append(img)

    # Fit grid
    cols, rows, sw, sh = fit_grid(n, cw, ch)
    ffw, ffh = sw // cols, sh // rows
    if ffw != cw or ffh != ch:
        cropped = [img.resize((ffw, ffh), Image.LANCZOS) for img in cropped]

    # Stitch
    sheet = Image.new("RGBA", (sw, sh))
    for i, img in enumerate(cropped):
        col = i % cols
        row = i // cols
        sheet.paste(img, (col * ffw, row * ffh))

    png_path = Path(str(output_base) + ".png")
    json_path = Path(str(output_base) + ".json")
    sheet.save(png_path, "PNG", optimize=True)

    meta = {
        "frameWidth": ffw, "frameHeight": ffh,
        "totalFrames": n, "fps": fps,
        "duration": n / fps,
        "columns": cols, "rows": rows,
        "sheetWidth": sw, "sheetHeight": sh,
    }
    json_path.write_text(json.dumps(meta, indent=2))

    return png_path, json_path
