import { useMemo, useState } from 'react'
import type { PlanResult, PlanRow } from '@/types'
import { Button } from '@/components/ui/button'

const ELEMENT_COLOR: Record<string, string> = {
  精神: 'text-violet-300 border-violet-500/40 bg-violet-500/10',
  火: 'text-rose-300 border-rose-500/40 bg-rose-500/10',
  风: 'text-emerald-300 border-emerald-500/40 bg-emerald-500/10',
  水: 'text-sky-300 border-sky-500/40 bg-sky-500/10',
  土: 'text-amber-300 border-amber-500/40 bg-amber-500/10',
}

function fmt(n: number): string {
  return Number.isInteger(n)
    ? n.toLocaleString('en-US')
    : n.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

function PlanCard({ row, main }: { row: PlanRow; main: string }) {
  const [open, setOpen] = useState(false)
  const mainGain = row.effects.find((e) => e.includes(`${main}元素+`))
  return (
    <div className="rounded-xl border border-slate-700/60 bg-slate-950/60 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-semibold text-slate-100">{row.name}</span>
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">{row.stars}星组合</span>
            {row.missing_fields.length > 0 && (
              <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-300">缺:{row.missing_fields.join('、')}</span>
            )}
          </div>
          <div className="mt-1 truncate text-xs text-slate-400">{row.members.join('、')}</div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-sm font-bold text-amber-300">{row.x}星 → {row.stage}</div>
          {row.advances > 0 && <div className="text-[10px] text-slate-500">进阶×{row.advances}</div>}
        </div>
      </div>
      <button onClick={() => setOpen(!open)} className="mt-1.5 text-[11px] text-slate-500 underline decoration-dotted">
        {open ? '收起效果' : `效果：${mainGain ?? row.effects[0] ?? ''}${open ? '' : ' …'}`}
      </button>
      {open && (
        <div className="mt-1 space-y-0.5 text-[11px] leading-5 text-slate-400">
          <div>每星：{row.effects.join('；')}</div>
          {row.advance_effects.length > 0 && <div>每次进阶：{row.advance_effects.join('；')}</div>}
          <div className="text-slate-600">来源：{row.source}</div>
        </div>
      )}
    </div>
  )
}

export default function ResultSection({
  result,
  onSave,
  saving,
}: {
  result: PlanResult
  onSave: () => void
  saving: boolean
}) {
  const [showDark, setShowDark] = useState(false)
  const grouped = useMemo(() => {
    const g: Record<string, Record<string, PlanRow[]>> = {}
    for (const row of result.plan) {
      ;((g[row.element] ||= {})[row.wheel] ||= []).push(row)
    }
    return g
  }, [result])

  const roleEntries = Object.entries(result.role_settlement).filter(([, v]) => v.stock > 0 || v.advances > 0)
  const wheelRemaining = Object.entries(result.wheel_remaining).filter(([, v]) => v !== '无限')
  const sel = result.selectable

  return (
    <section className="rounded-2xl border border-amber-500/30 bg-slate-900/60 p-4 shadow-xl">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold text-amber-300">③ 命轮方案</h2>
        <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-semibold text-emerald-300">
          {result.status === 'optimal' ? 'MILP 认证最优' : result.status}
        </span>
        <span className="text-xs text-slate-500">目标链：{result.objective_chain.join(' → ')}</span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onSave}
          disabled={saving}
          className="ml-auto h-9 border-amber-500/40 bg-amber-500/10 text-xs text-amber-200 hover:bg-amber-500/20 hover:text-amber-100"
        >
          {saving ? '保存中…' : '保存方案'}
        </Button>
      </div>

      {/* 总览卡片 */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {result.objective_chain.filter((o) => o !== '消耗').map((o) => (
          <div key={o} className="rounded-xl border border-slate-700/60 bg-slate-950/60 p-3 text-center">
            <div className="text-[11px] text-slate-400">{o}</div>
            <div className="mt-0.5 text-lg font-bold text-amber-300">+{fmt(result.objectives[o] ?? 0)}{o.endsWith('%') ? '%' : ''}</div>
          </div>
        ))}
        <div className="rounded-xl border border-slate-700/60 bg-slate-950/60 p-3 text-center">
          <div className="text-[11px] text-slate-400">点亮星数 / 消耗碎片</div>
          <div className="mt-0.5 text-lg font-bold text-slate-100">{result.stars_lit} 星</div>
          <div className="text-[11px] text-slate-500">{result.fragments_consumed} 片</div>
        </div>
      </div>

      {/* 自选分配 */}
      {sel.total > 0 && (
        <div className="mt-3 rounded-xl border border-slate-700/60 bg-slate-950/60 p-3 text-xs leading-6 text-slate-300">
          <b className="text-amber-300">命轮自选：</b>
          可用 {sel.total} · 已用 {sel.used} · 留存 {sel.remaining}
          <div>分配：{Object.keys(sel.allocation).length ? Object.entries(sel.allocation).map(([k, v]) => `${k}×${v}`).join('、') : '未使用'}</div>
          <div className="text-slate-500">
            缺口 top5（常驻SSR 需求−库存；含限定SSR/UR 的组合按可进阶轮次计，无角色碎片=只计第1轮）：{Object.keys(sel.eligible).length
              ? Object.entries(sel.eligible).map(([k, v]) => `${k}(缺${v})`).join('、')
              : '无缺口（库存已覆盖需求）'}
          </div>
        </div>
      )}

      {/* 方案明细 */}
      {Object.entries(grouped).map(([element, wheels]) => (
        <div key={element} className="mt-4">
          <div className={`mb-2 inline-block rounded-lg border px-2 py-0.5 text-sm font-bold ${ELEMENT_COLOR[element] ?? ''}`}>{element}元素</div>
          {Object.entries(wheels).map(([wheel, rows]) => (
            <div key={wheel} className="mb-3">
              <div className="mb-1.5 text-xs font-semibold text-slate-400">【{wheel}】{rows.length} 个组合</div>
              <div className="space-y-1.5">
                {rows.map((row) => <PlanCard key={row.name} row={row} main={result.main_element} />)}
              </div>
            </div>
          ))}
        </div>
      ))}

      {/* 角色碎片结算 */}
      {roleEntries.length > 0 && (
        <div className="mt-4">
          <div className="mb-1.5 text-xs font-semibold text-slate-400">限定 SSR 角色碎片结算</div>
          <div className="overflow-x-auto rounded-xl border border-slate-700/60">
            <table className="w-full min-w-[480px] text-xs">
              <thead>
                <tr className="bg-slate-800/80 text-slate-300">
                  <th className="px-2 py-1.5 text-left">角色</th>
                  <th className="px-2 py-1.5 text-right">单次成本</th>
                  <th className="px-2 py-1.5 text-right">进阶次数</th>
                  <th className="px-2 py-1.5 text-right">消耗/库存</th>
                  <th className="px-2 py-1.5 text-right">剩余</th>
                </tr>
              </thead>
              <tbody>
                {roleEntries.map(([name, v]) => (
                  <tr key={name} className="border-t border-slate-800 text-slate-300">
                    <td className="px-2 py-1.5">{name}<span className="ml-1 text-[10px] text-slate-500">{v.pool}</span></td>
                    <td className="px-2 py-1.5 text-right">{v.cost_per_advance ?? '未知'}</td>
                    <td className="px-2 py-1.5 text-right">{v.advances}</td>
                    <td className="px-2 py-1.5 text-right">{v.spent ?? '-'}/{v.stock}</td>
                    <td className={`px-2 py-1.5 text-right ${(v.remaining ?? 1) < 0 ? 'text-rose-300' : 'text-emerald-300'}`}>{v.remaining ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 剩余命轮碎片 */}
      {wheelRemaining.length > 0 && (
        <div className="mt-4">
          <div className="mb-1.5 text-xs font-semibold text-slate-400">剩余命轮碎片（方案内已无法凑星利用）</div>
          <div className="flex flex-wrap gap-1.5">
            {wheelRemaining.sort((a, b) => Number(b[1]) - Number(a[1])).map(([k, v]) => (
              <span key={k} className="rounded-md bg-slate-800 px-1.5 py-0.5 text-xs text-slate-300">{k}×{v}</span>
            ))}
          </div>
        </div>
      )}

      {/* 未点亮组合 */}
      {result.not_lit.length > 0 && (
        <div className="mt-4">
          <button onClick={() => setShowDark(!showDark)} className="text-xs font-semibold text-slate-400 underline decoration-dotted">
            {showDark ? '收起' : '查看'}未点亮组合（{result.not_lit.length} 个）
          </button>
          {showDark && (
            <div className="mt-2 space-y-1">
              {result.not_lit.map((d) => (
                <div key={`${d.element}${d.wheel}${d.name}`} className="rounded-lg border border-slate-800 bg-slate-950/40 px-2 py-1.5 text-xs text-slate-400">
                  <span className="text-slate-300">{d.element}/{d.wheel}/{d.name}</span>（{d.stars}星）
                  {d.reasons.length > 0 && <span className="ml-1 text-rose-300/80">—— {d.reasons.join('；')}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 口径 */}
      <details className="mt-4 rounded-xl border border-slate-800 bg-slate-950/40 p-3 text-[11px] leading-5 text-slate-500">
        <summary className="cursor-pointer text-xs text-slate-400">计算口径与假设</summary>
        <ul className="mt-1 list-disc pl-4">
          {result.assumptions.map((a, i) => <li key={i}>{a}</li>)}
        </ul>
      </details>
    </section>
  )
}
