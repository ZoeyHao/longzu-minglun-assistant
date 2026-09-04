import { useEffect, useMemo, useState } from 'react'
import type { CombosData } from '@/types'
import { combosExcelUrl, fetchCombos } from '@/lib/api'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

const POOL_COLOR: Record<string, string> = {
  UR: 'text-amber-300',
  限定SSR: 'text-orange-300',
  常驻SSR: 'text-sky-300',
  SR: 'text-violet-300',
  R: 'text-slate-400',
}

export default function DataDetailDialog(props: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { open, onOpenChange } = props
  const [data, setData] = useState<CombosData | null>(null)
  const [error, setError] = useState('')
  const [element, setElement] = useState('全部')
  const [keyword, setKeyword] = useState('')

  useEffect(() => {
    if (!open || data) return
    fetchCombos().then(setData).catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [open, data])

  const filtered = useMemo(() => {
    if (!data) return []
    const kw = keyword.trim()
    return data.combos.filter((c) => {
      if (element !== '全部' && c.element !== element) return false
      if (kw && !c.name.includes(kw) && !c.members.some((m) => m.name.includes(kw))) return false
      return true
    })
  }, [data, element, keyword])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88vh] w-[calc(100vw-1.5rem)] max-w-4xl flex-col border-slate-700 bg-slate-900 p-0 text-slate-100">
        <DialogHeader className="border-b border-slate-700/60 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2 pr-6">
            <DialogTitle className="text-sm font-bold text-amber-300">数据明细</DialogTitle>
            <a
              href={combosExcelUrl}
              download="命轮组合明细.xlsx"
              className="inline-flex h-7 items-center gap-1 rounded-lg border border-emerald-500/50 bg-emerald-500/10 px-2.5 text-[11px] font-medium text-emerald-300 transition-colors hover:bg-emerald-500/20"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              下载 Excel
            </a>
          </div>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {error && <div className="text-xs text-rose-300">{error}</div>}
          {!data && !error && <div className="py-8 text-center text-xs text-slate-500">加载中…</div>}
          {data && (
            <>
              <div className="mb-3 space-y-1 text-[11px] leading-5 text-slate-400">
                <p>
                  命轮工作簿 <span className="font-mono text-slate-300">{data.workbook_sha256}</span>
                  （SHA-256 前 12 位） · 明细共 <b className="text-amber-300">{data.combo_total}</b> 个组合
                  （工作簿 197 + 补充组合 1「时空守望人」）
                </p>
                <p className="text-slate-500">
                  品阶 白→绿→蓝→紫→橙→彩（5 次进阶） · 基础效果按每升 1 星计 · 进阶效果仅精神页提供 · 进阶成本：绘梨衣 15/次，其余限定 SSR 与 UR 30/次
                </p>
              </div>

              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                {['全部', ...data.elements].map((el) => (
                  <button
                    key={el}
                    onClick={() => setElement(el)}
                    className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition ${
                      element === el ? 'bg-amber-500 text-slate-950' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
                    }`}
                  >
                    {el}{el !== '全部' ? ` ${data.combo_counts[el] ?? 0}` : ''}
                    {el === '全部' ? ` ${data.combo_total}` : ''}
                  </button>
                ))}
                <input
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                  placeholder="搜组合名 / 成员"
                  className="h-7 min-w-28 flex-1 rounded-lg border border-slate-600 bg-slate-900/80 px-2 text-[11px] text-slate-100 outline-none focus:border-amber-400"
                />
              </div>

              <div className="overflow-x-auto rounded-xl border border-slate-700/60">
                <table className="w-full min-w-[860px] border-collapse text-left text-[11px]">
                  <thead className="sticky top-0 bg-slate-800 text-slate-300">
                    <tr>
                      <th className="px-2 py-1.5 font-medium">组合</th>
                      <th className="px-2 py-1.5 font-medium">成员</th>
                      <th className="px-2 py-1.5 font-medium">基础效果 / 星</th>
                      <th className="px-2 py-1.5 font-medium">进阶效果 / 次</th>
                      <th className="px-2 py-1.5 text-right font-medium">命轮/轮</th>
                      <th className="px-2 py-1.5 text-right font-medium">拉满5轮</th>
                      <th className="px-2 py-1.5 font-medium">进阶角色碎片</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((c) => (
                      <tr key={`${c.element}${c.wheel}${c.name}`} className="border-t border-slate-800 align-top text-slate-300 odd:bg-slate-950/40">
                        <td className="px-2 py-1.5">
                          <div className="font-medium text-slate-100">{c.name}</div>
                          <div className="mt-0.5 text-[10px] text-slate-500">
                            {c.element} · {c.wheel} · {c.stars}星
                            {c.missing_fields.length > 0 && <span className="ml-1 text-amber-400">缺:{c.missing_fields.join('、')}</span>}
                          </div>
                        </td>
                        <td className="px-2 py-1.5">
                          {c.members.map((m) => (
                            <span key={m.name} className={`mr-1.5 whitespace-nowrap ${POOL_COLOR[m.pool] ?? 'text-slate-300'}`}>
                              {m.name}
                              <span className="text-[9px] text-slate-500">{m.pool.replace('SSR', '')}</span>
                            </span>
                          ))}
                        </td>
                        <td className="px-2 py-1.5">{c.effects.length ? c.effects.join('；') : <span className="text-slate-600">—</span>}</td>
                        <td className="px-2 py-1.5">{c.advance_effects.length ? c.advance_effects.join('；') : <span className="text-slate-600">—</span>}</td>
                        <td className="px-2 py-1.5 text-right whitespace-nowrap">{c.wheel_per_member_round}/人·共{c.wheel_round_total}</td>
                        <td className="px-2 py-1.5 text-right whitespace-nowrap">{c.wheel_full_total}</td>
                        <td className="px-2 py-1.5">
                          {c.members.some((m) => m.advance_cost)
                            ? c.members.filter((m) => m.advance_cost).map((m) => (
                                <span key={m.name} className="mr-1.5 whitespace-nowrap text-orange-300/90">{m.name} {m.advance_cost}/次</span>
                              ))
                            : <span className="text-slate-600">无限定/UR</span>}
                        </td>
                      </tr>
                    ))}
                    {filtered.length === 0 && (
                      <tr><td colSpan={7} className="px-2 py-6 text-center text-slate-500">没有匹配的组合</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[10px] text-slate-600">数据支持：君度 · 技术支持：Roy、梧桐落</p>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
