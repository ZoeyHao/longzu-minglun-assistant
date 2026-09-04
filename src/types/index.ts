export interface CharInfo {
  name: string
  rarity: 'UR' | 'SSR' | 'SR' | 'R'
  pool: string // 常驻SSR / 限定SSR / UR / SR / R
  icon: string
  aliases: string[]
}

export interface GameData {
  workbook_sha256: string
  characters: CharInfo[]
  aliases: Record<string, string>
  elements: string[]
  wheels: string[]
  combo_counts: Record<string, number>
  recognizable_count: number
  recognizable_note: string
}

export interface PlanRow {
  element: string
  wheel: string
  name: string
  stars: number
  x: number
  advances: number
  stage: string
  members: string[]
  effects: string[]
  advance_effects: string[]
  source: string
  missing_fields: string[]
}

export interface DarkRow {
  element: string
  wheel: string
  name: string
  stars: number
  members: string[]
  reasons: string[]
}

export interface RoleSettle {
  pool: string
  cost_per_advance: number | null
  stock: number
  advances: number
  spent: number | null
  remaining: number | null
  budget_advances: number
}

export interface PlanResult {
  status: string
  error?: string
  elements: string[]
  main_element: string
  objective_chain: string[]
  objectives: Record<string, number>
  totals: Record<string, number>
  stars_lit: number
  fragments_consumed: number
  plan: PlanRow[]
  not_lit: DarkRow[]
  wheel_remaining: Record<string, number | string>
  role_settlement: Record<string, RoleSettle>
  selectable: {
    total: number
    eligible: Record<string, number>
    demand_common: Record<string, number>
    demand_all: Record<string, number>
    allocation: Record<string, number>
    used: number
    remaining: number
  }
  assumptions: string[]
}

export interface RecognizeCell {
  row: number
  col: number
  name: string
  count: number | null
  name_confident: boolean
  count_confident: boolean
}

export interface RecognizeFileResult {
  file: string
  cells: RecognizeCell[] | null
  error: string | null
}

export interface ParseReport {
  wheel: Record<string, number>
  role: Record<string, number>
  unknown: string[]
  detectedSRInfinite: boolean
  detectedRInfinite: boolean
  detectedUnlistedRoleInfinite: boolean
  wheelCount: number
  roleCount: number
}

export interface HistorySummary {
  id: number
  nick: string
  ts: number
  main_element: string
  elements: string[]
  stars_lit: number
  fragments_consumed: number
  main_flat: number
  main_pct: number
  atk: number
}

export interface ComboMember {
  name: string
  pool: string
  advance_cost: number | null
}

export interface ComboDetail {
  element: string
  wheel: string
  name: string
  stars: number
  members: ComboMember[]
  effects: string[]
  advance_effects: string[]
  wheel_per_member_round: number
  wheel_round_total: number
  wheel_full_total: number
  missing_fields: string[]
  source: string
}

export interface CombosData {
  workbook_sha256: string
  combo_counts: Record<string, number>
  combo_total: number
  elements: string[]
  wheels: string[]
  rounds_to_final: number
  combos: ComboDetail[]
}
