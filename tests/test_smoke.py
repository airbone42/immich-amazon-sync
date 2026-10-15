"""Smoke tests for pure-logic helpers in immich_sync."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Allow importing from src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TestState(unittest.TestCase):
    """Test load_state / save_state round-trip."""

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with patch("immich_sync.STATE_FILE", state_file):
                from immich_sync import load_state, save_state

                state = load_state()
                self.assertEqual(state["synced"], {})
                self.assertEqual(state["version"], 2)

                state["synced"] = {"asset1": "node1", "asset2": "node2"}
                state["album_node_id"] = "album1"
                save_state(state)
                self.assertEqual(load_state(), state)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "nonexistent" / "state.json"
            with patch("immich_sync.STATE_FILE", state_file):
                from immich_sync import load_state

                self.assertEqual(load_state()["synced"], {})

    def test_migrates_original_format_without_reupload(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            state_file.write_text(json.dumps({"synced": {"a1": "n1", "a2": "n2"}}))
            with patch("immich_sync.STATE_FILE", state_file):
                from immich_sync import load_state

                state = load_state()
                self.assertEqual(state["version"], 2)
                self.assertEqual(state["synced"], {"a1": "n1", "a2": "n2"})
                self.assertIsNone(state["album_node_id"])
                self.assertEqual(state["pending_album_add"], [])

    def test_corrupt_state_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            state_file.write_text("{not json")
            with patch("immich_sync.STATE_FILE", state_file):
                from immich_sync import FatalError, load_state

                with self.assertRaises(FatalError):
                    load_state()

    def test_atomic_write_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with patch("immich_sync.STATE_FILE", state_file), patch("immich_sync.DRY_RUN", False):
                from immich_sync import load_state, save_state

                save_state(load_state())
                self.assertEqual([p.name for p in Path(tmp).iterdir()], ["state.json"])

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with patch("immich_sync.STATE_FILE", state_file), patch("immich_sync.DRY_RUN", True):
                from immich_sync import load_state, save_state

                save_state(load_state())
                self.assertFalse(state_file.exists())


class TestEncodeMultipart(unittest.TestCase):
    """Test _encode_multipart produces valid multipart body."""

    def test_structure(self):
        from immich_sync import _encode_multipart

        body, content_type = _encode_multipart(
            {
                "field1": (None, b"value1", "text/plain"),
                "field2": ("file.txt", b"file content", "application/octet-stream"),
            }
        )
        length = len(body)
        body = body.read()
        self.assertEqual(length, len(body))

        self.assertIn("multipart/form-data; boundary=", content_type)
        boundary = content_type.split("boundary=")[1]

        self.assertIn(f"--{boundary}".encode(), body)
        self.assertIn(f"--{boundary}--".encode(), body)
        self.assertIn(b"value1", body)
        self.assertIn(b"file content", body)
        self.assertIn(b'name="field1"', body)
        self.assertIn(b'filename="file.txt"', body)

    def test_no_filename(self):
        from immich_sync import _encode_multipart

        body, _ = _encode_multipart(
            {"meta": (None, b'{"key": "val"}', "application/json")}
        )
        body = body.read()

        self.assertNotIn(b"filename=", body)
        self.assertIn(b'name="meta"', body)

    def test_streams_file_in_small_reads(self):
        from immich_sync import _encode_multipart

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob"
            path.write_bytes(bytes(range(256)) * 100)
            with open(path, "rb") as f:
                stream, _ = _encode_multipart({"content": ("x.bin", f, "application/octet-stream")})
                expected_len = len(stream)
                out = io.BytesIO()
                while chunk := stream.read(1000):
                    self.assertLessEqual(len(chunk), 1000)
                    out.write(chunk)
        self.assertEqual(len(out.getvalue()), expected_len)
        self.assertIn(bytes(range(256)) * 100, out.getvalue())


class TestUrlWithParams(unittest.TestCase):
    """Test _url_with_params URL construction."""

    def test_base_only(self):
        from immich_sync import _url_with_params, AMAZON_BASE_PARAMS

        result = _url_with_params("https://example.com/path")
        self.assertEqual(result, f"https://example.com/path?{AMAZON_BASE_PARAMS}")

    def test_with_extra_params(self):
        from immich_sync import _url_with_params, AMAZON_BASE_PARAMS

        result = _url_with_params("https://example.com/path", "foo=bar")
        self.assertEqual(
            result, f"https://example.com/path?{AMAZON_BASE_PARAMS}&foo=bar"
        )


class TestSafeFilename(unittest.TestCase):
    """Untrusted originalFileName must never become a path or break headers."""

    ASSET = "0b0e6d5a-1c1d-4b8e-9c3a-2f1e0d9c8b7a"

    def check(self, raw, mime=None):
        from immich_sync import safe_filename

        name = safe_filename(raw, self.ASSET, mime)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotIn('"', name)
        self.assertNotIn("\r", name)
        self.assertNotIn("\n", name)
        self.assertNotIn(name, ("", ".", ".."))
        return name

    def test_plain(self):
        self.assertEqual(self.check("IMG_0001.HEIC"), "IMG_0001.HEIC")

    def test_traversal(self):
        self.assertEqual(self.check("../../etc/passwd"), "passwd")
        self.assertEqual(self.check("..\\..\\win.ini"), "win.ini")
        self.assertEqual(self.check(".."), f"{self.ASSET}.jpg")

    def test_absolute_and_slash(self):
        self.assertEqual(self.check("/abs/path/photo.jpg"), "photo.jpg")
        self.assertEqual(self.check("dir/"), "dir")
        self.assertEqual(self.check("/"), f"{self.ASSET}.jpg")

    def test_quotes_and_crlf(self):
        self.assertEqual(self.check('a"b.jpg'), "ab.jpg")
        self.assertEqual(
            self.check('x.jpg"\r\nX-Injected: 1\r\n\r\n'),
            "x.jpgX-Injected: 1",
        )
        self.assertEqual(self.check("tab\there\x00.png"), "tabhere.png")

    def test_empty_uses_asset_id_and_extension(self):
        self.assertEqual(self.check(""), f"{self.ASSET}.jpg")
        self.assertEqual(self.check(None), f"{self.ASSET}.jpg")
        self.assertEqual(self.check("", "image/png"), f"{self.ASSET}.png")
        self.assertEqual(self.check("\r\n", "video/mp4"), f"{self.ASSET}.mp4")

    def test_long_name_keeps_extension(self):
        name = self.check("a" * 400 + ".jpeg")
        self.assertLessEqual(len(name), 200)
        self.assertTrue(name.endswith(".jpeg"))


class TestRegion(unittest.TestCase):
    """Hosts and cookie names are derived from AP_TLD."""

    def test_germany(self):
        from immich_sync import region_settings

        r = region_settings("de")
        self.assertEqual(r["drive_url"], "https://www.amazon.de/drive/v1")
        self.assertEqual(r["content_url"], "https://content-eu.drive.amazonaws.com/cdproxy")
        self.assertEqual(r["ubid_key"], "ubid-acbde")
        self.assertEqual(r["at_key"], "at-acbde")

    def test_us(self):
        from immich_sync import region_settings

        r = region_settings("com")
        self.assertEqual(r["drive_url"], "https://www.amazon.com/drive/v1")
        self.assertEqual(r["content_url"], "https://content-na.drive.amazonaws.com/cdproxy")
        self.assertEqual((r["ubid_key"], r["at_key"]), ("ubid-main", "at-main"))

    def test_uk(self):
        from immich_sync import region_settings

        r = region_settings("co.uk")
        self.assertEqual(r["drive_url"], "https://www.amazon.co.uk/drive/v1")
        self.assertEqual(r["content_url"], "https://content-eu.drive.amazonaws.com/cdproxy")
        self.assertEqual((r["ubid_key"], r["at_key"]), ("ubid-acbuk", "at-acbuk"))

    def test_unknown_region_has_no_upload_host(self):
        from immich_sync import region_settings

        self.assertIsNone(region_settings("co.jp")["content_url"])

    def test_invalid_tld(self):
        from immich_sync import ConfigError, region_settings

        for bad in ("evil.com/x", "", "d e"):
            with self.assertRaises(ConfigError):
                region_settings(bad)


if __name__ == "__main__":
    unittest.main()
