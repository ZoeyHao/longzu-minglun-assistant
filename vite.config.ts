import path from "path"
import fs from "fs"
import os from "os"
import { spawn } from "child_process"
import { createHash } from "crypto"
import react from "@vitejs/plugin-react"
import { defineConfig, type Plugin } from "vite"
import { inspectAttr } from 'kimi-plugin-inspect-react'

// Managed Python（含 highspy / openpyxl / pillow / numpy）
const PYTHON_CANDIDATES = [
  process.env.MINGLUN_PYTHON,
  // Kimi Work 桌面版自带 Python（含 highspy/openpyxl/pillow/numpy）；其他环境用 MINGLUN_PYTHON 指定
  path.join(os.homedir(), "Library/Application Support/kimi-desktop/daimon-share/daimon/runtime/python/.venv/bin/python3"),
  "python3",
].filter(Boolean) as string[]

const SERVER_DIR = path.resolve(__dirname, "server")
const HISTORY_FILE = path.join(SERVER_DIR, "history.json")
const MAX_HISTORY = 100
const MAX_HISTORY_TOTAL = 5000
const MAX_HISTORY_RECORD_BYTES = 256 * 1024
const MAX_HISTORY_OWNER_BYTES = 4 * 1024 * 1024
const MAX_HISTORY_FILE_BYTES = 16 * 1024 * 1024
const MAX_PLAN_BODY_BYTES = 128 * 1024
const MAX_RECOGNIZE_BODY_BYTES = 32 * 1024 * 1024
const MAX_IMAGE_BASE64_CHARS = Math.ceil((6 * 1024 * 1024) / 3) * 4 + 8

interface HistoryRec {
  id: number
  owner_hash: string
  nick: string
  ts: number
  payload: Record<string, unknown>
  result: Record<string, unknown>
}

function loadHistory(): HistoryRec[] {
  try {
    return JSON.parse(fs.readFileSync(HISTORY_FILE, "utf-8"))
  } catch {
    return []
  }
}

function saveHistory(records: HistoryRec[]) {
  const tmp = HISTORY_FILE + ".tmp"
  fs.writeFileSync(tmp, JSON.stringify(records), "utf-8")
  fs.renameSync(tmp, HISTORY_FILE)
}

function appendHistory(records: HistoryRec[], rec: HistoryRec) {
  const sizeOf = (value: unknown) => Buffer.byteLength(JSON.stringify(value), "utf-8")
  const mine: HistoryRec[] = []
  let mineBytes = 2
  for (const item of [...records.filter((entry) => entry.owner_hash === rec.owner_hash), rec].reverse()) {
    const itemBytes = sizeOf(item) + 1
    if (mine.length >= MAX_HISTORY || mineBytes + itemBytes > MAX_HISTORY_OWNER_BYTES) continue
    mine.unshift(item)
    mineBytes += itemBytes
  }
  const others = records.filter((item) => item.owner_hash !== rec.owner_hash)
  const candidates = [...others, ...mine].sort((a, b) => a.ts - b.ts).slice(-MAX_HISTORY_TOTAL)
  const kept: HistoryRec[] = []
  let totalBytes = 2
  for (const item of candidates.reverse()) {
    const itemBytes = sizeOf(item) + 1
    if (totalBytes + itemBytes > MAX_HISTORY_FILE_BYTES) continue
    kept.unshift(item)
    totalBytes += itemBytes
  }
  return kept
}

function summarize(rec: HistoryRec) {
  const r = (rec.result || {}) as { main_element?: string; elements?: string[]; stars_lit?: number; fragments_consumed?: number; totals?: Record<string, number> }
  const main = r.main_element || ""
  const totals = r.totals || {}
  return {
    id: rec.id,
    nick: rec.nick,
    ts: rec.ts,
    main_element: main,
    elements: r.elements || [],
    stars_lit: r.stars_lit || 0,
    fragments_consumed: r.fragments_consumed || 0,
    main_flat: totals[`${main}元素`] || 0,
    main_pct: totals[`${main}元素%`] || 0,
    atk: totals["攻击"] || 0,
  }
}

const STATS_FILE = path.join(SERVER_DIR, "stats.json")

function loadStats(): Record<string, number> {
  try {
    const data = JSON.parse(fs.readFileSync(STATS_FILE, "utf-8"))
    return data && typeof data === "object" ? data : {}
  } catch {
    return {}
  }
}

function bumpStat(key: string) {
  try {
    const stats = loadStats()
    stats[key] = (stats[key] || 0) + 1
    fs.writeFileSync(STATS_FILE, JSON.stringify(stats), "utf-8")
  } catch (e) {
    console.error("stats bump failed", e instanceof Error ? e.name : "UnknownError")
  }
}

function historyOwnerHash(req: import("http").IncomingMessage): string | null {
  const raw = req.headers["x-minglun-owner"]
  const token = String(Array.isArray(raw) ? raw[0] : raw || "").trim()
  if (token.length < 24 || token.length > 160) return null
  return createHash("sha256").update(token).digest("hex")
}

