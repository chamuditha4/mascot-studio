import json
import logging
import re
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from processor.pipeline import (
    extract_frames, remove_backgrounds, remove_backgrounds_rvm,
    stitch_sheet, REMBG_MODELS,
)

# Session ids are uuid4().hex[:12]; frames are always frame_NNNN.png.
# Both are attacker-controlled in URLs and JSON bodies, so they are
# matched against these patterns before ever touching the filesystem.
SESSION_ID_RE = re.compile(r"^[0-9a-f]{12}$")
FRAME_NAME_RE = re.compile(r"^frame_\d{4,6}\.png$")

# Guard rails for sprite-sheet import — a hostile metadata JSON could
# otherwise ask us to allocate an unbounded number of frames.
MAX_IMPORT_FRAMES = 2000
MAX_FRAME_DIM = 4096


logger = logging.getLogger(__name__)


def _server_error(message: str, exc: Exception) -> JsonResponse:
    """Log the real failure; only surface details while DEBUG is on."""
    logger.exception(message)
    detail = f"{message}: {exc}" if settings.DEBUG else message
    return JsonResponse({"error": detail}, status=500)


def _session_dir(session_id: str) -> Path:
    """Resolve (and create) a session directory, rejecting unsafe ids."""
    if not SESSION_ID_RE.match(session_id):
        raise SuspiciousOperation("Invalid session id.")
    d = Path(settings.MEDIA_ROOT) / "sessions" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _frame_path(session_id: str, name: str) -> Path:
    """Resolve a frame file inside a session, rejecting unsafe names."""
    if not isinstance(name, str) or not FRAME_NAME_RE.match(name):
        raise SuspiciousOperation("Invalid frame name.")
    return _session_dir(session_id) / "frames" / name


def _frame_list(session_id: str) -> list[dict]:
    """Return sorted list of frame info for the session."""
    d = _session_dir(session_id) / "frames"
    if not d.exists():
        return []
    frames = sorted(d.glob("frame_*.png"))
    return [
        {
            "name": f.name,
            "url": f"/media/sessions/{session_id}/frames/{f.name}",
        }
        for f in frames
    ]


# ── Pages ───────────────────────────────────────────────────────────

def index(request):
    """Landing / upload page."""
    return render(request, "editor/upload.html")


def editor(request, session_id: str):
    """Frame-by-frame editor."""
    frames = _frame_list(session_id)
    if not frames:
        return render(request, "editor/upload.html",
                      {"error": "Session not found or no frames."})

    # Read processing config
    config_path = _session_dir(session_id) / "config.json"
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text())

    return render(request, "editor/edit.html", {
        "session_id": session_id,
        "frames": frames,
        "frame_count": len(frames),
        "fps": config.get("fps", 10),
        "frame_width": config.get("frame_width", 0),
        "frame_height": config.get("frame_height", 0),
    })


# ── API ─────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def api_upload(request):
    """
    Upload a video, extract frames, run AI cleanup.

    Query parameters / POST fields:
        video:      MP4 file (required)
        width:      frame width in px (default 500)
        fps:        output FPS (default 10)
        backend:    'rembg' (default) or 'rvm' for temporal matting
        model:      AI model key (default 'birefnet-general').
                    See REMBG_MODELS for available options.
        despill:    'average' or 'red' (default 'average')
        strength:   despill blend 0.0–1.0 (default 0.5)
    """
    video = request.FILES.get("video")
    if not video:
        return JsonResponse({"error": "No video file."}, status=400)

    try:
        width = int(request.POST.get("width", 500))
        fps = int(request.POST.get("fps", 10))
        despill_strength = float(request.POST.get("strength", 0.5))
    except (TypeError, ValueError):
        return JsonResponse({"error": "width, fps and strength must be numeric."},
                            status=400)

    if not 32 <= width <= MAX_FRAME_DIM:
        return JsonResponse({"error": f"width must be 32–{MAX_FRAME_DIM}."}, status=400)
    if not 1 <= fps <= 60:
        return JsonResponse({"error": "fps must be 1–60."}, status=400)
    despill_strength = min(max(despill_strength, 0.0), 1.0)

    backend = request.POST.get("backend", "rembg")
    if backend not in ("rembg", "rvm"):
        return JsonResponse({"error": "backend must be 'rembg' or 'rvm'."}, status=400)

    model = request.POST.get("model", "u2net")
    if model not in REMBG_MODELS:
        return JsonResponse({"error": f"Unknown model '{model}'."}, status=400)

    despill_method = request.POST.get("despill", "average")
    if despill_method not in ("average", "red"):
        return JsonResponse({"error": "despill must be 'average' or 'red'."}, status=400)

    session_id = uuid.uuid4().hex[:12]
    sd = _session_dir(session_id)

    # Save video
    video_path = sd / "input.mp4"
    with open(video_path, "wb") as f:
        for chunk in video.chunks():
            f.write(chunk)

    try:
        frames_dir = sd / "frames"
        fw, fh = 0, 0

        if backend == "rvm":
            # ── Temporal matting via Robust Video Matting ────────
            clean_frames = remove_backgrounds_rvm(
                video_path, frames_dir,
                width=width,
                despill_method=despill_method,
                despill_strength=despill_strength,
            )
        else:
            # ── Static frame-by-frame matting via rembg ──────────
            raw_dir = sd / "raw"
            raw_frames = extract_frames(video_path, raw_dir, width, fps)
            clean_frames = remove_backgrounds(
                raw_frames, frames_dir,
                model=model,
                despill_method=despill_method,
                despill_strength=despill_strength,
            )
            shutil.rmtree(raw_dir)

        if clean_frames:
            from PIL import Image
            fw, fh = Image.open(clean_frames[0]).size

        # Save config
        (sd / "config.json").write_text(json.dumps({
            "width": width, "fps": fps,
            "frame_width": fw, "frame_height": fh,
            "backend": backend,
            "model": model,
            "despill": despill_method,
            "strength": despill_strength,
        }))

        return JsonResponse({
            "session_id": session_id,
            "frame_count": len(clean_frames),
            "frame_width": fw,
            "frame_height": fh,
            "redirect": f"/edit/{session_id}/",
        })
    except Exception as e:
        shutil.rmtree(sd, ignore_errors=True)
        return _server_error("Video processing failed", e)


