import { useEffect, useMemo, useState } from 'react'
import type { GameData, PlanResult } from '@/types'
import { fetchGameData, runPlan, saveHistory, type HistoryRecord, type PlanPayload } from '@/lib/api'
import InventorySection, { type InventoryState } from '@/sections/InventorySection'
import ParamsSection, { buildChain, type ParamsState } from '@/sections/ParamsSection'
import ResultSection from '@/sections/ResultSection'
import HistoryDialog from '@/sections/HistoryPanel'
import DataDetailDialog from '@/sections/DataDetailDialog'

const NICK_KEY = 'minglun-nick'

export default function Home() {
  const [data, setData] = useState<GameData | null>(null)
  const [loadError, setLoadError] = useState('')
  const [inv, setInv] = useState<InventoryState>({
    wheel: {},
    role: {},
    roleInfinite: {},
    srRInfinite: true,
  })
  const [params, setParams] = useState<ParamsState>({
    nick: localStorage.getItem(NICK_KEY) || '',
    selectable: '',
    elements: ['精神'],
    mainElement: '精神',
    objectivePreset: 'main',
    customChain: '',
  })
  const [result, setResult] = useState<PlanResult | null>(null)
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState('')
  const [saveNote, setSaveNote] = useState('')
  const [showHistory, setShowHistory] = useState(false)
  const [showDataInfo, setShowDataInfo] = useState(false)

  useEffect(() => {
    fetchGameData().then(setData).catch((e) => setLoadError(e instanceof Error ? e.message : String(e)))
  }, [])

  useEffect(() => {
    localStorage.setItem(NICK_KEY, params.nick)
  }, [params.nick])

  const wheelCount = useMemo(() => Object.values(inv.wheel).filter((v) => v !== '').length, [inv.wheel])

  const run = async () => {
    if (!data) return
    const nick = params.nick.trim()
    if (!nick) {
      setRunError('请先填写昵称（用于保存历史方案）')
      return
    }
    setRunning(true)
    setRunError('')
    setSaveNote('')
    try {
      const toNum = (r: Record<string, string>) =>
        Object.fromEntries(Object.entries(r).filter(([, v]) => v !== '').map(([k, v]) => [k, Math.max(0, parseInt(v, 10) || 0)]))
      // UR 暂不支持：完全不进入 payload
      const urNames = new Set(data.characters.filter((c) => c.rarity === 'UR').map((c) => c.name))
      // 逐角色∞开关：缺省 常驻SSR=无限，限定SSR=0；显式覆盖优先
      const roleInf: string[] = []
      const roleFinite: Record<string, number> = {}
      for (const c of data.characters) {
        if (c.rarity !== 'SSR') continue
        const pool = c.pool.startsWith('限定') ? '限定SSR' : '常驻SSR'
        const inf = inv.roleInfinite[c.name] ?? pool === '常驻SSR'
        if (inf) {
          roleInf.push(c.name)
        } else {
          const raw = inv.role[c.name]
          if (raw !== undefined && raw !== '') roleFinite[c.name] = Math.max(0, parseInt(raw, 10) || 0)
        }
      }
      const wheel = toNum(inv.wheel)
      for (const n of urNames) delete wheel[n]
      const payload: PlanPayload = {
        inventory: wheel,
        wheel_infinite_rarities: inv.srRInfinite ? ['SR', 'R'] : [],
        role_fragments: roleFinite,
        role_fragments_infinite: roleInf,
        selectable: Math.max(0, parseInt(params.selectable, 10) || 0),
        elements: params.elements,
        main_element: params.mainElement,
        objective: buildChain(params),
      }
      const r = await runPlan(payload)
      setResult(r)
      try {
        await saveHistory(nick, payload, r)
        setSaveNote(`已按昵称「${nick}」存入历史记录`)
      } catch (e) {
        setSaveNote(`方案已生成，但历史存档失败：${e instanceof Error ? e.message : String(e)}`)
      }
      setTimeout(() => document.getElementById('result-anchor')?.scrollIntoView({ behavior: 'smooth' }), 50)
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  const viewRecord = (rec: HistoryRecord) => {
    setResult(rec.result)
    setSaveNote(`正在查看 ${rec.nick} 的历史方案（未改动当前库存）`)
    setTimeout(() => document.getElementById('result-anchor')?.scrollIntoView({ behavior: 'smooth' }), 50)
  }

  const loadRecord = (rec: HistoryRecord) => {
    if (!data) return
    const p = rec.payload
    const wheel: Record<string, string> = {}
    for (const [k, v] of Object.entries(p.inventory || {})) wheel[k] = String(v)
    const role: Record<string, string> = {}
    for (const [k, v] of Object.entries(p.role_fragments || {})) role[k] = String(v)
    const infSet = new Set(p.role_fragments_infinite || [])
    const roleInfinite: Record<string, boolean> = {}
    for (const c of data.characters) {
      if (c.rarity === 'SSR') roleInfinite[c.name] = infSet.has(c.name)
    }
    const infRar = p.wheel_infinite_rarities || []
    setInv({ wheel, role, roleInfinite, srRInfinite: infRar.includes('SR') && infRar.includes('R') })
    const main = p.main_element || '精神'
    const chain = p.objective || []
    const mainChain = [`${main}元素`, `${main}元素%`, '消耗', '攻击', '攻击%', '命轮值']
    const consumeChain = ['消耗', `${main}元素`, `${main}元素%`, '攻击', '攻击%', '命轮值']
    const eq = (a: string[], b: string[]) => a.join('|') === b.join('|')
    setParams({
      nick: rec.nick,
      selectable: String(p.selectable ?? ''),
      elements: p.elements?.length ? p.elements : ['精神'],
      mainElement: main,
      objectivePreset: eq(chain, mainChain) ? 'main' : eq(chain, consumeChain) ? 'consume' : 'custom',
      customChain: chain.join(','),
    })
    setResult(null)
    setRunError('')
    setSaveNote(`已载入 ${rec.nick} 的库存与目标，核对后可直接重新生成`)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <div className="min-h-screen bg-[#0b0f1a] text-slate-100 [background-image:radial-gradient(70rem_32rem_at_50%_-8%,rgba(245,158,11,0.07),transparent)]">
      <div className="mx-auto max-w-3xl px-3 pb-16 pt-6 sm:px-6 lg:max-w-6xl">
        <header className="mb-5">
          <h1 className="bg-gradient-to-r from-amber-300 via-amber-400 to-orange-400 bg-clip-text text-2xl font-black text-transparent">
            龙族：卡塞尔之门 · 命轮助手
          </h1>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            录入命轮碎片 / 自选碎片 / 限定角色碎片，一键生成 MILP 认证最优命轮方案。支持文本与截图自动填充。
          </p>
          {data && (
            <button
              type="button"
              onClick={() => setShowDataInfo(true)}
              className="mt-1.5 inline-flex items-center gap-1 rounded-full border border-slate-700/70 bg-slate-900/60 px-2.5 py-1 text-[11px] text-slate-500 transition-colors hover:border-amber-400/40 hover:text-slate-300"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-3 w-3">
                <ellipse cx="12" cy="5" rx="9" ry="3" />
                <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
                <path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3" />
              </svg>
              数据明细 · {Object.values(data.combo_counts).reduce((a, b) => a + b, 0)} 组合
            </button>
          )}
          <p className="mt-2 text-[11px] leading-5 text-slate-500">
            数据支持：君度
            <br />
            技术支持：Roy、梧桐落
          </p>
        </header>

        {loadError && (
          <div className="mb-4 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">
            游戏数据加载失败：{loadError}（请确认通过 npm run dev 访问，而不是直接打开静态文件）
          </div>
        )}

        {data && (
          <div className="space-y-4 lg:grid lg:grid-cols-12 lg:items-start lg:gap-5 lg:space-y-0">
            <div className="lg:col-span-5">
              <InventorySection
                data={data}
                value={inv}
                onChange={setInv}
                headerExtra={
                  <button
                    type="button"
                    onClick={() => setShowHistory(!showHistory)}
                    title="历史方案"
                    aria-label="历史方案"
                    className={`flex h-7 w-7 items-center justify-center rounded-lg border transition-colors ${
                      showHistory
                        ? 'border-amber-400/60 bg-amber-500/15 text-amber-300'
                        : 'border-slate-600 bg-slate-800/60 text-slate-400 hover:border-slate-500 hover:text-slate-300'
                    }`}
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                      <circle cx="12" cy="12" r="10" />
                      <polyline points="12 6 12 12 16 14" />
                    </svg>
                  </button>
                }
              />
            </div>
            <div className="space-y-4 lg:col-span-7">
              <ParamsSection data={data} value={params} onChange={setParams} onRun={run} running={running} />
              {runError && (
                <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">{runError}</div>
              )}
              {saveNote && (
                <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-3 text-xs text-slate-400">{saveNote}</div>
              )}
              {!result && !running && wheelCount === 0 && (
                <p className="text-center text-xs text-slate-600">提示：点「① 库存」旁的上传图标，可一键填入示例体验完整流程</p>
              )}
              <div id="result-anchor" />
              {result && <ResultSection result={result} />}
            </div>
          </div>
        )}

        {data && (
          <HistoryDialog
            open={showHistory}
            onOpenChange={setShowHistory}
            currentNick={params.nick.trim()}
            onView={viewRecord}
            onLoad={loadRecord}
          />
        )}

        {data && (
          <DataDetailDialog open={showDataInfo} onOpenChange={setShowDataInfo} />
        )}

        <footer className="mt-8 text-center text-[11px] leading-5 text-slate-600">
          数据口径见「计算口径与假设」· 所有组合默认从白0星开始 · 品阶 白→绿→蓝→紫→橙→彩
        </footer>
      </div>
    </div>
  )
}
