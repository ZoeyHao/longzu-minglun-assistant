import { useCallback, useEffect, useState } from 'react'
import type { HistorySummary } from '@/types'
import { fetchHistory, fetchHistoryRecord, type HistoryRecord } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export default function HistoryDialog(props: {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentNick: string
  onView: (rec: HistoryRecord) => void
  onLoad: (rec: HistoryRecord) => void
}) {
  const { open, onOpenChange, currentNick, onView, onLoad } = props
  const [records, setRecords] = useState<HistorySummary[] | null>(null)
  const [error, setError] = useState('')
  const [onlyMine, setOnlyMine] = useState(!!currentNick)
  const [busyId, setBusyId] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    setError('')
    try {
      setRecords(await fetchHistory(onlyMine && currentNick ? currentNick : undefined))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [onlyMine, currentNick])

  useEffect(() => {
    if (open) refresh()
  }, [open, refresh])

  const open_ = async (id: number, cb: (rec: HistoryRecord) => void) => {
    setBusyId(id)
    setError('')
    try {
      cb(await fetchHistoryRecord(id))
      onOpenChange(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] w-[calc(100vw-2rem)] max-w-lg flex-col border-slate-700 bg-slate-900 p-0 text-slate-100">
        <DialogHeader className="border-b border-slate-700/60 px-4 py-3">
          <div className="flex items-center justify-between gap-2 pr-6">
            <DialogTitle className="text-sm font-bold text-amber-300">历史方案（保留最近 100 条）</DialogTitle>
            <label className="flex items-center gap-1.5 text-[11px] font-normal text-slate-400">
              <input
                type="checkbox"
                checked={onlyMine}
                onChange={(e) => setOnlyMine(e.target.checked)}
                className="accent-amber-400"
              />
              只看当前昵称
            </label>
          </div>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
          {error && <div className="mb-2 text-xs text-rose-300">{error}</div>}
          {!records && !error && <div className="py-6 text-center text-xs text-slate-500">加载中…</div>}
          {records && records.length === 0 && (
            <div className="py-6 text-center text-xs text-slate-500">暂无记录。填好昵称并生成方案后会自动存档。</div>
          )}
          {records && records.length > 0 && (
            <div className="space-y-1.5">
              {records.map((r) => (
                <div key={r.id} className="rounded-xl border border-slate-700/60 bg-slate-800/50 px-2.5 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 text-xs">
                      <span className="font-semibold text-amber-300">{r.nick}</span>
                      <span className="ml-2 text-slate-500">{fmtTime(r.ts)}</span>
                    </div>
                    <div className="flex shrink-0 gap-1.5">
                      <Button size="sm" variant="outline" disabled={busyId === r.id}
                        className="h-7 border-slate-600 bg-slate-800 px-2 text-[11px] text-slate-300 hover:bg-slate-700 hover:text-slate-200"
                        onClick={() => open_(r.id, onView)}>
                        查看
                      </Button>
                      <Button size="sm" variant="outline" disabled={busyId === r.id}
                        className="h-7 border-slate-600 bg-slate-800 px-2 text-[11px] text-slate-300 hover:bg-slate-700 hover:text-slate-200"
                        onClick={() => open_(r.id, onLoad)}>
                        载入库存
                      </Button>
                    </div>
                  </div>
                  <div className="mt-1 text-[11px] text-slate-400">
                    {r.main_element}元素 +{r.main_flat.toLocaleString('en-US')}（+{r.main_pct}%） · 攻击 +{r.atk.toLocaleString('en-US')}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
