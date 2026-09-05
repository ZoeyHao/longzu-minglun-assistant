#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙族命轮助手 · 生产 API 服务器。

路由（与 vite 开发中间件一致）：
  GET  /api/game-data   游戏数据（含缓存）
  POST /api/plan        命轮规划（engine.py 子进程）
  POST /api/recognize   截图识别（recognize_api.py 子进程）

环境变量：
  MINGLUN_SKILL_DIR       skill 目录（含 scripts/ 与 references/）
  MINGLUN_RECOGNIZER_DIR  命盘截图识别目录
  MINGLUN_PORT            监听端口，默认 8321（仅绑定 127.0.0.1，由 nginx 反代）
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import ipaddress
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, UnidentifiedImageError

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable
GAME_DATA_CACHE: dict[str, str | bytes] = {}
LOCK = threading.Lock()
HISTORY_FILE = Path(os.environ.get("MINGLUN_HISTORY_FILE", str(HERE / "history.json")))
HISTORY_LOCK = threading.Lock()
MAX_HISTORY = 100
MAX_HISTORY_TOTAL = 5000
MAX_HISTORY_RECORD_BYTES = 256 * 1024
MAX_HISTORY_OWNER_BYTES = 4 * 1024 * 1024
MAX_HISTORY_FILE_BYTES = 16 * 1024 * 1024
MAX_PLAN_BODY_BYTES = 128 * 1024
MAX_RECOGNIZE_BODY_BYTES = 32 * 1024 * 1024
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGES = 6
MAX_JSON_DEPTH = 8
MAX_JSON_NODES = 15_000

PLAN_SLOTS = threading.BoundedSemaphore(max(1, int(os.environ.get("MINGLUN_PLAN_CONCURRENCY", "2"))))
RECOGNIZE_SLOTS = threading.BoundedSemaphore(max(1, int(os.environ.get("MINGLUN_RECOGNIZE_CONCURRENCY", "1"))))
EXPORT_SLOTS = threading.BoundedSemaphore(1)

