import json
import os
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from processor.pipeline import extract_frames, remove_backgrounds, stitch_sheet


def _session_dir(session_id: str) -> Path:
    d = Path(settings.MEDIA_ROOT) / "sessions" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


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
    """Upload a video, extract frames, run AI cleanup."""
    video = request.FILES.get("video")
    if not video:
        return JsonResponse({"error": "No video file."}, status=400)

    width = int(request.POST.get("width", 500))
    fps = int(request.POST.get("fps", 10))

    session_id = uuid.uuid4().hex[:12]
    sd = _session_dir(session_id)

    # Save video
    video_path = sd / "input.mp4"
    with open(video_path, "wb") as f:
        for chunk in video.chunks():
            f.write(chunk)

    try:
        # Extract
        raw_dir = sd / "raw"
        raw_frames = extract_frames(video_path, raw_dir, width, fps)
        fw, fh = 0, 0
        if raw_frames:
            from PIL import Image
            fw, fh = Image.open(raw_frames[0]).size

        # AI cleanup
        frames_dir = sd / "frames"
        clean_frames = remove_backgrounds(raw_frames, frames_dir)

        # Save config
        (sd / "config.json").write_text(json.dumps({
            "width": width, "fps": fps,
            "frame_width": fw, "frame_height": fh,
        }))

        # Clean up raw
        shutil.rmtree(raw_dir)

        return JsonResponse({
            "session_id": session_id,
            "frame_count": len(clean_frames),
            "frame_width": fw,
            "frame_height": fh,
            "redirect": f"/edit/{session_id}/",
        })
    except Exception as e:
        shutil.rmtree(sd, ignore_errors=True)
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_reprocess_frame(request, session_id: str):
    """Re-run rembg AI background removal on a single frame.

    Accepts the frame name.  The server re-reads the saved PNG, runs it
    through rembg + clean_frame, and overwrites the file.  Returns the
    new frame URL so the editor can refresh it.
    """
    data = json.loads(request.body)
    name = data.get("name")
    if not name:
        return JsonResponse({"error": "Missing frame name."}, status=400)

    frame_path = _session_dir(session_id) / "frames" / name
    if not frame_path.exists():
        return JsonResponse({"error": "Frame not found."}, status=404)

    try:
        import io

        import numpy as np
        from PIL import Image
        from rembg import remove, new_session
        from processor.pipeline import clean_frame

        # Composite the current frame over the green-screen colour so
        # rembg sees proper green background instead of black holes.
        # The green screen is RGB(157, 231, 162).
        GS_R, GS_G, GS_B = 157, 231, 162

        current = Image.open(frame_path).convert("RGBA")
        bg = Image.new("RGB", current.size, (GS_R, GS_G, GS_B))
        composited = Image.alpha_composite(
            bg.convert("RGBA"), current
        ).convert("RGB")

        session = new_session("u2net")
        out = remove(composited, session=session)   # AI background removal
        out = clean_frame(out)                       # alpha sharpen + colour kill
        out.save(frame_path, "PNG")

        return JsonResponse({
            "ok": True,
            "url": f"/media/sessions/{session_id}/frames/{name}",
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_save_frame(request, session_id: str):
    """Save an edited frame (overwrite the PNG with the edited data URL)."""
    data = json.loads(request.body)
    name = data.get("name")
    image_data = data.get("image")  # base64 data URL

    if not name or not image_data:
        return JsonResponse({"error": "Missing name or image."}, status=400)

    # Decode base64 data URL
    import base64
    import re
    header, encoded = image_data.split(",", 1)
    binary = base64.b64decode(encoded)

    frame_path = _session_dir(session_id) / "frames" / name
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

    session_id = uuid.uuid4().hex[:12]
    sd = _session_dir(session_id)
    frames_dir = sd / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    try:
        from PIL import Image
        import io
        sheet = Image.open(io.BytesIO(sprite_file.read())).convert("RGBA")

        fw = meta["frameWidth"]
        fh = meta["frameHeight"]
        cols = meta["columns"]
        rows = meta["rows"]
        total = meta["totalFrames"]
        fps = meta.get("fps", 10)

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
        return JsonResponse({"error": str(e)}, status=500)
