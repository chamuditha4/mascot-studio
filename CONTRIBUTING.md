# Contributing

Thanks for taking a look. Issues and pull requests are welcome.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py test
```

You also need `ffmpeg` on your `PATH` to process real videos. The test suite
does not need it — it exercises the views with synthetic PNGs and never loads
a matting model.

## Before opening a PR

- `python manage.py check` and `python manage.py test` both pass.
- New or changed view behaviour has a test in [editor/tests.py](editor/tests.py).
- Anything reading a user-supplied path, filename, or session id stays behind
  the validators in [editor/views.py](editor/views.py) — no raw user strings in
  filesystem paths.
- No media files, `.env`, or model weights in the commit.

## Scope

Mascot Studio is a local, single-user tool. Features that assume a hosted
multi-tenant deployment (accounts, quotas, billing) are out of scope; adding
authentication in front of it is something to do in your own deployment.