@csrf_exempt
@require_http_methods(["POST"])
def api_reprocess_frame(request, session_id: str):
    """Re-run AI background removal on a single frame.

    Accepts the frame name and optional model/despill overrides.
    The server re-reads the saved PNG, composites it over the known
    green-screen colour, runs it through the selected rembg model +
    advanced despill, and overwrites the file.
    """
    data = json.loads(request.body)
    name = data.get("name")
    if not name:
        return JsonResponse({"error": "Missing frame name."}, status=400)

    frame_path = _frame_path(session_id, name)
    if not frame_path.exists():
        return JsonResponse({"error": "Frame not found."}, status=404)

    # Read session config for saved model preferences
    config_path = _session_dir(session_id) / "config.json"
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text())

    model = data.get("model", config.get("model", "u2net"))
    if model not in REMBG_MODELS:
        model = "birefnet-general"

    despill_method = data.get("despill", config.get("despill", "average"))
    if despill_method not in ("average", "red"):
        despill_method = "average"

    try:
        despill_strength = float(data.get("strength", config.get("strength", 0.5)))
    except (TypeError, ValueError):
        despill_strength = 0.5
    despill_strength = min(max(despill_strength, 0.0), 1.0)

    try:
        from PIL import Image
        from rembg import remove, new_session
        from processor.pipeline import clean_frame

        # Composite the current frame over the green-screen colour so
        # rembg sees proper green background instead of black holes.
        GS_R, GS_G, GS_B = 157, 231, 162

        current = Image.open(frame_path).convert("RGBA")
        bg = Image.new("RGB", current.size, (GS_R, GS_G, GS_B))
        composited = Image.alpha_composite(
            bg.convert("RGBA"), current
        ).convert("RGB")

        session_name, _desc = REMBG_MODELS[model]
        session = new_session(session_name)
        out = remove(composited, session=session)
        out = clean_frame(out,
                         despill_method=despill_method,
                         despill_strength=despill_strength)
        out.save(frame_path, "PNG")

        return JsonResponse({
            "ok": True,
            "url": f"/media/sessions/{session_id}/frames/{name}",
        })
    except Exception as e:
        return _server_error("Frame reprocessing failed", e)


@csrf_exempt
@require_http_methods(["POST"])
def api_save_frame(request, session_id: str):
    """Save an edited frame (overwrite the PNG with the edited data URL)."""
    import base64
    import binascii

    data = json.loads(request.body)
    name = data.get("name")
    image_data = data.get("image")  # base64 PNG data URL

    if not name or not isinstance(image_data, str):
        return JsonResponse({"error": "Missing name or image."}, status=400)

    if not image_data.startswith("data:image/png;base64,"):
        return JsonResponse({"error": "Expected a PNG data URL."}, status=400)

    try:
        binary = base64.b64decode(image_data.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError):
        return JsonResponse({"error": "Malformed base64 image data."}, status=400)

    # Only overwrite frames that already belong to this session.
    frame_path = _frame_path(session_id, name)
    if not frame_path.exists():
        return JsonResponse({"error": "Frame not found."}, status=404)

    frame_path.write_bytes(binary)

    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["POST"])
