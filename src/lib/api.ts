import type { CombosData, GameData, HistorySummary, PlanResult, RecognizeFileResult } from '@/types'

const API = `${import.meta.env.BASE_URL}api`

export async function fetchGameData(): Promise<GameData> {
  const res = await fetch(`${API}/game-data`)
  if (!res.ok) throw new Error(`游戏数据加载失败（${res.status}）`)
  return res.json()
}

export async function fetchCombos(): Promise<CombosData> {
  const res = await fetch(`${API}/combos`)
  if (!res.ok) throw new Error(`组合明细加载失败（${res.status}）`)
  return res.json()
}

export const combosExcelUrl = `${API}/combos.xlsx`

export interface PlanPayload {
  inventory: Record<string, number>
  wheel_infinite_rarities: string[]
  role_fragments: Record<string, number>
  role_fragments_infinite: string[]
  ur_advance_cost?: Record<string, number>
  selectable: number
  elements: string[]
  main_element: string
  objective?: string[]
}

export async function runPlan(payload: PlanPayload): Promise<PlanResult> {
  const res = await fetch(`${API}/plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || `求解失败（${res.status}）`)
  return data
}

export async function recognizeImages(images: string[]): Promise<RecognizeFileResult[]> {
  const res = await fetch(`${API}/recognize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ images }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || `识别失败（${res.status}）`)
  return data.results
}

export interface HistoryRecord extends HistorySummary {
  payload: PlanPayload
  result: PlanResult
}

export async function fetchHistory(nick?: string): Promise<HistorySummary[]> {
  const res = await fetch(`${API}/history${nick ? `?nick=${encodeURIComponent(nick)}` : ''}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || `历史加载失败（${res.status}）`)
  return data.records
}

export async function fetchHistoryRecord(id: number): Promise<HistoryRecord> {
  const res = await fetch(`${API}/history?id=${id}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || `记录加载失败（${res.status}）`)
  return data.record
}

export async function saveHistory(nick: string, payload: PlanPayload, result: PlanResult): Promise<number> {
  const res = await fetch(`${API}/history`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nick, payload, result }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || `存档失败（${res.status}）`)
  return data.id
}

export async function deleteHistory(id: number, nick: string): Promise<void> {
  const res = await fetch(`${API}/history`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, nick }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || `删除失败（${res.status}）`)
}
