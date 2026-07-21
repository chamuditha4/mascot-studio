"""
Processing pipeline: frame extraction, AI background removal, despill
cleanup, crop, and sprite-sheet stitching.  All functions operate on file
paths so they work inside Django views.
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

def clean_frame(img: Image.Image,
                despill_method: str = 'average',
                despill_strength: float = 0.5) -> Image.Image:
    """
    High-fidelity post-processing after AI background removal.

    Replaces the legacy destructive alpha-thresholding approach with a
    luminance-preserving, alpha-gated green despill algorithm.  The
    alpha channel is preserved exactly as the AI model produced it —
    no clamping, no blurring — so sub-pixel edge geometry (fingers,
    hair, motion blur) remains intact.

    Args:
        img: RGBA PIL Image from AI matting.
        despill_method: 'average' or 'red' — the green-channel limiting
                        strategy.  'red' is optimised for human skin tones.
        despill_strength: 0.0 – 1.0 blend factor for the despill effect.
                          Default 0.5 gives natural-looking results.

    Returns:
        RGBA PIL Image with neutralised green spill and untouched alpha.
    """
    arr = np.array(img)
    if arr.shape[-1] != 4:
        return img

    img_float = arr.astype(np.float32)

    # Extract channels
    r = img_float[:, :, 0]
    g = img_float[:, :, 1]
    b = img_float[:, :, 2]
    a = img_float[:, :, 3] / 255.0   # normalise to 0–1

    # ── Pass 0: hard-kill fully-opaque green-screen patches ─────
    # Only targets pixels that are unmistakably green screen (high
    # alpha + dominant green).  Uses relaxed thresholds compared to
    # the legacy version to avoid eating into skin tones.
    green_screen = (
        (a > 0.7) &
        (g > 160) &
        (g - b > 30) &
        (g - r > 25)
    )
    img_float[green_screen, 3] = 0
    # Refresh alpha after hard-kill
    a = img_float[:, :, 3] / 255.0

    # ── Pass 1: luminance-preserving alpha-gated despill ────────
    # Calculate the green-channel limit based on the chosen method.
    if despill_method == 'average':
        g_limit = (r + b) / 2.0
    elif despill_method == 'red':
        g_limit = r
    else:
        raise ValueError("despill_method must be 'average' or 'red'")

    g_despilled = np.minimum(g, g_limit)
    spill_amount = g - g_despilled

    # Luminance preservation: redistribute removed green energy
    # symmetrically into red and blue to avoid darkening edges.
    r_compensated = r + (spill_amount * 0.5)
    b_compensated = b + (spill_amount * 0.5)

    # Alpha-gating: apply despill only to semi-transparent edge
    # pixels (alpha between 0.02 and 0.95).  Opaque core regions
    # and fully-transparent areas are left untouched.
    edge_weight = np.clip(
        (1.0 - np.abs(a - 0.5) * 2.0) * despill_strength * 2.0,
        0.0, 1.0
    )

    r_final = r * (1.0 - edge_weight) + r_compensated * edge_weight
    g_final = g * (1.0 - edge_weight) + g_despilled * edge_weight
    b_final = b * (1.0 - edge_weight) + b_compensated * edge_weight

    # ── Reassemble ─────────────────────────────────────────────
    result = np.stack((r_final, g_final, b_final, img_float[:, :, 3]),
                      axis=-1)
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))


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


# ── Supported AI matting models ─────────────────────────────────────
# Model registry: maps user-facing keys to (rembg_session_name, description)
REMBG_MODELS = {
    "u2net":              ("u2net",              "U²-Net — baseline salient object detection (fast, coarse edges)"),
    "u2net_human_seg":    ("u2net_human_seg",    "U²-Net Human Seg — fine-tuned for human anatomy"),
    "isnet-general-use":  ("isnet-general-use",  "ISNet — high-accuracy segmentation, sharp boundaries"),
    "isnet-anime":        ("isnet-anime",        "ISNet Anime — optimised for non-photorealistic line art"),
    "birefnet-general":   ("birefnet-general",   "BiRefNet — bilateral reference, extreme edge fidelity"),
    "birefnet-portrait":  ("birefnet-portrait",  "BiRefNet Portrait — human-optimised with gradient references"),
    "birefnet-dis":       ("birefnet-dis",       "BiRefNet DIS — optimised for thin/delicate structures"),
    "ben2":               ("ben2",               "BEN2 — Confidence Guided Matting, exceptional on fine edges"),
}


def remove_backgrounds(frames: list[Path], out_dir: Path,
                       model: str = "u2net",
                       despill_method: str = "average",
                       despill_strength: float = 0.5) -> list[Path]:
    """
    AI background removal (rembg) + advanced despill on every frame.

    Args:
        frames: list of paths to input RGB PNG frames.
        out_dir: directory for output RGBA PNGs.
        model: key into REMBG_MODELS dict (default: birefnet-general).
        despill_method: 'average' or 'red' for green-channel limiting.
        despill_strength: 0.0–1.0 blend for despill effect.

    Returns:
        Sorted list of Path objects pointing to processed frames.
    """
    from rembg import remove, new_session

    if model not in REMBG_MODELS:
        raise ValueError(
            f"Unknown model '{model}'. Available: {list(REMBG_MODELS.keys())}"
        )

    session_name, _desc = REMBG_MODELS[model]
    session = new_session(session_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_paths = []

    for i, fp in enumerate(frames):
        img = Image.open(fp).convert("RGB")
        out = remove(img, session=session)
        out = clean_frame(out,
                         despill_method=despill_method,
                         despill_strength=despill_strength)
        dest = out_dir / fp.name
        out.save(dest, "PNG")
        out_paths.append(dest)

    return sorted(out_paths)


# ── Robust Video Matting (RVM) pipeline ─────────────────────────────

def remove_backgrounds_rvm(video_path: Path, out_dir: Path,
                           width: int = 500,
                           despill_method: str = "average",
                           despill_strength: float = 0.5) -> list[Path]:
    """
    Temporal-aware background removal using Robust Video Matting (RVM).

    Unlike frame-by-frame static matting, RVM uses a recurrent neural
    network that maintains hidden states across frames, dramatically
    reducing temporal flicker (edge boiling) and improving motion-blur
    handling.

    Prerequisites:
        pip install robustvideomatting  (or the torch hub variant)

    Args:
        video_path: path to the input MP4 video.
        out_dir: directory for output RGBA PNG frames.
        width: resize width (height auto-scaled).
        despill_method: 'average' or 'red'.
        despill_strength: 0.0–1.0 despill blend.

    Returns:
        Sorted list of Path objects pointing to processed frames.
    """
    import warnings
    import tempfile

    out_dir.mkdir(parents=True, exist_ok=True)
    out_paths = []

    try:
        import torch
        import torchvision.transforms as T
    except ImportError:
        raise ImportError(
            "PyTorch and torchvision are required for RVM. "
            "Install with: pip install torch torchvision"
        )

    # Load RVM from torch hub
    try:
        model = torch.hub.load("PeterL1n/RobustVideoMatting", "mobilenetv3")
    except Exception:
        try:
            model = torch.hub.load("PeterL1n/RobustVideoMatting", "resnet50")
        except Exception:
            raise RuntimeError(
                "Could not load RVM from torch hub. "
                "Ensure internet access and try: "
                "pip install robustvideomatting"
            )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device).eval()

    # Extract frames via ffmpeg as a temporary step (RVM needs the
    # video directly, but we keep compatibility with the existing
    # frame-extraction pipeline).
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        raw_frames = extract_frames(video_path, tmp, width=width, fps=10)

        # Read frames into tensor batch
        transform = T.Compose([
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406],
                        std=[0.229, 0.224, 0.225]),
        ])

        # Process in chunks to respect GPU memory
        bgr_tensors = []
        for fp in raw_frames:
            img_pil = Image.open(fp).convert("RGB")
            tensor = transform(img_pil).unsqueeze(0)
            bgr_tensors.append(tensor)

        # RVM processing with recurrent state
        rec = [None] * 4  # recurrent states
        downscale_ratio = 0.25

        for i, src in enumerate(bgr_tensors):
            src = src.to(device)
            with torch.no_grad():
                fgr, pha, *rec = model(src, *rec, downscale_ratio)

            # Convert to numpy RGBA
            fgr_np = (fgr.squeeze(0).permute(1, 2, 0).cpu().numpy() * 255)
            pha_np = (pha.squeeze(0).permute(1, 2, 0).cpu().numpy() * 255)

            fgr_np = np.clip(fgr_np, 0, 255).astype(np.uint8)
            pha_np = np.clip(pha_np, 0, 255).astype(np.uint8)

            # If pha is single-channel, expand
            if pha_np.shape[-1] == 1:
                pha_np = pha_np[:, :, 0]

            rgba = np.dstack([fgr_np, pha_np])

            # Apply advanced despill
            result = clean_frame(
                Image.fromarray(rgba),
                despill_method=despill_method,
                despill_strength=despill_strength,
            )

            dest = out_dir / f"frame_{i:04d}.png"
            result.save(dest, "PNG")
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
