"""Device login: registration, cookie exchange before each run, deregistration (HTTP mocked)."""

import base64
import hashlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import immich_sync  # noqa: E402
from test_sync import SECRETS, Backend, FakeResponse, SyncTestCase, aid  # noqa: E402

REFRESH = "Atnr|refresh-secret-777"
ACCESS = "Atna|access-secret-888"
CODE = "ANcodeSecret999xyz"


def cookie_list(**values):
    return [{"Name": k.replace("_", "-"), "Value": v, "Path": "/", "Secure": "true"}
            for k, v in values.items()]


class IdentityBackend(Backend):
    """Backend plus Amazon's identity endpoints (register, exchange, token, deregister)."""

    def __init__(self):
        super().__init__()
        self.exchange_status = None
        self.register_status = None
        self.drive_rejects = False
        self.deregistered = []
        self.headers = []  # (url, headers) of every request
        self.exchanges = 0

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.headers.append((url, dict(req.header_items())))
        data = req.data
        if url.startswith("https://api.amazon.de/auth/register"):
            body = json.loads(data)
            self.register_body = body
            if self.register_status:
                raise HTTPError(url, self.register_status, "bad", {}, io.BytesIO(json.dumps(
                    {"response": {"error": {"code": "InvalidValue", "message": "code expired"}}}).encode()))
            return FakeResponse({"response": {"success": {
                "tokens": {
                    "bearer": {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": "3600"},
                    "website_cookies": cookie_list(session_id="262-5555555-6666666",
                                                   ubid_acbde="ubid-from-registration",
                                                   at_acbde="Atza|at-from-registration"),
                },
                "extensions": {"device_info": {"device_name": "Tobias' Audible for iPhone"}},
            }}})
        if url == "https://www.amazon.de/ap/exchangetoken/cookies":
            self.exchanges += 1
            form = parse_qs(data.decode())
            self.exchange_form = form
            if self.exchange_status:
                raise HTTPError(url, self.exchange_status, "bad", {}, io.BytesIO(json.dumps(
                    {"response": {"error": {"code": "InvalidToken", "message": "token revoked"}}}).encode()))
            assert form["source_token"] == [REFRESH]
            return FakeResponse({"response": {"tokens": {"cookies": {".amazon.de": cookie_list(
                at_acbde=f"Atza|fresh-at-{self.exchanges}",
                x_acbde='"fresh-x-secret"',
                sess_at_acbde='"fresh-sess-at"',
                session_token="fresh-session-token",
                ubid_acbde="ubid-fresh",
            )}}}})
        if url == "https://api.amazon.de/auth/token":
            return FakeResponse({"access_token": ACCESS, "token_type": "bearer", "expires_in": 3600})
        if url == "https://api.amazon.de/auth/deregister":
            self.deregistered.append(dict(req.header_items()).get("Authorization"))
            return FakeResponse({"response": {"success": {}}})
        if self.drive_rejects and url.startswith("https://www.amazon.de/drive/"):
            raise HTTPError(url, 401, "no", {}, io.BytesIO(b'{"message":"Unauthorized"}'))
        return super().__call__(req, timeout)

    def drive_headers(self):
        return [h for u, h in self.headers if u.startswith("https://www.amazon.de/drive/")]


def device_file_data(**overrides):
    data = {"version": 1, "tld": "de", "device_serial": "ABC", "device_name": "Tobias' Audible for iPhone",
            "registered_at": "2026-11-03T12:00:00+00:00", "refresh_token": REFRESH,
            "base_cookies": {"session-id": "262-5555555-6666666"}}
    data.update(overrides)
    return data


class TestLoginUrl(unittest.TestCase):
    def test_pkce_and_client_id(self):
        url = immich_sync.device_login_url("de", "SERIAL1", "verifier-123")
        parts = urlsplit(url)
        self.assertEqual((parts.scheme, parts.netloc, parts.path), ("https", "www.amazon.de", "/ap/signin"))
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        expected = base64.urlsafe_b64encode(hashlib.sha256(b"verifier-123").digest()).rstrip(b"=").decode()
        self.assertEqual(q["openid.oa2.code_challenge"], expected)
        self.assertEqual(q["openid.oa2.code_challenge_method"], "S256")
        self.assertEqual(q["openid.oa2.client_id"], "device:" + b"SERIAL1#A2CZJZGLK2JJVM".hex())
        self.assertEqual(q["openid.return_to"], "https://www.amazon.de/ap/maplanding")
        self.assertEqual(q["openid.assoc_handle"], "amzn_audible_ios_de")
        self.assertEqual(q["marketPlaceId"], "AN7V1F1VY261K")

    def test_unknown_marketplace(self):
        with self.assertRaises(immich_sync.ConfigError):
            immich_sync.device_login_url("co.jp", "S", "v")


class TestParseCode(unittest.TestCase):
    def test_full_address(self):
        url = ("https://www.amazon.de/ap/maplanding?openid.assoc_handle=amzn_audible_ios_de"
               f"&openid.oa2.authorization_code={CODE}&openid.mode=id_res")
        self.assertEqual(immich_sync.parse_authorization_code(f"  {url}\n", "de"), CODE)

    def test_bare_code(self):
        self.assertEqual(immich_sync.parse_authorization_code(CODE, "de"), CODE)

    def test_rejects_other_hosts_and_garbage(self):
        for bad in (f"https://evil.example/ap/maplanding?openid.oa2.authorization_code={CODE}",
                    f"http://www.amazon.de/ap/maplanding?openid.oa2.authorization_code={CODE}",
                    "https://www.amazon.de/ap/maplanding?openid.mode=cancel",
                    "abc", "code with spaces in it"):
            with self.assertRaises(ValueError, msg=bad):
                immich_sync.parse_authorization_code(bad, "de")


class DeviceTestCase(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.backend = IdentityBackend()
        # device mode needs no cookie secrets
        self.env = {k: v for k, v in self.env.items() if not k.startswith("AP_") or k == "AP_ALBUM_NAME"}
        self.device_file = self.state_dir / "amazon_device.json"

    def write_device(self, **overrides):
        self.device_file.write_text(json.dumps(device_file_data(**overrides)))

    def run_cli(self, command, pasted=""):
        with patch.dict(os.environ, self.env, clear=True), \
                patch("immich_sync.urlopen", self.backend), \
                patch("immich_sync.getpass.getpass", return_value=pasted), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = immich_sync.main([command])
        self.output = out.getvalue() + err.getvalue()
        return code

    def assert_no_secrets_in_output(self):
        for secret in (REFRESH, ACCESS, CODE, "fresh-x-secret", "fresh-session-token", "fresh-at-"):
            self.assertNotIn(secret, self.output)
        log_file = self.state_dir / "immich-sync.log"
        if log_file.exists():
            text = log_file.read_text()
            for secret in (REFRESH, "fresh-x-secret", "fresh-session-token"):
                self.assertNotIn(secret, text)


class TestDeviceSync(DeviceTestCase):
    def test_run_uses_fresh_cookies(self):
        self.write_device()
        self.backend.add_album("Echo", [aid(1)])
        self.assertEqual(self.run_sync(), immich_sync.EXIT_OK, self.output)
        self.assertEqual(self.backend.exchanges, 1)
        headers = self.backend.drive_headers()
        self.assertTrue(headers)
        cookie = headers[0]["Cookie"]
        self.assertIn("at-acbde=Atza|fresh-at-1", cookie)
        self.assertIn('x-acbde="fresh-x-secret"', cookie)
        self.assertIn("session-token=fresh-session-token", cookie)
        self.assertIn("ubid-acbde=ubid-fresh", cookie)
        # session-id is not part of the exchange reply: taken over from the registration
        self.assertIn("session-id=262-5555555-6666666", cookie)
        self.assertEqual(headers[0]["X-amzn-sessionid"], "262-5555555-6666666")
        self.assertIn("fresh cookies from the device login", self.output)
        self.assert_no_secrets_in_output()

    def test_device_file_wins_over_cookie_secrets(self):
        self.write_device()
        self.env.update(SECRETS)
        self.backend.add_album("Echo", [aid(1)])
        self.assertEqual(self.run_sync(), immich_sync.EXIT_OK, self.output)
        self.assertNotIn(SECRETS["AP_AT_VALUE"], self.backend.drive_headers()[0]["Cookie"])

    def test_revoked_device_is_auth_error(self):
        self.write_device()
        self.backend.add_album("Echo", [aid(1)])
        self.backend.exchange_status = 400
        self.assertEqual(self.run_sync(), immich_sync.EXIT_AUTH)
        self.assertIn("device login expired or removed", self.output)
        self.assertIn("amazon-login", self.output)
        self.assertIn("token revoked", self.output)
        self.assertEqual(self.backend.amazon_writes(), [])
        self.assert_no_secrets_in_output()

    def test_throttling_is_not_auth_error(self):
        self.write_device()
        self.backend.add_album("Echo", [aid(1)])
        self.backend.exchange_status = 429
        self.assertEqual(self.run_sync(), immich_sync.EXIT_ERROR)

    def test_wrong_tld_or_corrupt_file(self):
        self.write_device(tld="fr")
        self.assertEqual(self.run_sync(), immich_sync.EXIT_ERROR)
        self.assertIn("AP_TLD", self.output)
        self.device_file.write_text("{broken")
        self.assertEqual(self.run_sync(), immich_sync.EXIT_ERROR)
        self.assertIn("amazon-login", self.output)

    def test_without_device_file_cookie_secrets_are_required(self):
        self.assertEqual(self.run_sync(), immich_sync.EXIT_ERROR)
        self.assertIn("AP_SESSION_ID", self.output)


class TestLoginCommand(DeviceTestCase):
    MAPLANDING = f"https://www.amazon.de/ap/maplanding?openid.oa2.authorization_code={CODE}&openid.mode=id_res"

    def test_login_registers_and_saves(self):
        self.assertEqual(self.run_cli("amazon-login", self.MAPLANDING), immich_sync.EXIT_OK, self.output)
        data = json.loads(self.device_file.read_text())
        self.assertEqual(data["refresh_token"], REFRESH)
        self.assertEqual(data["tld"], "de")
        self.assertEqual(data["base_cookies"], {"session-id": "262-5555555-6666666",
                                                "ubid-acbde": "ubid-from-registration"})
        self.assertEqual(self.device_file.stat().st_mode & 0o777, 0o600)
        auth = self.backend.register_body["auth_data"]
        self.assertEqual(auth["authorization_code"], CODE)
        self.assertEqual(auth["client_id"], immich_sync.device_client_id(data["device_serial"]))
        # the verifier sent at registration matches the challenge in the printed sign-in address
        url = next(line for line in self.output.splitlines() if line.startswith("https://www.amazon.de/ap/signin"))
        challenge = parse_qs(urlsplit(url).query)["openid.oa2.code_challenge"][0]
        self.assertEqual(immich_sync._b64url(hashlib.sha256(auth["code_verifier"].encode()).digest()), challenge)
        self.assertIn("Tobias' Audible for iPhone", self.output)
        self.assertEqual(self.backend.deregistered, [])
        self.assert_no_secrets_in_output()

    def test_login_rejected_by_drive_removes_device(self):
        self.backend.drive_rejects = True
        self.assertEqual(self.run_cli("amazon-login", self.MAPLANDING), immich_sync.EXIT_AUTH)
        self.assertFalse(self.device_file.exists())
        self.assertEqual(self.backend.deregistered, [f"Bearer {ACCESS}"])
        self.assertIn("Nothing was saved", self.output)
        self.assert_no_secrets_in_output()

    def test_login_with_expired_code(self):
        self.backend.register_status = 400
        self.assertEqual(self.run_cli("amazon-login", self.MAPLANDING), immich_sync.EXIT_AUTH)
        self.assertIn("code expired", self.output)
        self.assertFalse(self.device_file.exists())

    def test_login_with_wrong_paste(self):
        self.assertEqual(self.run_cli("amazon-login", "https://www.amazon.de/"), immich_sync.EXIT_ERROR)
        self.assertFalse(self.device_file.exists())

    def test_login_again_replaces_previous_device(self):
        self.write_device(refresh_token="Atnr|old-refresh-token")
        self.assertEqual(self.run_cli("amazon-login", self.MAPLANDING), immich_sync.EXIT_OK, self.output)
        self.assertEqual(json.loads(self.device_file.read_text())["refresh_token"], REFRESH)
        self.assertEqual(len(self.backend.deregistered), 1)
        self.assertIn("Removed the previous device", self.output)

    def test_logout(self):
        self.write_device()
        self.assertEqual(self.run_cli("amazon-logout"), immich_sync.EXIT_OK, self.output)
        self.assertFalse(self.device_file.exists())
        self.assertEqual(self.backend.deregistered, [f"Bearer {ACCESS}"])
        self.assert_no_secrets_in_output()

    def test_unknown_command(self):
        self.assertEqual(self.run_cli("amazon-dance"), immich_sync.EXIT_ERROR)
        self.assertIn("Usage", self.output)


if __name__ == "__main__":
    unittest.main()
