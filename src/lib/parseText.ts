import type { GameData, ParseReport } from '@/types'

/**
 * 解析用户口述文本，例如：
 *   恺撒56，陈墨瞳40，…… 碎片方面：风间琉璃90，…… 其余角色碎片无限供应，
 *   紫色角色和蓝色角色命轮及碎片都无限供应。
 *
 * 「碎片方面 / 角色碎片 / 进阶碎片」之后的内容按角色碎片解析，之前按命轮碎片解析。
 */
export function parseInventoryText(text: string, data: GameData): ParseReport {
  // 名称 → 规范名
  const nameMap = new Map<string, string>()
  for (const c of data.characters) {
    nameMap.set(c.name, c.name)
    for (const a of c.aliases) nameMap.set(a, c.name)
  }
  for (const [alias, canonical] of Object.entries(data.aliases)) {
    nameMap.set(alias, canonical)
  }
  // 更长的名字优先匹配（避免“路明非”吃掉“弑罪路明非”）

  const detectedSRInfinite = /紫(色)?(的|角色|卡)?[^。；\n]{0,12}无限/.test(text)
  const detectedRInfinite = /蓝(色)?(的|角色|卡)?[^。；\n]{0,12}无限/.test(text)
  const detectedUnlistedRoleInfinite = /(其余|其他|剩下)[^。；\n]{0,10}(角色碎片|碎片)[^。；\n]{0,6}无限/.test(text)

  // 分段：角色碎片部分
  let wheelPart = text
  let rolePart = ''
  const segRe = /(碎片方面|角色碎片|进阶碎片|角色碎篇)[:：]?/
  const seg = segRe.exec(text)
  if (seg && seg.index !== undefined) {
    // 避免把“其余角色碎片无限”误判为分段起点：要求分段词前没有“其余/其他”紧贴
    const before = text.slice(Math.max(0, seg.index - 4), seg.index)
    if (!/(其余|其他|剩下)/.test(before)) {
      wheelPart = text.slice(0, seg.index)
      rolePart = text.slice(seg.index + seg[0].length)
      // 角色碎片段里遇到新的“命轮”提示则截断
      const back = /命轮(碎片)?[:：]/.exec(rolePart)
      if (back) rolePart = rolePart.slice(0, back.index)
    }
  }

  const entryRe = /([A-Za-z一-龥&·]{1,12}?)\s*(\d{1,4})\s*(?:个|枚|片|张)?/g
  const wheel: Record<string, number> = {}
  const role: Record<string, number> = {}
  const unknown = new Set<string>()

  const fill = (part: string, target: Record<string, number>) => {
    entryRe.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = entryRe.exec(part))) {
      const rawName = m[1]
      const value = parseInt(m[2], 10)
      const canonical = nameMap.get(rawName)
      if (canonical) {
        target[canonical] = value
      } else if (!/^(命轮|碎片|角色|方面|无限|供应|都|和|及|其余|其他|紫色|蓝色|角色碎片)$/.test(rawName)) {
        unknown.add(rawName)
      }
    }
  }
  fill(wheelPart, wheel)
  if (rolePart) fill(rolePart, role)

  return {
    wheel,
    role,
    unknown: [...unknown],
    detectedSRInfinite,
    detectedRInfinite,
    detectedUnlistedRoleInfinite,
    wheelCount: Object.keys(wheel).length,
    roleCount: Object.keys(role).length,
  }
}
