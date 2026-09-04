import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { CharInfo, GameData, ParseReport, RecognizeFileResult } from '@/types'
import { parseInventoryText } from '@/lib/parseText'
import { recognizeImages } from '@/lib/api'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { useIsMobile } from '@/hooks/use-mobile'

export interface InventoryState {
  wheel: Record<string, string>
  role: Record<string, string>
  /** 角色碎片∞开关的显式覆盖；缺省：常驻SSR=无限，限定SSR/UR=0 */
  roleInfinite: Record<string, boolean>
  srRInfinite: boolean
}

const POOL_GROUPS: { key: string; title: string; hint: string }[] = [
  { key: 'UR', title: 'UR', hint: '暂不支持，后续优化' },
  { key: '限定SSR', title: '限定 SSR', hint: '绘梨衣15/次·其余30/次 · 默认0，点∞设无限' },
  { key: '常驻SSR', title: '常驻 SSR', hint: '角色碎片默认无限，点∞可改为实报' },
  { key: 'SR', title: 'SR（紫卡）', hint: '命轮与角色碎片默认无限' },
  { key: 'R', title: 'R（蓝卡）', hint: '命轮与角色碎片默认无限' },
]

const EXAMPLE_TEXT = `恺撒56，陈墨瞳40，路明非107，苏恩曦65，酒德麻衣61，芬格尔49，诺玛50，路明泽124，昂热62，夏弥58，王将90，帕西50，楚子航72，零56，绘梨衣72，守夜人55，源稚生140，源稚女138，康斯坦丁42，上杉越87，诺顿29，芬里厄12，风间琉璃30，楚天骄26，耶梦加得19，EVA42，龙麻27，强欲72，贪婪47，饕餮34，傲慢34，巫女诺诺31，龙马小暮21，天演苏恩曦27，弑罪路明非22，双源15，影镰恺撒13，剑御苏茜7，隐狩矢吹樱4。碎片方面：风间琉璃90，耶梦加得30，弑罪路明非150，天演苏恩熙60，龙马小暮60，巫女诺诺90，EVA60，诺顿150，绘梨衣360，影镰恺撒0，剑御苏茜0，隐狩矢吹樱0，龙麻0，其余角色碎片无限供应，紫色角色和蓝色角色命轮及碎片都无限供应。`

function poolOf(c: CharInfo): string {
  if (c.rarity === 'SSR') return c.pool.startsWith('限定') ? '限定SSR' : '常驻SSR'
  return c.rarity
}

function NumInput(props: { value: string; onChange: (v: string) => void; disabled?: boolean; ariaLabel: string }) {
  if (props.disabled) {
    return <span aria-label={props.ariaLabel} className="flex h-10 w-16 items-center justify-center rounded-lg bg-emerald-500/15 text-xs font-semibold text-emerald-300">无限</span>
  }
  return (
    <input
      type="number" inputMode="numeric" min={0} placeholder="0" autoComplete="off"
      aria-label={props.ariaLabel}
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
      className="h-10 w-16 rounded-lg border border-slate-600 bg-slate-900/80 px-2 text-right text-sm text-slate-100 outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-400/20"
    />
  )
}