function runPython(script: string, args: string[], timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    let settled = false
    const tryNext = (idx: number) => {
      if (idx >= PYTHON_CANDIDATES.length) {
        reject(new Error("找不到可用的 Python（需要 managed Python 环境）"))
        return
      }
      const py = PYTHON_CANDIDATES[idx]
      let attemptFailed = false
      const child = spawn(py, [path.join(SERVER_DIR, script), ...args], {
        maxBuffer: 64 * 1024 * 1024,
      } as never)
      let out = ""
      let err = ""
      const timer = setTimeout(() => {
        child.kill("SIGKILL")
        if (!settled) { settled = true; reject(new Error("计算超时")) }
      }, timeoutMs)
      child.stdout.on("data", (d) => { out += d })
      child.stderr.on("data", (d) => { err += d })
      child.on("error", () => {
        attemptFailed = true
        clearTimeout(timer)
        if (!settled) tryNext(idx + 1) // ENOENT → 试下一个候选
      })
      child.on("close", (code) => {
        clearTimeout(timer)
        if (settled || attemptFailed) return
        settled = true
        if (code === 0) resolve(out)
        else reject(new Error(err.trim().slice(-800) || `Python 退出码 ${code}`))
      })
    }
    tryNext(0)
  })
}

class HttpError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function readBody(req: import("http").IncomingMessage, maxBytes: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const contentType = String(req.headers["content-type"] || "").split(";", 1)[0].trim().toLowerCase()
    if (contentType !== "application/json") {
      reject(new HttpError(415, "请求必须使用 application/json"))
      return
    }
    const declared = Number(req.headers["content-length"])
    if (!Number.isSafeInteger(declared) || declared <= 0) {
      reject(new HttpError(411, "Content-Length 无效"))
      return
    }
    if (declared > maxBytes) {
      reject(new HttpError(413, "请求体过大"))
      return
    }
    let data = ""
    let received = 0
    req.on("data", (c: Buffer) => {
      received += c.length
      if (received > maxBytes) {
        req.pause()
        reject(new HttpError(413, "请求体过大"))
        return
      }
      data += c
    })
    req.on("end", () => resolve(data))
    req.on("error", reject)
  })
}

function sendJson(res: import("http").ServerResponse, status: number, payload: unknown) {
  res.statusCode = status
  res.setHeader("Content-Type", "application/json; charset=utf-8")
  res.setHeader("Cache-Control", "no-store")
  res.setHeader("X-Content-Type-Options", "nosniff")
  res.setHeader("X-Frame-Options", "DENY")
  res.setHeader("Referrer-Policy", "strict-origin-when-cross-origin")
  res.end(JSON.stringify(payload))
}

function isAllowedOrigin(req: import("http").IncomingMessage) {
  const origin = String(req.headers.origin || "").trim()
  if (!origin) return true
  try {
    return new URL(origin).host === req.headers.host
  } catch {
    return false
  }
}

