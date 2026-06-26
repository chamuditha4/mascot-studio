# Mascot Studio 🎬

Web-based tool for processing green-screen mascot videos into
mobile-ready sprite sheets with frame-by-frame editing.

## Quick start

```bash
pip install -r requirements.txt
python manage.py runserver
# Open http://localhost:8000
```

## Workflow

1. **Upload** a green-screen mascot video (MP4)
2. AI removes the background (rembg) and auto-polishes the frames
3. **Edit** frame-by-frame with the canvas eraser tool
4. **Export** as a sprite sheet + metadata JSON ready for React Native

## Features

- AI background removal (rembg u2net)
- Auto-cleanup: alpha sharpen + green-screen colour kill
- Canvas eraser with variable brush size
- Frame filmstrip navigation
- Undo per frame (Ctrl+Z)
- Animation playback preview
- Export to 4096² GPU-safe sprite sheet
- Keyboard shortcuts: ← → navigate, E erase, Ctrl+Z undo, Ctrl+S save

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
│   ├── views.py          # Upload, editor, API endpoints
│   ├── urls.py
│   └── templates/editor/
│       ├── upload.html   # Video upload + processing page
│       └── edit.html     # Canvas frame editor
├── processor/            # Processing pipeline
│   └── pipeline.py       # Frame extraction, rembg, cleanup, stitch
└── media/                # Uploads, frames, exports (created at runtime)
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/` | Upload page |
| `GET`  | `/edit/<session>/` | Frame editor |
| `POST` | `/api/upload/` | Upload video, trigger processing |
| `POST` | `/api/save-frame/<session>/` | Save edited frame |
| `POST` | `/api/export/<session>/` | Generate sprite sheet |

## Integration with NotePal Mobile

The exported sprite sheet (`.png` + `.json`) is directly compatible with
`MascotAnimation.tsx` in the NotePal mobile app.  Copy the files to
`assets/mascots/` and update the import paths.
