import path from "path"
import fs from "fs"
import os from "os"
import { spawn } from "child_process"
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

interface HistoryRec {
  id: number
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

function runPython(script: string, args: string[], timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    let settled = false
    const tryNext = (idx: number) => {
      if (idx >= PYTHON_CANDIDATES.length) {
        reject(new Error("找不到可用的 Python（需要 managed Python 环境）"))
        return
      }
      const py = PYTHON_CANDIDATES[idx]
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
        clearTimeout(timer)
        if (!settled) { settled = true; tryNext(idx + 1) } // ENOENT → 试下一个候选
      })
      child.on("close", (code) => {
        clearTimeout(timer)
        if (settled) return
        settled = true
        if (code === 0) resolve(out)
        else reject(new Error(err.trim().slice(-800) || `Python 退出码 ${code}`))
      })
    }
    tryNext(0)
  })
}

function readBody(req: NodeJS.ReadableStream): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = ""
    req.on("data", (c) => { data += c })
    req.on("end", () => resolve(data))
    req.on("error", reject)
  })
}

function sendJson(res: import("http").ServerResponse, status: number, payload: unknown) {
  res.statusCode = status
  res.setHeader("Content-Type", "application/json; charset=utf-8")
  res.end(JSON.stringify(payload))
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
          if (url === "/api/plan" && req.method === "POST") {
            const body = await readBody(req)
            const tmp = path.join(os.tmpdir(), `minglun-plan-${Date.now()}-${Math.random().toString(36).slice(2)}.json`)
            fs.writeFileSync(tmp, body, "utf-8")
            try {
              const out = await runPython("engine.py", [tmp], 180_000)
              const parsed = JSON.parse(out)
              sendJson(res, parsed.status === "error" ? 400 : 200, parsed)
            } finally {
              fs.rmSync(tmp, { force: true })
            }
            return
          }
          if (url === "/api/recognize" && req.method === "POST") {
            const body = JSON.parse(await readBody(req) || "{}")
            const images: string[] = Array.isArray(body.images) ? body.images.slice(0, 6) : []
            if (!images.length) {
              sendJson(res, 400, { error: "没有收到图片" })
              return
            }
            const tmpPaths: string[] = []
            try {
              for (let i = 0; i < images.length; i++) {
                const m = /^data:image\/(png|jpeg|jpg|webp);base64,(.+)$/.exec(images[i])
                if (!m) continue
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
            const q = new URL(req.url || "", "http://localhost").searchParams
            const idRaw = (q.get("id") || "").trim()
            const nick = (q.get("nick") || "").trim()
            const records = loadHistory()
            if (idRaw) {
              const rec = records.find((r) => String(r.id) === idRaw)
              if (!rec) { sendJson(res, 404, { error: "记录不存在" }); return }
              sendJson(res, 200, { record: rec })
              return
            }
            let items = records.slice().sort((a, b) => b.ts - a.ts).map(summarize)
            if (nick) items = items.filter((s) => s.nick === nick)
            sendJson(res, 200, { records: items })
            return
          }
          if (url === "/api/history" && req.method === "POST") {
            const body = JSON.parse(await readBody(req) || "{}")
            const nick = String(body.nick || "").trim().slice(0, 24)
            if (!nick) { sendJson(res, 400, { error: "缺少昵称" }); return }
            if (!body.payload || typeof body.payload !== "object" || !body.result || typeof body.result !== "object") {
              sendJson(res, 400, { error: "记录内容不完整" })
              return
            }
            const records = loadHistory()
            const nid = records.reduce((m, r) => Math.max(m, r.id || 0), 0) + 1
            const rec: HistoryRec = { id: nid, nick, ts: Math.floor(Date.now() / 1000), payload: body.payload, result: body.result }
            records.push(rec)
            saveHistory(records.slice(-MAX_HISTORY))
            sendJson(res, 200, { id: nid, summary: summarize(rec) })
            return
          }
          if (url === "/api/history" && req.method === "DELETE") {
            const body = JSON.parse(await readBody(req) || "{}")
            const nick = String(body.nick || "").trim()
            const records = loadHistory()
            const rec = records.find((r) => r.id === body.id)
            if (!rec || rec.nick !== nick) { sendJson(res, 404, { error: "记录不存在或昵称不匹配" }); return }
            saveHistory(records.filter((r) => r.id !== body.id))
            sendJson(res, 200, { ok: true })
            return
          }
          next()
        } catch (e) {
          sendJson(res, 500, { error: e instanceof Error ? e.message : String(e) })
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
    port: 3000,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