function minglunApi(): Plugin {
  let gameDataCache: string | null = null
  let combosCache: string | null = null
  return {
    name: "minglun-api",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = (req.url || "").split("?")[0]
        try {
          if (url.startsWith("/api/")) {
            res.setHeader("Cache-Control", "no-store")
            res.setHeader("X-Content-Type-Options", "nosniff")
            res.setHeader("X-Frame-Options", "DENY")
            res.setHeader("Referrer-Policy", "strict-origin-when-cross-origin")
          }
          if ((req.method === "POST" || req.method === "DELETE") && !isAllowedOrigin(req)) {
            sendJson(res, 403, { error: "不允许跨站请求" })
            return
          }
          if (url === "/api/combos" && req.method === "GET") {
            if (!combosCache) combosCache = await runPython("combos_data.py", [], 60_000)
            res.statusCode = 200
            res.setHeader("Content-Type", "application/json; charset=utf-8")
            res.end(combosCache)
            return
          }
          if (url === "/api/combos.xlsx" && req.method === "GET") {
            const tmp = path.join(os.tmpdir(), `minglun-combos-${Date.now()}.xlsx`)
            try {
              await runPython("combos_export.py", [tmp], 60_000)
              const buf = fs.readFileSync(tmp)
              res.statusCode = 200
              res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
              res.setHeader("Content-Disposition", "attachment; filename=minglun-combos.xlsx")
              res.end(buf)
            } finally {
              fs.rmSync(tmp, { force: true })
            }
            return
          }
          if (url === "/api/game-data" && req.method === "GET") {
            if (!gameDataCache) gameDataCache = await runPython("game_data.py", [], 60_000)
            res.statusCode = 200
            res.setHeader("Content-Type", "application/json; charset=utf-8")
            res.end(gameDataCache)
            return
          }
          if (url === "/api/stats" && req.method === "GET") {
            sendJson(res, 200, { plans: loadStats().plans || 0 })
            return
          }
          if (url === "/api/plan" && req.method === "POST") {
            const body = await readBody(req, MAX_PLAN_BODY_BYTES)
            const tmp = path.join(os.tmpdir(), `minglun-plan-${Date.now()}-${Math.random().toString(36).slice(2)}.json`)
            fs.writeFileSync(tmp, body, "utf-8")
            try {
              const out = await runPython("engine.py", [tmp], 180_000)
              const parsed = JSON.parse(out)
              if (parsed.status !== "error") bumpStat("plans")
              sendJson(res, parsed.status === "error" ? 400 : 200, parsed)
            } finally {
              fs.rmSync(tmp, { force: true })
            }
            return
          }
          if (url === "/api/recognize" && req.method === "POST") {
            const body = JSON.parse(await readBody(req, MAX_RECOGNIZE_BODY_BYTES) || "{}")
            const images: string[] = Array.isArray(body.images) ? body.images : []
            if (!images.length) {
              sendJson(res, 400, { error: "没有收到图片" })
              return
            }
            if (images.length > 6) {
              sendJson(res, 400, { error: "一次最多识别 6 张图片" })
              return
            }
            const tmpPaths: string[] = []
            try {
              for (let i = 0; i < images.length; i++) {
                const m = /^data:image\/(png|jpeg|jpg|webp);base64,(.+)$/.exec(images[i])
                if (!m) continue
                if (m[2].length > MAX_IMAGE_BASE64_CHARS) throw new HttpError(413, "单张图片不能超过 6 MB")
                const p = path.join(os.tmpdir(), `minglun-shot-${Date.now()}-${i}.${m[1] === "jpeg" ? "jpg" : m[1]}`)
                fs.writeFileSync(p, Buffer.from(m[2], "base64"))
                tmpPaths.push(p)
              }
              if (!tmpPaths.length) {
                sendJson(res, 400, { error: "图片格式不支持（仅 PNG/JPG/WebP）" })
                return
              }
              const out = await runPython("recognize_api.py", tmpPaths, 300_000)
              sendJson(res, 200, JSON.parse(out))
            } finally {
              tmpPaths.forEach((p) => fs.rmSync(p, { force: true }))
            }
            return
          }
          if (url === "/api/history" && req.method === "GET") {
            const owner = historyOwnerHash(req)
            if (!owner) { sendJson(res, 401, { error: "缺少历史方案访问凭证" }); return }
            const q = new URL(req.url || "", "http://localhost").searchParams
            const idRaw = (q.get("id") || "").trim()
            const nick = (q.get("nick") || "").trim()
            const records = loadHistory().filter((r) => r.owner_hash === owner)
            if (idRaw) {
              const rec = records.find((r) => String(r.id) === idRaw)
              if (!rec) { sendJson(res, 404, { error: "记录不存在" }); return }
              const publicRecord = {
                id: rec.id,
                nick: rec.nick,
                ts: rec.ts,
                payload: rec.payload,
                result: rec.result,
              }
              sendJson(res, 200, { record: publicRecord })
              return
            }
            let items = records.slice().sort((a, b) => b.ts - a.ts).map(summarize)
            if (nick) items = items.filter((s) => s.nick === nick)
            sendJson(res, 200, { records: items })
            return
          }
          if (url === "/api/history" && req.method === "POST") {
            const owner = historyOwnerHash(req)
            if (!owner) { sendJson(res, 401, { error: "缺少历史方案访问凭证" }); return }
            const rawBody = await readBody(req, MAX_HISTORY_RECORD_BYTES)
            const body = JSON.parse(rawBody || "{}")
            const nick = String(body.nick || "").trim().slice(0, 24)
            if (!nick) { sendJson(res, 400, { error: "缺少昵称" }); return }
            if (!body.payload || typeof body.payload !== "object" || !body.result || typeof body.result !== "object") {
              sendJson(res, 400, { error: "记录内容不完整" })
              return
            }
            const records = loadHistory()
            const nid = records.reduce((m, r) => Math.max(m, r.id || 0), 0) + 1
            const rec: HistoryRec = { id: nid, owner_hash: owner, nick, ts: Math.floor(Date.now() / 1000), payload: body.payload, result: body.result }
            saveHistory(appendHistory(records, rec))
            sendJson(res, 200, { id: nid, summary: summarize(rec) })
            return
          }
          if (url === "/api/history" && req.method === "DELETE") {
            const owner = historyOwnerHash(req)
            if (!owner) { sendJson(res, 401, { error: "缺少历史方案访问凭证" }); return }
            const body = JSON.parse(await readBody(req, 1024) || "{}")
            const records = loadHistory()
            const rec = records.find((r) => r.id === body.id)
            if (!rec || rec.owner_hash !== owner) { sendJson(res, 404, { error: "记录不存在" }); return }
            saveHistory(records.filter((r) => r.id !== body.id))
            sendJson(res, 200, { ok: true })
            return
          }
          next()
        } catch (e) {
          if (e instanceof HttpError) {
            sendJson(res, e.status, { error: e.message })
          } else {
            console.error("minglun api error", e instanceof Error ? e.name : "UnknownError")
            sendJson(res, 500, { error: "服务器内部错误" })
          }
        }
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [inspectAttr(), react(), minglunApi()],
  server: {
    host: "127.0.0.1",
    port: 3000,
    strictPort: true,
    headers: {
      // Development-only: React Refresh injects an inline module preamble.
      // Production CSP is supplied by nginx and remains strict.
      "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' ws:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
