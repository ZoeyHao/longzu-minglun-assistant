import type { GameData } from '@/types'
import { Button } from '@/components/ui/button'
import { buildChain } from '@/lib/objective'

export interface ParamsState {
  nick: string
  selectable: string
  elements: string[]
  mainElement: string
  objectivePreset: 'main' | 'consume' | 'custom'
  customChain: string
}

const PRESETS = [
  { key: 'main', label: '主元素优先（兼顾清库存）' },
  { key: 'consume', label: '尽量清空库存' },
  { key: 'custom', label: '自定义目标链' },
] as const

export default function ParamsSection(props: {
  data: GameData
  value: ParamsState
  onChange: (v: ParamsState) => void
  onRun: () => void
  running: boolean
}) {
  const { data, value, onChange, onRun, running } = props
  const allSelected = value.elements.includes('全部')

  const toggleElement = (el: string) => {
    if (el === '全部') {
      onChange({ ...value, elements: allSelected ? ['精神'] : ['全部'] })
      return
    }
    const cur = value.elements.filter((e) => e !== '全部')
    const next = cur.includes(el) ? cur.filter((e) => e !== el) : [...cur, el]
    const elements = next.length ? next : ['精神']
    onChange({
      ...value,
      elements,
      mainElement: elements.includes(value.mainElement) ? value.mainElement : elements[0],
    })
  }

  const changeMainElement = (mainElement: string) => {
    const elements = allSelected || value.elements.includes(mainElement)
      ? value.elements
      : [...value.elements, mainElement]
    onChange({ ...value, mainElement, elements })
  }

  return (
    <section className="rounded-2xl border border-slate-700/60 bg-slate-900/60 p-4 shadow-xl">
      <h2 className="mb-3 text-base font-bold text-amber-300">② 目标与自选</h2>

      <div className="space-y-3 text-sm">
        <div>
          <div className="mb-1.5 text-xs text-slate-400">参与规划的元素（默认只点主元素，避免碎片被低价值组合吃掉）</div>
          <div className="flex flex-wrap gap-1.5">
            {['全部', ...data.elements].map((el) => {
              const active = el === '全部' ? allSelected : !allSelected && value.elements.includes(el)
              return (
                <button
                  key={el}
                  onClick={() => toggleElement(el)}
                  className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                    active ? 'bg-amber-500 text-slate-950' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
                  }`}
                >
                  {el}
                </button>
              )
            })}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-xs text-slate-400">主元素（目标链核心）</span>
            <select
              value={value.mainElement}
              onChange={(e) => changeMainElement(e.target.value)}
              className="h-11 w-full rounded-lg border border-slate-600 bg-slate-900/80 px-3 text-sm text-slate-100 outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-400/20"
            >
              {data.elements.map((el) => (
                <option key={el} value={el}>{el}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-slate-400">目标优先级</span>
            <select
              value={value.objectivePreset}
              onChange={(e) => onChange({ ...value, objectivePreset: e.target.value as ParamsState['objectivePreset'] })}
              className="h-11 w-full rounded-lg border border-slate-600 bg-slate-900/80 px-3 text-sm text-slate-100 outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-400/20"
            >
              {PRESETS.map((p) => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </select>
          </label>
        </div>

        {value.objectivePreset === 'custom' && (
          <input
            value={value.customChain}
            onChange={(e) => onChange({ ...value, customChain: e.target.value })}
            placeholder="例：精神元素,精神元素%,消耗,攻击,攻击%,命轮值"
            className="h-11 w-full rounded-lg border border-slate-600 bg-slate-900/80 px-3 text-sm text-slate-100 outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-400/20"
          />
        )}
        <div className="rounded-lg bg-slate-950/60 px-3 py-2 text-xs leading-5 text-slate-400">
          当前目标链：{buildChain(value).join(' → ')}
        </div>

        <div>
          <label className="block">
            <span className="mb-1 block text-xs text-slate-400">昵称（选填，保存历史方案时使用）</span>
            <input
              type="text" maxLength={24} autoComplete="off"
              value={value.nick}
              onChange={(e) => onChange({ ...value, nick: e.target.value })}
              placeholder="输入你的游戏昵称"
              className="h-11 w-full rounded-lg border border-slate-600 bg-slate-900/80 px-3 text-sm text-slate-100 outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-400/20"
            />
          </label>
        </div>

        <div>
          <label className="block">
            <span className="mb-1 block text-xs text-slate-400">命轮自选碎片数量（按缺口 top5 分配：含限定SSR/UR 的组合按可进阶轮次计需求，多余留存）</span>
            <input
              type="number" inputMode="numeric" min={0} autoComplete="off"
              value={value.selectable}
              onChange={(e) => onChange({ ...value, selectable: e.target.value })}
              className="h-11 w-full rounded-lg border border-slate-600 bg-slate-900/80 px-3 text-right text-sm text-slate-100 outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-400/20"
            />
          </label>
        </div>

        <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs leading-5 text-amber-100/70">
          当前版本按所有组合从白 0 星开始规划。已点过命轮的账号请把结果视为剩余库存的理论方案，暂不用于精确跨阶结算。
        </div>

        <Button size="lg" onClick={onRun} disabled={running} className="h-12 w-full bg-gradient-to-r from-amber-500 to-orange-500 text-base font-bold text-slate-950 shadow-lg shadow-amber-950/20 hover:from-amber-400 hover:to-orange-400">
          {running ? '求解中…（MILP 认证最优）' : '生成命轮方案'}
        </Button>
      </div>
    </section>
  )
}
