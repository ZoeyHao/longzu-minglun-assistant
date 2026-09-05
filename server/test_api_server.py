#!/usr/bin/env python3
from __future__ import annotations

import http.client
import io
import json
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from api_server import (
    MAX_HISTORY,
    MAX_PLAN_BODY_BYTES,
    Handler,
    HardenedHTTPServer,
    RequestError,
    TokenBucketLimiter,
    append_history,
    decode_image_data_url,
    json_size,
    owner_hash,
    validate_history_submission,
)


def record(index: int, owner: str) -> dict:
    return {
        "id": index,
        "owner_hash": owner,
        "nick": "测试",
        "ts": index,
        "payload": {},
        "result": {},
    }


class TestHistoryIsolation(unittest.TestCase):
    def test_owner_token_is_validated_and_hashed(self):
        self.assertIsNone(owner_hash("short"))
        self.assertEqual(owner_hash("a" * 32), owner_hash("a" * 32))
        self.assertNotEqual(owner_hash("a" * 32), owner_hash("b" * 32))

    def test_history_limit_is_per_owner(self):
        records = [record(i, "owner-a") for i in range(MAX_HISTORY + 4)]
        records.append(record(900, "owner-b"))
        merged = append_history(records, record(999, "owner-a"))
        mine = [item for item in merged if item["owner_hash"] == "owner-a"]
        self.assertEqual(len(mine), MAX_HISTORY)
        self.assertEqual(mine[-1]["id"], 999)
        self.assertTrue(any(item["owner_hash"] == "owner-b" for item in merged))

    def test_history_byte_quota_keeps_newest_records(self):
        records = [record(i, "owner-a") | {"result": {"note": "x" * 200}} for i in range(5)]
        newest = record(99, "owner-a") | {"result": {"note": "new"}}
        with patch("api_server.MAX_HISTORY_OWNER_BYTES", 600), patch("api_server.MAX_HISTORY_FILE_BYTES", 900):
            merged = append_history(records, newest)
        self.assertEqual(merged[-1]["id"], 99)
        self.assertLessEqual(json_size(merged), 900)

    def test_history_submission_rejects_oversized_fields(self):
        with self.assertRaises(RequestError) as caught:
            validate_history_submission({"nick": "测试", "payload": {}, "result": {"note": "x" * 2001}})
        self.assertEqual(caught.exception.status, 400)


class TestInputHardening(unittest.TestCase):
    @staticmethod
    def png_data_url() -> str:
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
        import base64
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    def test_image_declared_format_must_match_content(self):
        data_url = self.png_data_url().replace("image/png", "image/jpeg")
        with self.assertRaises(RequestError) as caught:
            decode_image_data_url(data_url)
        self.assertEqual(caught.exception.status, 400)

    def test_image_pixel_limit_is_enforced(self):
        with patch("api_server.MAX_IMAGE_PIXELS", 10):
            with self.assertRaises(RequestError) as caught:
                decode_image_data_url(self.png_data_url())
        self.assertEqual(caught.exception.status, 413)

    def test_token_bucket_rejects_burst(self):
        limiter = TokenBucketLimiter()
        key = ("127.0.0.1", "POST", "/api/plan")
        self.assertTrue(limiter.allow(key, 1, 60)[0])
        allowed, retry_after = limiter.allow(key, 1, 60)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)


class TestHTTPBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HardenedHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        connection.close()
        return result

    def test_security_headers_are_on_api_responses(self):
        status, headers, _ = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_plan_rejects_non_json_content_type_before_work(self):
        status, _, payload = self.request(
            "POST", "/api/plan", b"{}", {"Content-Type": "text/plain", "Content-Length": "2"},
        )
        self.assertEqual(status, 415)
        self.assertIn("application/json", json.loads(payload)["error"])

    def test_plan_rejects_oversized_declared_body(self):
        status, _, _ = self.request(
            "POST",
            "/api/plan",
            b"{}",
            {"Content-Type": "application/json", "Content-Length": str(MAX_PLAN_BODY_BYTES + 1)},
        )
        self.assertEqual(status, 413)

    def test_plan_timeout_returns_actionable_gateway_timeout(self):
        body = b"{}"
        with patch("api_server.run_script", return_value=(-1, "", "计算超时")):
            status, _, payload = self.request(
                "POST",
                "/api/plan",
                body,
                {"Content-Type": "application/json", "Content-Length": str(len(body))},
            )
        self.assertEqual(status, 504)
        self.assertIn("减少参与元素", json.loads(payload)["error"])

    def test_cross_origin_write_is_rejected(self):
        status, _, _ = self.request(
            "POST",
            "/api/history",
            b"{}",
            {"Content-Type": "application/json", "Content-Length": "2", "Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