RATE_LIMITS = {
    ("POST", "/api/plan"): (6, 60),
    ("POST", "/api/recognize"): (3, 60),
    ("GET", "/api/combos.xlsx"): (10, 60),
    ("GET", "/api/history"): (60, 60),
    ("POST", "/api/history"): (30, 60),
    ("DELETE", "/api/history"): (30, 60),
}

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class TokenBucketLimiter:
    """Small in-memory per-IP limiter; nginx remains the primary edge limiter."""

    def __init__(self, max_entries: int = 10_000):
        self.max_entries = max_entries
        self.entries: dict[tuple[str, str, str], tuple[float, float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: tuple[str, str, str], capacity: int, period: int) -> tuple[bool, int]:
        now = time.monotonic()
        refill = capacity / period
        with self.lock:
            tokens, last = self.entries.get(key, (float(capacity), now))
            tokens = min(float(capacity), tokens + (now - last) * refill)
            if tokens < 1:
                self.entries[key] = (tokens, now)
                return False, max(1, math.ceil((1 - tokens) / refill))
            self.entries[key] = (tokens - 1, now)
            if len(self.entries) > self.max_entries:
                stale = sorted(self.entries.items(), key=lambda item: item[1][1])[: self.max_entries // 10]
                for old_key, _ in stale:
                    self.entries.pop(old_key, None)
        return True, 0


RATE_LIMITER = TokenBucketLimiter()


def load_history() -> list[dict]:
    try:
        if HISTORY_FILE.stat().st_size > MAX_HISTORY_FILE_BYTES:
            raise ValueError("history file exceeds quota")
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as exc:
        sys.stderr.write(f"history load failed: {type(exc).__name__}\n")
        return []


def save_history(records: list[dict]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=HISTORY_FILE.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, HISTORY_FILE)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def append_history(records: list[dict], rec: dict) -> list[dict]:
    """Apply record-count and byte quotas per owner and globally."""
    owner = rec["owner_hash"]
    mine_candidates = [*filter(lambda r: r.get("owner_hash") == owner, records), rec]
    mine: list[dict] = []
    mine_bytes = 2
    for item in reversed(mine_candidates):
        item_bytes = json_size(item) + 1
        if len(mine) >= MAX_HISTORY or mine_bytes + item_bytes > MAX_HISTORY_OWNER_BYTES:
            continue
        mine.append(item)
        mine_bytes += item_bytes
    mine.reverse()
    others = [r for r in records if r.get("owner_hash") != owner]
    candidates = sorted([*others, *mine], key=lambda r: r.get("ts", 0))[-MAX_HISTORY_TOTAL:]
    kept: list[dict] = []
    total_bytes = 2
    for item in reversed(candidates):
        item_bytes = json_size(item) + 1
        if total_bytes + item_bytes > MAX_HISTORY_FILE_BYTES:
            continue
        kept.append(item)
        total_bytes += item_bytes
    kept.reverse()
    return kept


def validate_json_shape(value, *, depth: int = 0, counter: list[int] | None = None) -> None:
    """Bound nested client JSON before persisting or handing it to expensive work."""
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
        raise RequestError(400, "请求内容过于复杂")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RequestError(400, "请求数字无效")
        return
    if isinstance(value, str):
        if len(value) > 2_000:
            raise RequestError(400, "请求字段过长")
        return
    if isinstance(value, list):
        if len(value) > 500:
            raise RequestError(400, "请求数组过长")
        for item in value:
            validate_json_shape(item, depth=depth + 1, counter=counter)
        return
    if isinstance(value, dict):
        if len(value) > 500:
            raise RequestError(400, "请求对象字段过多")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80:
                raise RequestError(400, "请求字段名无效")
            validate_json_shape(item, depth=depth + 1, counter=counter)
        return
    raise RequestError(400, "请求内容类型无效")


def validate_history_submission(payload: dict) -> tuple[str, dict, dict]:
    if not isinstance(payload, dict):
        raise RequestError(400, "记录内容无效")
    nick = payload.get("nick")
    if not isinstance(nick, str):
        raise RequestError(400, "缺少昵称")
    nick = nick.strip()
    if not nick or len(nick) > 24 or any(ord(ch) < 32 for ch in nick):
        raise RequestError(400, "昵称格式无效")
    plan_payload = payload.get("payload")
    result = payload.get("result")
    if not isinstance(plan_payload, dict) or not isinstance(result, dict):
        raise RequestError(400, "记录内容不完整")
    validate_json_shape(plan_payload)
    validate_json_shape(result)
    if result.get("status") != "optimal":
        raise RequestError(400, "只能保存成功生成的方案")
    if result.get("main_element") not in {"精神", "火", "风", "水", "土"}:
        raise RequestError(400, "方案主元素无效")
    if not isinstance(result.get("plan"), list) or len(result["plan"]) > 250:
        raise RequestError(400, "方案明细无效")
    if not isinstance(result.get("not_lit"), list) or len(result["not_lit"]) > 250:
        raise RequestError(400, "未点亮明细无效")
    if not isinstance(result.get("totals"), dict) or len(result["totals"]) > 50:
        raise RequestError(400, "方案汇总无效")
    if any(
        isinstance(v, bool)
        or not isinstance(v, (int, float))
        or (isinstance(v, float) and not math.isfinite(v))
        or abs(v) > 1_000_000_000_000
        for v in result["totals"].values()
    ):
        raise RequestError(400, "方案汇总数值无效")
    if json_size(payload) > MAX_HISTORY_RECORD_BYTES:
        raise RequestError(413, "单条历史方案过大")
    return nick, plan_payload, result


def summarize(rec: dict) -> dict:
    r = rec.get("result") or {}
    main = r.get("main_element") or ""
    totals = r.get("totals") or {}
    return {
        "id": rec["id"],
        "nick": rec["nick"],
        "ts": rec["ts"],
        "main_element": main,
        "elements": r.get("elements") or [],
        "stars_lit": r.get("stars_lit", 0),
        "fragments_consumed": r.get("fragments_consumed", 0),
        "main_flat": totals.get(f"{main}元素", 0),
        "main_pct": totals.get(f"{main}元素%", 0),
        "atk": totals.get("攻击", 0),
    }


def owner_hash(token: str) -> str | None:
    token = token.strip()
    if not 24 <= len(token) <= 160:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_image_bytes(raw: bytes, declared_format: str) -> str:
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise RequestError(413, "单张图片不能超过 6 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                actual = (image.format or "").upper()
                expected = "JPEG" if declared_format in ("jpeg", "jpg") else declared_format.upper()
                if actual not in {"PNG", "JPEG", "WEBP"} or actual != expected:
                    raise RequestError(400, "图片真实格式与声明不一致")
                width, height = image.size
                if width <= 0 or height <= 0 or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                    raise RequestError(400, "图片尺寸不支持")
                if width * height > MAX_IMAGE_PIXELS:
                    raise RequestError(413, "图片总像素不能超过 1200 万")
                image.verify()
        return "jpg" if actual == "JPEG" else actual.lower()
    except RequestError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise RequestError(400, "图片内容无效") from None


def decode_image_data_url(data_url: str) -> tuple[bytes, str]:
    if not isinstance(data_url, str) or not data_url.startswith("data:image/") or ";base64," not in data_url:
        raise RequestError(400, "图片格式不支持（仅 PNG/JPG/WebP）")
    header, encoded = data_url.split(",", 1)
    declared = header.removeprefix("data:image/").removesuffix(";base64").lower()
    if declared not in {"png", "jpeg", "jpg", "webp"}:
        raise RequestError(400, "图片格式不支持（仅 PNG/JPG/WebP）")
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 8:
        raise RequestError(413, "单张图片不能超过 6 MB")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise RequestError(400, "图片 Base64 内容无效") from None
    return raw, validate_image_bytes(raw, declared)


def run_script(
    script: str,
    args: list[str],
    timeout: int,
    semaphore: threading.BoundedSemaphore | None = None,
) -> tuple[int, str, str]:
    acquired = semaphore.acquire(blocking=False) if semaphore else True
    if not acquired:
        return -2, "", "服务器繁忙，请稍后重试"
    try:
        proc = subprocess.run(
            [PYTHON, str(HERE / script), *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ},
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "计算超时"
    except OSError as exc:
        return -3, "", type(exc).__name__
    finally:
        if semaphore and acquired:
            semaphore.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "minglun"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, fmt, *args):  # 简化日志
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send_error(self, code, message=None, explain=None):
        public_message = "请求方法不支持" if code in {405, 501} else "请求无效"
        self._send_json(code, {"error": public_message})

    def _send(
        self,
        status: int,
        body: bytes,
        ctype: str = "application/json; charset=utf-8",
        headers: dict[str, str] | None = None,
    ):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload, headers: dict[str, str] | None = None):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers)

    def _client_ip(self) -> str:
        peer = self.client_address[0]
        try:
            if ipaddress.ip_address(peer).is_loopback:
                forwarded = (self.headers.get("X-Real-IP") or "").strip()
                if forwarded:
                    return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
        return peer

    def _check_rate_limit(self, method: str, path: str) -> bool:
        config = RATE_LIMITS.get((method, path))
        if not config:
            return True
        allowed, retry_after = RATE_LIMITER.allow((self._client_ip(), method, path), *config)
        if allowed:
            return True
        self._send_json(
            429,
            {"error": "请求过于频繁，请稍后重试"},
            headers={"Retry-After": str(retry_after)},
        )
        return False

    def _origin_allowed(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        allowed = {item.strip() for item in os.environ.get("MINGLUN_ALLOWED_ORIGINS", "").split(",") if item.strip()}
        if allowed:
            return origin in allowed
        parsed = urlparse(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc == (self.headers.get("Host") or "")

    def _read_json(self, max_bytes: int):
        if (self.headers.get("Transfer-Encoding") or "").strip():
            self.close_connection = True
            raise RequestError(400, "不支持分块请求体")
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self.close_connection = True
            raise RequestError(415, "请求必须使用 application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self.close_connection = True
            raise RequestError(411, "缺少 Content-Length")
        try:
            length = int(raw_length)
        except ValueError:
            self.close_connection = True
            raise RequestError(400, "Content-Length 无效") from None
        if length <= 0:
            self.close_connection = True
            raise RequestError(400, "请求体不能为空")
        if length > max_bytes:
            self.close_connection = True
            raise RequestError(413, "请求体过大")
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            raise RequestError(400, "请求体不完整")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise RequestError(400, "JSON 格式无效") from None
        validate_json_shape(payload)
        return payload

    def _reject_request(self, exc: RequestError):
        self._send_json(exc.status, {"error": exc.message})

    def _send_internal_error(self, context: str, exc) -> None:
        sys.stderr.write(f"{context}: {type(exc).__name__}\n")
        self._send_json(500, {"error": "服务器内部错误"})

    def do_GET(self):
        path = self.path.split("?")[0]
        if not self._check_rate_limit("GET", path):
            return
        if path == "/api/game-data":
            with LOCK:
                if "data" not in GAME_DATA_CACHE:
                    code, out, err = run_script("game_data.py", [], 60)
                    if code != 0:
                        sys.stderr.write(f"game_data failed: {err.strip()[-500:]}\n")
                        self._send_json(500, {"error": "游戏数据暂时不可用"})
                        return
                    GAME_DATA_CACHE["data"] = out.encode("utf-8")
                self._send(200, GAME_DATA_CACHE["data"])
            return
        if path == "/api/health":
            self._send_json(200, {"ok": True})
            return
        if path == "/api/combos":
            with LOCK:
                if "combos" not in GAME_DATA_CACHE:
                    code, out, err = run_script("combos_data.py", [], 60)
                    if code != 0:
                        sys.stderr.write(f"combos_data failed: {err.strip()[-500:]}\n")
                        self._send_json(500, {"error": "组合数据暂时不可用"})
                        return
                    GAME_DATA_CACHE["combos"] = out.encode("utf-8")
                self._send(200, GAME_DATA_CACHE["combos"])
            return
        if path == "/api/combos.xlsx":
            with LOCK:
                if "excel" not in GAME_DATA_CACHE:
                    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
                    os.close(fd)
                    try:
                        code, out, err = run_script("combos_export.py", [tmp], 45, EXPORT_SLOTS)
                        if code == -2:
                            self._send_json(429, {"error": "导出任务繁忙，请稍后重试"}, headers={"Retry-After": "5"})
                            return
                        if code != 0:
                            sys.stderr.write(f"combos export failed: {err.strip()[-500:]}\n")
                            self._send_json(500, {"error": "Excel 导出失败"})
                            return
                        GAME_DATA_CACHE["excel"] = Path(tmp).read_bytes()
                    finally:
                        try:
                            os.unlink(tmp)
                        except OSError:
                            pass
                self._send(
                    200,
                    GAME_DATA_CACHE["excel"],
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=minglun-combos.xlsx"},
                )
            return
        if path == "/api/history":
            owner = owner_hash(self.headers.get("X-Minglun-Owner") or "")
            if not owner:
                self._send_json(401, {"error": "缺少历史方案访问凭证"})
                return
            q = parse_qs(urlparse(self.path).query)
            nick = (q.get("nick") or [""])[0].strip()
            id_raw = (q.get("id") or [""])[0].strip()
            with HISTORY_LOCK:
                records = [r for r in load_history() if r.get("owner_hash") == owner]
            if id_raw:
                rec = next((r for r in records if str(r.get("id")) == id_raw), None)
                if not rec:
                    self._send_json(404, {"error": "记录不存在"})
                    return
                self._send_json(
                    200,
                    {"record": {k: v for k, v in rec.items() if k != "owner_hash"}},
                    headers={"Vary": "X-Minglun-Owner"},
                )
                return
            items = [summarize(r) for r in sorted(records, key=lambda r: r.get("ts", 0), reverse=True)]
            if nick:
                items = [s for s in items if s["nick"] == nick]
            self._send_json(200, {"records": items}, headers={"Vary": "X-Minglun-Owner"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        limits = {
            "/api/plan": MAX_PLAN_BODY_BYTES,
            "/api/recognize": MAX_RECOGNIZE_BODY_BYTES,
            "/api/history": MAX_HISTORY_RECORD_BYTES,
        }
        if path not in limits:
            self.close_connection = True
            self._send_json(404, {"error": "not found"})
            return
        if not self._origin_allowed():
            self.close_connection = True
            self._send_json(403, {"error": "不允许跨站请求"})
            return
        if not self._check_rate_limit("POST", path):
            self.close_connection = True
            return
        try:
            request = self._read_json(limits[path])
        except RequestError as exc:
            self._reject_request(exc)
            return

        if path == "/api/plan":
            tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            try:
                tmp.write(json.dumps(request, ensure_ascii=False))
                tmp.close()
                code, out, err = run_script("engine.py", [tmp.name], 90, PLAN_SLOTS)
                if code == -2:
                    self._send_json(429, {"error": err}, headers={"Retry-After": "5"})
                    return
                if code != 0:
                    sys.stderr.write(f"plan failed: {err.strip()[-800:]}\n")
                    self._send_json(500, {"error": "求解失败，请稍后重试"})
                    return
                payload = json.loads(out)
                self._send_json(400 if payload.get("status") == "error" else 200, payload)
            except (json.JSONDecodeError, OSError) as exc:
                self._send_internal_error("plan response failed", exc)
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            return

        if path == "/api/recognize":
            tmp_paths: list[str] = []
            try:
                images = request.get("images") if isinstance(request, dict) else None
                if not isinstance(images, list) or not images:
                    self._send_json(400, {"error": "没有收到图片"})
                    return
                if len(images) > MAX_IMAGES:
                    raise RequestError(400, f"一次最多识别 {MAX_IMAGES} 张图片")
                total_decoded = 0
                for i, data_url in enumerate(images):
                    raw, ext = decode_image_data_url(data_url)
                    total_decoded += len(raw)
                    if total_decoded > MAX_IMAGE_BYTES * 4:
                        raise RequestError(413, "图片总大小过大")
                    fd, p = tempfile.mkstemp(suffix=f"-{i}.{ext}")
                    with os.fdopen(fd, "wb") as f:
                        f.write(raw)
                    tmp_paths.append(p)
                code, out, err = run_script("recognize_api.py", tmp_paths, 90, RECOGNIZE_SLOTS)
                if code == -2:
                    self._send_json(429, {"error": err}, headers={"Retry-After": "5"})
                    return
                if code != 0:
                    sys.stderr.write(f"recognize failed: {err.strip()[-800:]}\n")
                    self._send_json(500, {"error": "图片识别失败，请稍后重试"})
                    return
                self._send(200, out.encode("utf-8"))
            except RequestError as exc:
                self._reject_request(exc)
            except (json.JSONDecodeError, OSError) as exc:
                self._send_internal_error("recognize response failed", exc)
            finally:
                for p in tmp_paths:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
            return

        if path == "/api/history":
            try:
                owner = owner_hash(self.headers.get("X-Minglun-Owner") or "")
                if not owner:
                    self._send_json(401, {"error": "缺少历史方案访问凭证"})
                    return
                nick, plan_payload, result = validate_history_submission(request)
                with HISTORY_LOCK:
                    records = load_history()
                    nid = max([r.get("id", 0) for r in records] or [0]) + 1
                    rec = {"id": nid, "owner_hash": owner, "nick": nick, "ts": int(time.time()),
                           "payload": plan_payload, "result": result}
                    records = append_history(records, rec)
                    save_history(records)
                self._send_json(200, {"id": nid, "summary": summarize(rec)}, headers={"Vary": "X-Minglun-Owner"})
            except RequestError as exc:
                self._reject_request(exc)
            except (OSError, ValueError, TypeError) as exc:
                self._send_internal_error("history save failed", exc)
            return

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if path == "/api/history":
            if not self._origin_allowed():
                self.close_connection = True
                self._send_json(403, {"error": "不允许跨站请求"})
                return
            if not self._check_rate_limit("DELETE", path):
                self.close_connection = True
                return
            try:
                owner = owner_hash(self.headers.get("X-Minglun-Owner") or "")
                if not owner:
                    self._send_json(401, {"error": "缺少历史方案访问凭证"})
                    return
                payload = self._read_json(1024)
                if not isinstance(payload, dict) or not isinstance(payload.get("id"), int):
                    raise RequestError(400, "记录编号无效")
                rid = payload.get("id")
                with HISTORY_LOCK:
                    records = load_history()
                    rec = next((r for r in records if r.get("id") == rid), None)
                    if not rec or rec.get("owner_hash") != owner:
                        self._send_json(404, {"error": "记录不存在"})
                        return
                    records = [r for r in records if r.get("id") != rid]
                    save_history(records)
                self._send_json(200, {"ok": True}, headers={"Vary": "X-Minglun-Owner"})
            except RequestError as exc:
                self._reject_request(exc)
            except (OSError, ValueError, TypeError) as exc:
                self._send_internal_error("history delete failed", exc)
            return
        self._send_json(404, {"error": "not found"})


class HardenedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32


def main():
    port = int(os.environ.get("MINGLUN_PORT", "8321"))
    server = HardenedHTTPServer(("127.0.0.1", port), Handler)
    print(f"minglun api server on 127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
