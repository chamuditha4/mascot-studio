"""Smoke tests for the editor views, focused on input validation.

Run with:  python manage.py test
These use a temporary MEDIA_ROOT and never touch the real media/ directory.
"""

import base64
import io
import json
import shutil
import tempfile

from django.test import Client, TestCase, override_settings
from PIL import Image


def _png_data_url(size=(10, 10), colour=(0, 255, 0, 255)) -> str:
    buf = io.BytesIO()
    Image.new("RGBA", size, colour).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _sprite_file(size=(20, 10)) -> io.BytesIO:
    buf = io.BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 255)).save(buf, "PNG")
    buf.seek(0)
    return buf


class EditorViewTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp(prefix="mascot-test-")
        cls._override = override_settings(MEDIA_ROOT=cls.media_root)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client = Client()

    def _post_json(self, url, payload):
        return self.client.post(url, data=json.dumps(payload),
                                content_type="application/json")

    def _import_sheet(self, **meta_overrides):
        meta = {"frameWidth": 10, "frameHeight": 10, "totalFrames": 2,
                "columns": 2, "rows": 1, "fps": 12}
        meta.update(meta_overrides)
        return self.client.post("/api/import/", {
            "sprite": _sprite_file(),
            "meta_json": json.dumps(meta),
        })

    # ── Path traversal ──────────────────────────────────────────────

    def test_frame_name_traversal_is_rejected(self):
        r = self._post_json("/api/save-frame/aabbccddeeff/", {
            "name": "../../../../../../tmp/pwned.png",
            "image": _png_data_url(),
        })
        self.assertEqual(r.status_code, 400)

    def test_session_id_must_be_a_hex_token(self):
        r = self._post_json("/api/save-frame/notahexid/", {
            "name": "frame_0001.png", "image": _png_data_url(),
        })
        self.assertEqual(r.status_code, 400)

    def test_save_frame_rejects_non_png_payload(self):
        r = self._post_json("/api/save-frame/aabbccddeeff/", {
            "name": "frame_0001.png", "image": "not-a-data-url",
        })
        self.assertEqual(r.status_code, 400)

    def test_save_frame_refuses_to_create_new_files(self):
        r = self._post_json("/api/save-frame/aabbccddeeff/", {
            "name": "frame_0001.png", "image": _png_data_url(),
        })
        self.assertEqual(r.status_code, 404)

    # ── Upload parameter validation ─────────────────────────────────

    def test_upload_rejects_bad_parameters(self):
        for field, value in [("width", "abc"), ("width", "999999"),
                             ("fps", "0"), ("model", "../../evil"),
                             ("backend", "bogus"), ("despill", "bogus")]:
            with self.subTest(field=field, value=value):
                r = self.client.post("/api/upload/", {
                    "video": io.BytesIO(b"fake"), field: value,
                })
                self.assertEqual(r.status_code, 400)

    def test_upload_requires_a_video(self):
        self.assertEqual(self.client.post("/api/upload/", {}).status_code, 400)

    # ── Import metadata validation ──────────────────────────────────

    def test_import_rejects_oversized_frame_count(self):
        r = self._import_sheet(totalFrames=99_999_999, columns=1, rows=1)
        self.assertEqual(r.status_code, 400)

    def test_import_rejects_non_integer_metadata(self):
        r = self._import_sheet(frameWidth="x")
        self.assertEqual(r.status_code, 400)

    def test_import_rejects_frames_exceeding_the_grid(self):
        r = self._import_sheet(totalFrames=9, columns=2, rows=1)
        self.assertEqual(r.status_code, 400)

    def test_import_requires_metadata(self):
        r = self.client.post("/api/import/", {"sprite": _sprite_file()})
        self.assertEqual(r.status_code, 400)

    # ── Happy path ──────────────────────────────────────────────────

    def test_import_edit_export_roundtrip(self):
        r = self._import_sheet()
        self.assertEqual(r.status_code, 200)
        session_id = r.json()["session_id"]
        self.assertEqual(r.json()["frame_count"], 2)

        self.assertEqual(self.client.get(f"/edit/{session_id}/").status_code, 200)

        r = self._post_json(f"/api/save-frame/{session_id}/", {
            "name": "frame_0001.png", "image": _png_data_url(),
        })
        self.assertEqual(r.status_code, 200)

        r = self.client.post(f"/api/export/{session_id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total_frames"], 2)

    def test_models_endpoint_lists_the_registry(self):
        r = self.client.get("/api/models/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("u2net", r.json()["models"])
