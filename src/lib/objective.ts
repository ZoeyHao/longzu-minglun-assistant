export interface ObjectiveParams {
  mainElement: string
  objectivePreset: 'main' | 'consume' | 'custom'
  customChain: string
}

export function buildChain(p: ObjectiveParams): string[] {
  const main = p.mainElement
  const elementGoals = main === '精神' ? [`${main}元素`, `${main}元素%`] : [`${main}元素`]
  const attackGoals = main === '精神' ? ['攻击', '攻击%'] : ['攻击']
  if (p.objectivePreset === 'consume') {
    return ['消耗', ...elementGoals, ...attackGoals, '命轮值']
  }
  if (p.objectivePreset === 'custom') {
    return p.customChain.split(/[,，、\s]+/).map((item) => item.trim()).filter(Boolean)
  }
  return [...elementGoals, '消耗', ...attackGoals, '命轮值']
}