def api_export(request, session_id: str):
    """Stitch edited frames into a sprite sheet and return download URL."""
    sd = _session_dir(session_id)
    frames_dir = sd / "frames"
    frames = sorted(frames_dir.glob("frame_*.png"))
    if not frames:
        return JsonResponse({"error": "No frames."}, status=400)

    config = json.loads((sd / "config.json").read_text()) if (sd / "config.json").exists() else {}
    fps = config.get("fps", 10)

    export_dir = sd / "export"
    export_dir.mkdir(exist_ok=True)
    png_path, json_path = stitch_sheet(frames, export_dir / "sprite", fps)

    return JsonResponse({
        "sprite_url": f"/media/sessions/{session_id}/export/sprite.png",
        "meta_url": f"/media/sessions/{session_id}/export/sprite.json",
        "frame_width": json.loads(json_path.read_text())["frameWidth"],
        "frame_height": json.loads(json_path.read_text())["frameHeight"],
        "total_frames": len(frames),
    })


@csrf_exempt
@require_http_methods(["GET"])
def api_models(request):
    """List available AI matting models and backends with cache status."""
    from pathlib import Path

    # Determine which models are already cached (downloaded)
    cache_dir = Path.home() / ".u2net"
    models_info = {}
    for key, (session_name, desc) in REMBG_MODELS.items():
        cached = (cache_dir / f"{session_name}.onnx").exists()
        models_info[key] = {
            "session_name": session_name,
            "description": desc,
            "cached": cached,
            "status": "ready" if cached else "needs_download",
        }

    return JsonResponse({
        "backends": {
            "remgb": "Static frame-by-frame matting via rembg (fast, many models)",
            "rvm": "Robust Video Matting — temporal coherence, best for video",
        },
        "models": models_info,
        "despill_methods": {
            "average": "Green limited to avg(R,B) — balanced neutralization",
            "red": "Green limited to Red channel — best for skin tones",
        },
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_import(request):
    """Import a sprite sheet: split it back into individual frames for editing."""
    sprite_file = request.FILES.get("sprite")
    meta_file = request.FILES.get("meta")

    if not sprite_file:
        return JsonResponse({"error": "No sprite sheet PNG."}, status=400)

    # Parse metadata — from uploaded JSON file, or inline JSON, or defaults
    meta = {}
    if meta_file:
        try:
            meta = json.loads(meta_file.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"error": "Invalid metadata JSON."}, status=400)
    else:
        # Try inline JSON from the text field
        inline = request.POST.get("meta_json", "")
        if inline:
            try:
                meta = json.loads(inline)
            except json.JSONDecodeError:
                return JsonResponse({"error": "Invalid inline metadata JSON."}, status=400)

    required = ["frameWidth", "frameHeight", "totalFrames", "columns", "rows"]
    missing = [k for k in required if k not in meta]
    if missing:
        return JsonResponse({"error": f"Metadata missing: {', '.join(missing)}"}, status=400)

    # The metadata comes from an uploaded file, so bound every value that
    # drives an allocation or a loop before acting on it.
    try:
        dims = {k: int(meta[k]) for k in required}
    except (TypeError, ValueError):
        return JsonResponse({"error": "Metadata values must be integers."}, status=400)

    if any(v < 1 for v in dims.values()):
        return JsonResponse({"error": "Metadata values must be positive."}, status=400)
    if dims["frameWidth"] > MAX_FRAME_DIM or dims["frameHeight"] > MAX_FRAME_DIM:
        return JsonResponse(
            {"error": f"Frame dimensions must be ≤ {MAX_FRAME_DIM}px."}, status=400)
    if dims["totalFrames"] > MAX_IMPORT_FRAMES:
        return JsonResponse(
            {"error": f"Sprite sheets are limited to {MAX_IMPORT_FRAMES} frames."},
            status=400)
    if dims["totalFrames"] > dims["columns"] * dims["rows"]:
        return JsonResponse(
            {"error": "totalFrames exceeds the columns × rows grid."}, status=400)

    session_id = uuid.uuid4().hex[:12]
    sd = _session_dir(session_id)
    frames_dir = sd / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    try:
        from PIL import Image
        import io
        sheet = Image.open(io.BytesIO(sprite_file.read())).convert("RGBA")

        fw, fh = dims["frameWidth"], dims["frameHeight"]
        cols, rows = dims["columns"], dims["rows"]
        total = dims["totalFrames"]
        fps = int(meta.get("fps", 10) or 10)

        for i in range(total):
            col = i % cols
            row = i // cols
            x, y = col * fw, row * fh
            frame = sheet.crop((x, y, x + fw, y + fh))
            name = f"frame_{i + 1:04d}.png"
            frame.save(frames_dir / name, "PNG")

        (sd / "config.json").write_text(json.dumps({
            "fps": fps,
            "frame_width": fw,
            "frame_height": fh,
            "imported": True,
        }))

        return JsonResponse({
            "session_id": session_id,
            "frame_count": total,
            "frame_width": fw,
            "frame_height": fh,
            "redirect": f"/edit/{session_id}/",
        })
    except Exception as e:
        shutil.rmtree(sd, ignore_errors=True)
        return _server_error("Sprite sheet import failed", e)
