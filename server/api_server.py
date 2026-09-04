#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙族命轮助手 · 生产 API 服务器（纯标准库）。

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

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable
GAME_DATA_CACHE: dict[str, str] = {}
LOCK = threading.Lock()
HISTORY_FILE = Path(os.environ.get("MINGLUN_HISTORY_FILE", str(HERE / "history.json")))
HISTORY_LOCK = threading.Lock()
MAX_HISTORY = 100


def load_history() -> list[dict]:
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(records: list[dict]) -> None:
    tmp = HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    tmp.replace(HISTORY_FILE)


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


def run_script(script: str, args: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            [PYTHON, str(HERE / script), *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ},
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "计算超时"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 简化日志
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path.split("?")[0] == "/api/game-data":
            with LOCK:
                if "data" not in GAME_DATA_CACHE:
                    code, out, err = run_script("game_data.py", [], 60)
                    if code != 0:
                        self._send_json(500, {"error": err.strip()[-500:] or "game_data 失败"})
                        return
                    GAME_DATA_CACHE["data"] = out
                self._send(200, GAME_DATA_CACHE["data"].encode("utf-8"))
            return
        if self.path.split("?")[0] == "/api/health":
            self._send_json(200, {"ok": True})
            return
        if self.path.split("?")[0] == "/api/combos":
            with LOCK:
                if "combos" not in GAME_DATA_CACHE:
                    code, out, err = run_script("combos_data.py", [], 60)
                    if code != 0:
                        self._send_json(500, {"error": err.strip()[-500:] or "combos_data 失败"})
                        return
                    GAME_DATA_CACHE["combos"] = out
                self._send(200, GAME_DATA_CACHE["combos"].encode("utf-8"))
            return
        if self.path.split("?")[0] == "/api/combos.xlsx":
            fd, tmp = tempfile.mkstemp(suffix=".xlsx")
            os.close(fd)
            try:
                code, out, err = run_script("combos_export.py", [tmp], 60)
                if code != 0:
                    self._send_json(500, {"error": err.strip()[-500:] or "Excel 导出失败"})
                    return
                data = Path(tmp).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", "attachment; filename=minglun-combos.xlsx")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return
        if self.path.split("?")[0] == "/api/history":
            q = parse_qs(urlparse(self.path).query)
            nick = (q.get("nick") or [""])[0].strip()
            id_raw = (q.get("id") or [""])[0].strip()
            with HISTORY_LOCK:
                records = load_history()
            if id_raw:
                rec = next((r for r in records if str(r.get("id")) == id_raw), None)
                if not rec:
                    self._send_json(404, {"error": "记录不存在"})
                    return
                self._send_json(200, {"record": rec})
                return
            items = [summarize(r) for r in sorted(records, key=lambda r: r.get("ts", 0), reverse=True)]
            if nick:
                items = [s for s in items if s["nick"] == nick]
            self._send_json(200, {"records": items})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 40 * 1024 * 1024)
            body = self.rfile.read(length)
        except Exception:
            self._send_json(400, {"error": "读取请求体失败"})
            return

        if path == "/api/plan":
            tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            try:
                tmp.write(body.decode("utf-8"))
                tmp.close()
                code, out, err = run_script("engine.py", [tmp.name], 180)
                if code != 0:
                    self._send_json(500, {"error": err.strip()[-800:] or "求解失败"})
                    return
                payload = json.loads(out)
                self._send_json(400 if payload.get("status") == "error" else 200, payload)
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            finally:
                os.unlink(tmp.name)
            return

        if path == "/api/recognize":
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
                images = payload.get("images") or []
                if not images:
                    self._send_json(400, {"error": "没有收到图片"})
                    return
                import base64
                tmp_paths = []
                for i, data_url in enumerate(images[:6]):
                    if not isinstance(data_url, str) or "," not in data_url:
                        continue
                    header, b64 = data_url.split(",", 1)
                    ext = "png"
                    if "jpeg" in header or "jpg" in header:
                        ext = "jpg"
                    elif "webp" in header:
                        ext = "webp"
                    fd, p = tempfile.mkstemp(suffix=f"-{i}.{ext}")
                    with os.fdopen(fd, "wb") as f:
                        f.write(base64.b64decode(b64))
                    tmp_paths.append(p)
                if not tmp_paths:
                    self._send_json(400, {"error": "图片格式不支持（仅 PNG/JPG/WebP）"})
                    return
                try:
                    code, out, err = run_script("recognize_api.py", tmp_paths, 300)
                    if code != 0:
                        self._send_json(500, {"error": err.strip()[-800:] or "识别失败"})
                        return
                    self._send(200, out.encode("utf-8"))
                finally:
                    for p in tmp_paths:
                        try:
                            os.unlink(p)
                        except OSError:
                            pass
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/api/history":
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
                nick = str(payload.get("nick") or "").strip()[:24]
                if not nick:
                    self._send_json(400, {"error": "缺少昵称"})
                    return
                if not isinstance(payload.get("payload"), dict) or not isinstance(payload.get("result"), dict):
                    self._send_json(400, {"error": "记录内容不完整"})
                    return
                with HISTORY_LOCK:
                    records = load_history()
                    nid = max([r.get("id", 0) for r in records] or [0]) + 1
                    rec = {"id": nid, "nick": nick, "ts": int(time.time()),
                           "payload": payload["payload"], "result": payload["result"]}
                    records.append(rec)
                    records = records[-MAX_HISTORY:]
                    save_history(records)
                self._send_json(200, {"id": nid, "summary": summarize(rec)})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        self._send_json(404, {"error": "not found"})

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if path == "/api/history":
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 1024 * 1024)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                rid = payload.get("id")
                nick = str(payload.get("nick") or "").strip()
                with HISTORY_LOCK:
                    records = load_history()
                    rec = next((r for r in records if r.get("id") == rid), None)
                    if not rec or rec.get("nick") != nick:
                        self._send_json(404, {"error": "记录不存在或昵称不匹配"})
                        return
                    records = [r for r in records if r.get("id") != rid]
                    save_history(records)
                self._send_json(200, {"ok": True})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return
        self._send_json(404, {"error": "not found"})


def main():
    port = int(os.environ.get("MINGLUN_PORT", "8321"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"minglun api server on 127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