/** 角色碎片格：数值输入 + ∞ 无限开关 */
function RoleFragCell(props: { name: string; infinite: boolean; value: string; onToggle: (next: boolean) => void; onChange: (v: string) => void }) {
  return (
    <div className="flex items-center gap-1">
      <NumInput ariaLabel={`${props.name}角色碎片`} value={props.value} onChange={props.onChange} disabled={props.infinite} />
      <button
        type="button"
        onClick={() => props.onToggle(!props.infinite)}
        title={props.infinite ? '当前：无限（点击改为实报数量）' : '当前：按所填数量（点击设为无限）'}
        aria-label={`${props.name}角色碎片：${props.infinite ? '无限，点击改为填写数量' : '按填写数量，点击设为无限'}`}
        aria-pressed={props.infinite}
        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border text-sm font-bold transition-colors ${
          props.infinite
            ? 'border-emerald-500/60 bg-emerald-500/20 text-emerald-300'
            : 'border-slate-600 bg-slate-800/60 text-slate-500 hover:border-slate-500 hover:text-slate-300'
        }`}
      >
        ∞
      </button>
    </div>
  )
}

export default function InventorySection(props: {
  data: GameData
  value: InventoryState
  onChange: (v: InventoryState) => void
  headerExtra?: ReactNode
}) {
  const { data, value, onChange, headerExtra } = props
  const isMobile = useIsMobile()
  const [text, setText] = useState('')
  const [report, setReport] = useState<ParseReport | null>(null)
  const [shots, setShots] = useState<RecognizeFileResult[] | null>(null)
  const [recognizing, setRecognizing] = useState(false)
  const [shotError, setShotError] = useState('')
  const [showImport, setShowImport] = useState(false)
  const [query, setQuery] = useState('')
  const [openGroups, setOpenGroups] = useState<string[]>(() => isMobile ? [] : ['限定SSR', '常驻SSR'])
  const fileRef = useRef<HTMLInputElement>(null)

  const groups = useMemo(() => {
    const g: Record<string, CharInfo[]> = {}
    for (const c of data.characters) {
      ;(g[poolOf(c)] ||= []).push(c)
    }
    return g
  }, [data])

  const visibleGroups = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase('zh-CN')
    if (!keyword) return groups
    const aliasMatches = new Set(
      Object.entries(data.aliases)
        .filter(([alias]) => alias.toLocaleLowerCase('zh-CN').includes(keyword))
        .map(([, canonical]) => canonical),
    )
    return Object.fromEntries(
      Object.entries(groups).map(([key, chars]) => [
        key,
        chars.filter((c) => c.name.toLocaleLowerCase('zh-CN').includes(keyword)
          || c.aliases.some((alias) => alias.toLocaleLowerCase('zh-CN').includes(keyword))
          || aliasMatches.has(c.name)),
      ]),
    )
  }, [data.aliases, groups, query])

  useEffect(() => {
    if (!query.trim()) return
    setOpenGroups(POOL_GROUPS.filter((g) => (visibleGroups[g.key] ?? []).length > 0).map((g) => g.key))
  }, [query, visibleGroups])

  const wheelFilled = Object.values(value.wheel).filter((v) => v !== '').length
  const roleFilled = Object.values(value.role).filter((v) => v !== '').length

  const setWheel = (name: string, v: string) => onChange({ ...value, wheel: { ...value.wheel, [name]: v } })
  const setRole = (name: string, v: string) => onChange({ ...value, role: { ...value.role, [name]: v } })

  const isRoleInfinite = (c: CharInfo) => value.roleInfinite[c.name] ?? poolOf(c) === '常驻SSR'
  const setRoleInfinite = (name: string, inf: boolean) =>
    onChange({ ...value, roleInfinite: { ...value.roleInfinite, [name]: inf } })

  const applyText = () => {
    const r = parseInventoryText(text, data)
    const wheel = { ...value.wheel }
    for (const [k, v] of Object.entries(r.wheel)) wheel[k] = String(v)
    const role = { ...value.role }
    for (const [k, v] of Object.entries(r.role)) role[k] = String(v)
    const roleInfinite = { ...value.roleInfinite }
    // 文本中实报了数量的角色 → 关掉∞
    for (const k of Object.keys(r.role)) roleInfinite[k] = false
    // 「其余角色碎片无限供应」→ 未实报的 UR/SSR 全部开∞
    if (r.detectedUnlistedRoleInfinite) {
      for (const c of data.characters) {
        const p = poolOf(c)
        if ((p === 'UR' || p === '限定SSR' || p === '常驻SSR') && !(c.name in r.role)) roleInfinite[c.name] = true
      }
    }
    onChange({
      ...value,
      wheel,
      role,
      roleInfinite,
      srRInfinite: r.detectedSRInfinite || r.detectedRInfinite ? true : value.srRInfinite,
    })
    setReport(r)
  }

  const onPickImages = async (files: FileList | null) => {
    if (!files || !files.length) return
    setShotError('')
    setRecognizing(true)
    setShots(null)
    try {
      const images = await Promise.all(
        [...files].slice(0, 6).map(
          (f) =>
            new Promise<string>((resolve, reject) => {
              const reader = new FileReader()
              reader.onload = () => resolve(String(reader.result))
              reader.onerror = reject
              reader.readAsDataURL(f)
            }),
        ),
      )
      setShots(await recognizeImages(images))
    } catch (e) {
      setShotError(e instanceof Error ? e.message : String(e))
    } finally {
      setRecognizing(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const applyShots = () => {
    if (!shots) return
    const wheel = { ...value.wheel }
    for (const f of shots) {
      for (const cell of f.cells ?? []) {
        if (!cell.name_confident || cell.count === null) continue
        if (!(cell.name in wheel) || !cell.count_confident) wheel[cell.name] = String(cell.count)
      }
    }
    onChange({ ...value, wheel })
  }

  return (
    <section className="rounded-2xl border border-slate-700/60 bg-slate-900/60 p-4 shadow-xl">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-base font-bold text-amber-300">
          ① 库存
          <button
            type="button"
            onClick={() => setShowImport(!showImport)}
            title="导入（文本 / 截图）"
            aria-label="导入（文本 / 截图）"
            className={`flex h-9 items-center justify-center gap-1 rounded-lg border px-2 transition-colors ${
              showImport
                ? 'border-amber-400/60 bg-amber-500/15 text-amber-300'
                : 'border-slate-600 bg-slate-800/60 text-slate-400 hover:border-slate-500 hover:text-slate-300'
            }`}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <span className="hidden text-xs sm:inline">导入</span>
          </button>
          {headerExtra}
        </h2>
        <span className="text-xs text-slate-400">命轮 {wheelFilled} 名 · 角色碎片 {roleFilled} 名</span>
      </div>

      {/* 导入工具（图标唤起）：文本 + 截图，汇入下方同一张表 */}
      {showImport && (
        <div className="rounded-xl border border-slate-700 bg-slate-950/60">
          <div className="space-y-3 p-3">
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="粘贴口述库存，例如：恺撒56，陈墨瞳40，……碎片方面：风间琉璃90，……"
              className="min-h-24 border-slate-700 bg-slate-900/70 text-sm text-slate-100"
            />
            <div className="flex flex-wrap gap-2">
              <Button onClick={applyText} disabled={!text.trim()} className="bg-amber-500 font-semibold text-slate-950 hover:bg-amber-400">
                解析文本并填入下表
              </Button>
              <Button variant="outline" className="border-slate-600 text-slate-300" onClick={() => setText(EXAMPLE_TEXT)}>
                填入示例
              </Button>
            </div>
            {report && (
              <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-2.5 text-xs leading-6 text-slate-300">
                命轮碎片 <b className="text-amber-300">{report.wheelCount}</b> 名 · 角色碎片 <b className="text-amber-300">{report.roleCount}</b> 名
                {report.detectedUnlistedRoleInfinite && <Badge className="ml-2 bg-emerald-600/30 text-emerald-300">识别到「其余角色碎片无限」→ 已开∞</Badge>}
                {(report.detectedSRInfinite || report.detectedRInfinite) && <Badge className="ml-2 bg-emerald-600/30 text-emerald-300">识别到「紫/蓝卡无限」</Badge>}
                {report.unknown.length > 0 && <div className="text-rose-300">未识别：{report.unknown.join('、')}（请在下方手动填写）</div>}
              </div>
            )}
            <div className="border-t border-slate-800 pt-3">
              <p className="mb-2 text-xs leading-5 text-slate-400">或上传命盘「物品详情」弹窗截图（可多选），离线识别后填入下表：</p>
              <input ref={fileRef} type="file" accept="image/*" multiple className="hidden" onChange={(e) => onPickImages(e.target.files)} />
              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => fileRef.current?.click()} disabled={recognizing} className="bg-amber-500 font-semibold text-slate-950 hover:bg-amber-400">
                  {recognizing ? '识别中…（约几十秒）' : '选择截图并识别'}
                </Button>
                {shots && (
                  <Button variant="outline" className="border-slate-600 text-slate-300" onClick={applyShots}>
                    填入下表
                  </Button>
                )}
              </div>
              {shotError && <div className="mt-2 text-xs text-rose-300">{shotError}</div>}
              {shots && (
                <div className="mt-2 space-y-2">
                  {shots.map((f, i) => (
                    <div key={i} className="rounded-xl border border-slate-700 bg-slate-900/60 p-2.5">
                      <div className="mb-1 text-xs text-slate-400">{f.file}</div>
                      {f.error ? (
                        <div className="text-xs text-rose-300">{f.error}</div>
                      ) : (
                        <div className="flex flex-wrap gap-1.5">
                          {(f.cells ?? []).map((cell, j) => (
                            <span
                              key={j}
                              className={`rounded-md px-1.5 py-0.5 text-xs ${
                                cell.name_confident && cell.count_confident
                                  ? 'bg-slate-800 text-slate-200'
                                  : 'bg-amber-500/20 text-amber-300 ring-1 ring-amber-400/50'
                              }`}
                            >
                              {cell.name}×{cell.count ?? '?'}{(!cell.name_confident || !cell.count_confident) && ' ⚠'}
                            </span>
                          ))}
                          {(f.cells ?? []).length === 0 && <span className="text-xs text-slate-500">没有识别到格子</span>}
                        </div>
                      )}
                    </div>
                  ))}
                  <p className="text-[11px] text-slate-500">⚠ 为低置信项（名称低置信的不填入），填入后请务必在下方核对修改。</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 全局开关 */}
      <div className="mt-3 space-y-2">
        <div className="flex items-center justify-between rounded-xl border border-slate-700 bg-slate-950/60 px-3 py-2">
          <span className="text-xs text-slate-300">SR / R（紫卡/蓝卡）命轮与角色碎片无限供应</span>
          <Switch checked={value.srRInfinite} onCheckedChange={(b) => onChange({ ...value, srRInfinite: b })} />
        </div>
        <p className="px-1 text-[11px] leading-5 text-slate-500">
          限定 SSR 的角色碎片默认按 <b className="text-slate-400">0</b> 处理，常驻 SSR 默认 <b className="text-emerald-400">∞ 无限</b>——在下表每行右侧点 ∞ 即可逐个切换。UR 暂不支持。
        </p>
      </div>

      {/* 统一库存表：每行 = 角色 + 命轮碎片 + 角色碎片（含∞开关） */}
      <div className="mt-3">
        <label className="mb-3 block">
          <span className="sr-only">搜索角色</span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索角色或别名"
            className="h-11 w-full rounded-xl border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-amber-400 focus:ring-2 focus:ring-amber-400/20"
          />
        </label>
        <div className="mb-1 grid grid-cols-[auto_1fr_auto_auto] items-center gap-2 px-2 text-[11px] text-slate-500">
          <span className="w-10" />
          <span>角色</span>
          <span className="w-16 text-center">命轮</span>
          <span className="w-[6.75rem] text-center">角色碎片 / ∞</span>
        </div>
        <Accordion type="multiple" value={openGroups} onValueChange={setOpenGroups}>
          {POOL_GROUPS.map((g) => {
            const isUR = g.key === 'UR'
            const visible = visibleGroups[g.key] ?? []
            if (query.trim() && visible.length === 0) return null
            return (
              <AccordionItem key={g.key} value={g.key} className="border-slate-700/60">
                <AccordionTrigger className={`py-2 text-sm ${isUR ? 'text-slate-500' : 'text-slate-200'}`}>
                  {g.title}
                  <span className="ml-1 text-xs font-normal text-slate-500">{visible.length}</span>
                  <span className="ml-2 hidden text-[11px] font-normal text-slate-500 sm:inline">{g.hint}</span>
                  {isUR && <span className="ml-2 rounded bg-slate-700/60 px-1.5 py-0.5 text-[10px] text-slate-400">暂不支持</span>}
                </AccordionTrigger>
                <AccordionContent>
                  {isUR && (
                    <p className="mb-2 rounded-lg border border-slate-700/60 bg-slate-950/60 px-2.5 py-1.5 text-[11px] leading-5 text-slate-500">
                      UR 角色暂不支持录入与规划，后续版本会优化。当前计算不包含 UR 命轮与角色碎片。
                    </p>
                  )}
                  <div className={`space-y-1.5 ${isUR ? 'pointer-events-none opacity-40' : ''}`}>
                    {visible.map((c) => {
                      const hasRoleToggle = c.rarity === 'UR' || c.rarity === 'SSR'
                      const wheelInf = (c.rarity === 'SR' || c.rarity === 'R') && value.srRInfinite
                      return (
                        <div key={c.name} className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-2 rounded-xl border border-slate-700/60 bg-slate-800/50 px-2 py-1.5">
                          <img src={`${import.meta.env.BASE_URL}${c.icon}`} alt={c.name} className="h-10 w-10 rounded-lg bg-slate-700 object-cover" loading="lazy" />
                          <div className="min-w-0">
                            <div className="truncate text-[13px] font-medium text-slate-100">{c.name}</div>
                            {hasRoleToggle && !isUR && <div className="text-[10px] text-amber-300/80">{c.name === '绘梨衣' ? '15/次进阶' : '30/次进阶'}</div>}
                          </div>
                          <NumInput ariaLabel={`${c.name}命轮碎片`} value={value.wheel[c.name] ?? ''} onChange={(v) => setWheel(c.name, v)} disabled={wheelInf} />
                          {hasRoleToggle ? (
                            <RoleFragCell
                              name={c.name}
                              infinite={isRoleInfinite(c)}
                              value={value.role[c.name] ?? ''}
                              onToggle={(next) => setRoleInfinite(c.name, next)}
                              onChange={(v) => setRole(c.name, v)}
                            />
                          ) : (
                            <div className="flex items-center gap-1">
                              <NumInput ariaLabel={`${c.name}角色碎片`} value="" onChange={() => {}} disabled />
                              <span className="w-10" />
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </AccordionContent>
              </AccordionItem>
            )
          })}
        </Accordion>
        <p className="mt-2 text-[11px] leading-5 text-slate-500">
          「命轮」= 命轮碎片（升星用）；「角色碎片」= 进阶用。每行右侧 <b className="text-emerald-400">∞</b> 亮 = 无限，灭 = 按所填数量；限定 SSR 默认 0，常驻 SSR 默认无限。导入的数据都会落到这里，可直接改数。
        </p>
      </div>
    </section>
  )
}
