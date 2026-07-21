# Mascot Studio 🎬

Web-based tool for turning green-screen character videos into mobile-ready
sprite sheets, with AI background removal and frame-by-frame touch-up in the
browser.

Upload an MP4, let the matting model cut out the background, clean up the
stray pixels by hand with a canvas eraser, and export a packed sprite sheet
plus a metadata JSON that any game or animation runtime can consume.

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) on your `PATH` (used for frame extraction)

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # optional for local dev
python manage.py runserver
# Open http://127.0.0.1:8000
```

The first run downloads the selected matting model (~100–900 MB depending on
the model) into `~/.u2net/`. Later runs reuse the cache.

## Examples

[`examples/`](examples/) has two complete jobs — the green-screen source video
plus the sprite sheet and metadata the tool produced from it:

| Example | Source | Output | Frames |
|---------|--------|--------|--------|
| Celebrating | [`.mp4`](examples/Celebrating/Celebrating.mp4) | [`.png`](examples/Celebrating/Celebrating.png) + [`.json`](examples/Celebrating/Celebrating.json) | 40 @ 10fps |
| Concerned | [`.mp4`](examples/Concerned/Concerned.mp4) | [`.png`](examples/Concerned/Concerned.png) + [`.json`](examples/Concerned/Concerned.json) | 40 @ 10fps |

Upload a `.mp4` to exercise the full pipeline, or import a finished `.png` +
`.json` pair to jump straight into the editor without waiting on the model.

## Workflow

1. **Upload** a green-screen video (MP4).
2. The matting model removes the background and the despill pass neutralises
   green light bleeding onto the subject's edges.
3. **Edit** frame by frame with the canvas eraser, or re-run the AI on a
   single bad frame with a different model.
4. **Export** a sprite sheet + metadata JSON.

You can also **import** an existing sprite sheet (PNG + metadata JSON) to
split it back into editable frames.

## Features

- Eight AI matting models (U²-Net, ISNet, BiRefNet, BEN2) selectable per job
- Optional temporal matting backend (Robust Video Matting) for flicker-free
  edges on moving subjects
- Luminance-preserving, alpha-gated green despill that leaves sub-pixel edge
  geometry (hair, fingers, motion blur) intact
- Canvas eraser with variable brush size, per-frame undo
- Filmstrip navigation and animation playback preview
- Export to a GPU-safe sprite sheet (max 4096×4096)
- Keyboard shortcuts: `←`/`→` navigate, `E` erase, `Ctrl+Z` undo, `Ctrl+S` save

### Optional: temporal matting backend

The `rvm` backend uses [Robust Video Matting](https://github.com/PeterL1n/RobustVideoMatting),
a recurrent network that carries state across frames and largely eliminates
edge boiling. It needs PyTorch:

```bash
pip install -r requirements-rvm.txt
```

## Configuration

All settings are read from the environment — see [.env.example](.env.example).

| Variable | Default | Purpose |
|----------|---------|---------|
| `DJANGO_SECRET_KEY` | — | Required when `DJANGO_DEBUG=0` |
| `DJANGO_DEBUG` | `1` | Debug pages; also serves `/media/` from the dev server |
| `DJANGO_ALLOWED_HOSTS` | — | Comma-separated hosts, used when debug is off |
| `MAX_UPLOAD_MB` | `200` | Upload size ceiling |

## Deployment note

**This is a single-user local tool, not a multi-tenant service.** There is no
authentication, and anyone who can reach the server can upload videos, run GPU
work, and read every session's frames by guessing a session id. Run it on
`127.0.0.1`, or put it behind your own auth layer and a real WSGI server before
exposing it. With `DJANGO_DEBUG=0` you must also serve `media/` yourself —
Django's dev-server media route is disabled outside debug.

Uploaded videos, frames, and exports accumulate under `media/sessions/` and are
never garbage-collected; prune that directory yourself.

## Project structure

```
mascot-studio/
├── manage.py
├── requirements.txt
├── mascot_studio/        # Django settings
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── editor/               # Django app
│   ├── views.py          # Pages + API endpoints
│   ├── urls.py
│   └── templates/editor/
│       ├── upload.html   # Video upload + processing page
│       └── edit.html     # Canvas frame editor
├── processor/            # Processing pipeline
│   └── pipeline.py       # Frame extraction, matting, despill, stitching
└── media/                # Uploads, frames, exports (created at runtime)
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/` | Upload page |
| `GET`  | `/edit/<session>/` | Frame editor |
| `POST` | `/api/upload/` | Upload video, trigger processing |
| `GET`  | `/api/models/` | List matting models and cache status |
| `POST` | `/api/import/` | Import a sprite sheet back into frames |
| `POST` | `/api/save-frame/<session>/` | Save an edited frame |
| `POST` | `/api/reprocess-frame/<session>/` | Re-run AI on one frame |
| `POST` | `/api/export/<session>/` | Generate sprite sheet |

## Sprite sheet format

Export produces `sprite.png` and `sprite.json`:

```json
{
  "frameWidth": 320, "frameHeight": 480,
  "totalFrames": 24, "fps": 10, "duration": 2.4,
  "columns": 5, "rows": 5,
  "sheetWidth": 1600, "sheetHeight": 2400
}
```

Frames are packed left-to-right, top-to-bottom, so frame `i` sits at
`(i % columns * frameWidth, i // columns * frameHeight)`.

## Hire me

I build the mobile side of projects like this one. If you're looking for an
iOS or Android developer, I'm available at **$1,500/month** to work on your
mobile app.

**[jayasena.dev](https://jayasena.dev/)** — portfolio and contact.

## License

MIT — see [LICENSE](LICENSE).
