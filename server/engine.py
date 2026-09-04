#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙族命轮规划引擎（网站版，MILP 逐目标认证最优）。

口径（2026-09-03 用户确认，见 skill references/account-profile.md）：
- 品阶 白→绿→蓝→紫→橙→彩，彩0为终点；点满彩0需 5 次进阶。
- 组合变量 x=点亮总星数，a=进阶次数；耦合 s·a ≤ x ≤ s·(a+1)（s=表定星级）。
- 命轮碎片按库存逐星消耗；SR/R 等可声明为无限档位。
- 角色碎片（进阶用）：绘梨衣 15/次，其余限定 SSR 30/次；UR 同为 30/次（2026-09-04 确认）。
- 2026-09-04 起改为逐角色 ∞ 开关：role_fragments_infinite 列出的角色无限；
  常驻 SSR / SR / R 默认无限（可关）；限定 SSR / UR 默认按 0（需实报或开 ∞）。
- 自选碎片为共享池：统计常驻SSR需求，含限定SSR/UR 的组合按可进阶轮次计（无角色碎片=不能进阶只计第1轮），按缺口（需求−当前库存）取 top5 分配，多余留存。

用法：python3 engine.py payload.json  → stdout 输出结果 JSON。
依赖：highspy、openpyxl，以及 skill 自带的 query_minglun.py / plan_minglun.py。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SKILL = Path(os.environ.get(
    "MINGLUN_SKILL_DIR",
    str(Path(__file__).resolve().parent / "skill"),
))
sys.path.insert(0, str(SKILL / "scripts"))

import highspy  # noqa: E402
from highspy import Highs, HighsVarType  # noqa: E402

import query_minglun as QM  # noqa: E402
from plan_minglun import parse_attributes  # noqa: E402

TIERS = ["白", "绿", "蓝", "紫", "橙", "彩"]
ADV_TO_FINAL = len(TIERS) - 1  # 5 次进阶到彩0
INF = 10 ** 9
LIMITED_COST = {"绘梨衣": 15}
DEFAULT_LIMITED_COST = 30
DEFAULT_CHAIN = ["{main}元素", "{main}元素%", "消耗", "攻击", "攻击%", "命轮值"]


def compute_demands(combos: list[dict], common_ssr: set[str]) -> tuple[dict, dict]:
    """自选碎片缺口的需求统计，返回 (demand_common, demand_all)。

    轮次规则（每个组合内常驻SSR成员的需求 = 表定星级 × 轮次）：
    - 全常驻SSR组合：满 ADV_TO_FINAL 轮；
    - 含限定SSR/UR（稀缺成员）：轮次 = min(ADV_TO_FINAL, 最低进阶预算 + 1)；
      预算为 0 = 不能进阶，但仍可点亮第 1 轮（升到表定星级后卡住），计 1 轮。
    demand_all 为展示用的理论满轮需求。
    """
    demand_common: dict[str, int] = {}
    demand_all: dict[str, int] = {}
    for c in combos:
        if c["scarce"]:
            rounds = min(ADV_TO_FINAL, min(c["scarce"].values()) + 1)
        else:
            rounds = ADV_TO_FINAL
        for m in c["members"]:
            if m in common_ssr:
                demand_common[m] = demand_common.get(m, 0) + c["stars"] * rounds
                demand_all[m] = demand_all.get(m, 0) + c["stars"] * ADV_TO_FINAL
    return demand_common, demand_all


def fail(message: str, **extra) -> None:
    print(json.dumps({"status": "error", "error": message, **extra}, ensure_ascii=False))
    raise SystemExit(0)


def main() -> None:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    elements_req = payload.get("elements") or ["精神"]
    if "全部" in elements_req:
        elements = {"火", "风", "水", "土", "精神"}
    else:
        elements = {str(e).replace("元素", "") for e in elements_req}
    main_element = str(payload.get("main_element") or "精神").replace("元素", "")
    chain = payload.get("objective") or [t.format(main=main_element) for t in DEFAULT_CHAIN]

    inventory = {QM.canonical_name(k): int(v) for k, v in (payload.get("inventory") or {}).items() if int(v) > 0}
    wheel_inf_rarities = set(payload.get("wheel_infinite_rarities") or ["SR", "R"])
    wheel_inf_names = {QM.canonical_name(n) for n in (payload.get("wheel_infinite") or [])}
    role_frags = {QM.canonical_name(k): int(v) for k, v in (payload.get("role_fragments") or {}).items()}
    role_inf_names = {QM.canonical_name(n) for n in (payload.get("role_fragments_infinite") or [])}
    # 旧协议兼容：仅在未提供逐角色开关时生效；新前端恒发 role_fragments_infinite
    role_unlisted_infinite = bool(payload.get("role_unlisted_infinite", False)) and "role_fragments_infinite" not in payload
    ur_cost = {QM.canonical_name(k): int(v) for k, v in (payload.get("ur_advance_cost") or {}).items() if int(v) > 0}
    selectable_total = int(payload.get("selectable") or 0)

    pool = json.loads((SKILL / "references" / "ssr-pool.json").read_text(encoding="utf-8"))
    common_ssr = {QM.canonical_name(n) for n in pool["常驻SSR"]}
    limited_ssr = {QM.canonical_name(n) for n in pool["限定SSR"]}
    avatar = json.loads((SKILL / "references" / "avatar-map.json").read_text(encoding="utf-8"))
    rarity = {QM.canonical_name(p["canonical_name"]): p["rarity"] for p in avatar["portraits"]}

    wb = QM.load(SKILL / "references" / "minglun-source.xlsx")
    rows = QM.all_combos(wb)
    extra = json.loads((SKILL / "references" / "supplemental-combos.json").read_text(encoding="utf-8"))
    rows.extend(extra["combos"])

    # ---- 角色分类：命轮库存 + 进阶预算 ----
    def wheel_stock(m: str) -> int:
        if m in wheel_inf_names or rarity.get(m) in wheel_inf_rarities:
            return INF
        return inventory.get(m, 0)

    def role_cost(m: str) -> int:
        """每次进阶的角色碎片成本。"""
        if m in limited_ssr:
            return LIMITED_COST.get(m, DEFAULT_LIMITED_COST)
        if rarity.get(m) == "UR":
            return ur_cost.get(m, DEFAULT_LIMITED_COST)
        return DEFAULT_LIMITED_COST  # 常驻 SSR 等实报数量时按 30/次

    def advance_budget(m: str):
        """返回 (是否受限, 可进阶次数)。None 表示不受限（无限角色碎片）。

        逐角色 ∞ 开关优先；其次实报数量；
        缺省：常驻 SSR / SR / R 无限，限定 SSR / UR 按 0。
        """
        if m in role_inf_names:
            return False, None
        if m in role_frags:
            return True, role_frags[m] // role_cost(m)
        if m in limited_ssr:
            if role_unlisted_infinite:
                return False, None
            return True, 0
        if rarity.get(m) == "UR":
            return True, 0
        if m in common_ssr or rarity.get(m) in ("SR", "R"):
            return False, None
        # 未分类角色（如皇女零）：保守处理，不可进阶
        return True, 0

    combos, seen = [], set()
    for r in rows:
        if r["element"] not in elements:
            continue
        key = (r["element"], r["wheel"], r["name"])
        if key in seen:
            continue
        seen.add(key)
        members = [QM.canonical_name(m) for m in r["members"]]
        if not members:
            continue
        scarce = {}
        for m in members:
            limited, budget = advance_budget(m)
            if limited:
                scarce[m] = budget
        combos.append({
            "element": r["element"], "wheel": r["wheel"], "name": r["name"],
            "stars": int(r["stars"]), "members": members,
            "attrs": dict(parse_attributes(tuple(r.get("effects") or ()))),
            "adv_attrs": dict(parse_attributes(tuple(r.get("advance_effects") or ()), assume_percent=True)),
            "effects": list(r.get("effects") or []),
            "advance_effects": list(r.get("advance_effects") or []),
            "scarce": scarce, "source": r.get("source", ""),
            "missing_fields": list(r.get("missing_fields") or []),
        })

    # ---- 自选池资格：常驻SSR需求，按缺口（需求−当前库存）取 top5 ----
    # 含限定SSR/UR 的组合按可进阶轮次计需求；未实报角色碎片（预算0）= 不能进阶，只计第 1 轮。
    demand_common, demand_all = compute_demands(combos, common_ssr)
    gap = {}
    for m, d in demand_common.items():
        stock = wheel_stock(m)
        if stock >= INF:
            continue
        g = d - stock
        if g > 0:
            gap[m] = g
    eligible = {m: gap[m] for m in sorted(gap, key=lambda m: (-gap[m], m))[:5]}
    if selectable_total <= 0:
        eligible = {}

    # ---- MILP ----
    n = len(combos)
    if n == 0:
        fail("所选元素没有可用组合")
    member_names = sorted({m for c in combos for m in c["members"]})
    h = Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", 60.0)
    inf = highspy.kHighsInf
    for k in range(n):  # x_k：点亮星数
        h.addCol(0.0, 0.0, float(ADV_TO_FINAL * combos[k]["stars"]), 0, [], [])
        h.changeColIntegrality(k, HighsVarType.kInteger)
    for k in range(n):  # a_k：进阶次数
        h.addCol(0.0, 0.0, float(ADV_TO_FINAL), 0, [], [])
        h.changeColIntegrality(n + k, HighsVarType.kInteger)
    eligible_list = sorted(eligible)
    y_index = {m: 2 * n + i for i, m in enumerate(eligible_list)}
    for m in eligible_list:  # y_m：自选分配量（不超过缺口）
        h.addCol(0.0, 0.0, float(min(selectable_total, eligible[m])), 0, [], [])
        h.changeColIntegrality(y_index[m], HighsVarType.kInteger)
    for k in range(n):  # s·a ≤ x ≤ s·(a+1)
        s = float(combos[k]["stars"])
        h.addRow(-inf, s, 2, [k, n + k], [1.0, -s])
        h.addRow(-inf, 0.0, 2, [n + k, k], [s, -1.0])
    for m in member_names:  # 命轮库存（含自选池补给）
        idx = [k for k in range(n) if m in combos[k]["members"]]
        if not idx:
            continue
        stock = wheel_stock(m)
        if m in y_index and stock < INF:
            h.addRow(-inf, float(stock), len(idx) + 1, idx + [y_index[m]], [1.0] * len(idx) + [-1.0])
        else:
            h.addRow(-inf, float(min(stock, INF)), len(idx), idx, [1.0] * len(idx))
    if eligible_list:  # 自选池总量
        h.addRow(-inf, float(selectable_total), len(eligible_list),
                 [y_index[m] for m in eligible_list], [1.0] * len(eligible_list))
    scarce_members = sorted({m for c in combos for m in c["scarce"]})
    for m in scarce_members:  # 进阶预算
        budget = max(c["scarce"][m] for c in combos if m in c["scarce"])
        idx = [n + k for k in range(n) if m in combos[k]["scarce"]]
        if idx:
            h.addRow(-inf, float(budget), len(idx), idx, [1.0] * len(idx))

    attr_names = sorted({a for c in combos for a in list(c["attrs"]) + list(c["adv_attrs"])})

    def coeff(name: str):
        vec = [0.0] * (2 * n + len(eligible_list))
        if name == "消耗":
            for k in range(n):
                vec[k] = float(len(combos[k]["members"]))
        else:
            for k in range(n):
                vec[k] = float(combos[k]["attrs"].get(name, 0.0))
                vec[n + k] = float(combos[k]["adv_attrs"].get(name, 0.0))
        return vec

    unknown = [o for o in chain if o != "消耗" and o not in attr_names]
    if unknown:
        fail(f"未知目标：{'、'.join(unknown)}", available_objectives=["消耗"] + attr_names)

    results = {}
    for name in chain:
        vec = coeff(name)
        for i, v in enumerate(vec):
            h.changeColCost(i, v)
        h.setMaximize()
        h.run()
        status = h.getModelStatus()
        if status != highspy.HighsModelStatus.kOptimal:
            fail(f"目标[{name}]求解失败：{status}")
        sol = h.getSolution().col_value
        val = sum(vec[i] * v for i, v in enumerate(sol))
        results[name] = round(val, 4)
        idx = [i for i in range(len(vec)) if vec[i] != 0.0]
        h.addRow(val - 1e-6, inf, len(idx), idx, [vec[i] for i in idx])
        for i in range(len(vec)):
            h.changeColCost(i, 0.0)

    sol = h.getSolution().col_value
    plan, wheel_use, adv_use = [], {}, {}
    totals = {}
    for k in range(n):
        x, a = int(round(sol[k])), int(round(sol[n + k]))
        c = combos[k]
        if x == 0 and a == 0:
            continue
        for key_, v in c["attrs"].items():
            totals[key_] = round(totals.get(key_, 0.0) + v * x, 4)
        for key_, v in c["adv_attrs"].items():
            totals[key_] = round(totals.get(key_, 0.0) + v * a, 4)
        for m in c["members"]:
            wheel_use[m] = wheel_use.get(m, 0) + x
        for m in c["scarce"]:
            adv_use[m] = adv_use.get(m, 0) + a
        cur = x - a * c["stars"]
        stage = "彩0星（毕业）" if a == ADV_TO_FINAL else f"{TIERS[a]}{cur}星"
        plan.append({
            "element": c["element"], "wheel": c["wheel"], "name": c["name"],
            "stars": c["stars"], "x": x, "advances": a, "stage": stage,
            "members": c["members"], "effects": c["effects"],
            "advance_effects": c["advance_effects"], "source": c["source"],
            "missing_fields": c["missing_fields"],
        })
    order = {e: i for i, e in enumerate(["精神", "火", "风", "水", "土"])}
    wheel_order = {"创始": 0, "物质": 1, "执行": 2}
    plan.sort(key=lambda p: (order.get(p["element"], 9), wheel_order.get(p["wheel"], 9),
                             -(p["attrs"].get(f"{main_element}元素", 0) if False else 0), p["name"]))

    dark = []
    for c in combos:
        if any(p["element"] == c["element"] and p["wheel"] == c["wheel"] and p["name"] == c["name"] for p in plan):
            continue
        reasons = []
        for m in c["members"]:
            if wheel_stock(m) <= 0:
                reasons.append(f"{m}无命轮碎片")
        for m, b in c["scarce"].items():
            if b <= 0:
                reasons.append(f"{m}无角色碎片（不可进阶）")
        dark.append({"element": c["element"], "wheel": c["wheel"], "name": c["name"],
                     "stars": c["stars"], "members": c["members"], "reasons": reasons[:3]})

    allocation = {m: int(round(sol[y_index[m]])) for m in eligible_list if round(sol[y_index[m]]) > 0}
    role_settle = {}
    for m in scarce_members:
        limited, budget = advance_budget(m)
        cost = role_cost(m)
        stock = role_frags.get(m, 0)
        used = adv_use.get(m, 0)
        role_settle[m] = {
            "pool": "限定SSR" if m in limited_ssr else ("UR" if rarity.get(m) == "UR" else ("常驻SSR" if m in common_ssr else "未分类")),
            "cost_per_advance": cost,
            "stock": stock, "advances": used, "spent": used * cost,
            "remaining": stock - used * cost,
            "budget_advances": budget,
        }
    wheel_remaining = {}
    for m in member_names:
        stock = wheel_stock(m)
        if stock >= INF:
            wheel_remaining[m] = "无限"
            continue
        rem = stock - wheel_use.get(m, 0)
        if rem > 0:
            wheel_remaining[m] = rem

    result = {
        "status": "optimal",
        "elements": sorted(elements, key=lambda e: order.get(e, 9)),
        "main_element": main_element,
        "objective_chain": chain,
        "objectives": results,
        "totals": totals,
        "stars_lit": sum(p["x"] for p in plan),
        "fragments_consumed": sum(p["x"] * len(p["members"]) for p in plan),
        "plan": plan,
        "not_lit": dark,
        "wheel_remaining": wheel_remaining,
        "role_settlement": role_settle,
        "selectable": {
            "total": selectable_total,
            "eligible": eligible,
            "demand_common": {m: demand_common.get(m, 0) for m in eligible},
            "demand_all": {m: demand_all.get(m, 0) for m in eligible},
            "allocation": allocation,
            "used": sum(allocation.values()),
            "remaining": selectable_total - sum(allocation.values()),
        },
        "assumptions": [
            "所有组合从白0星开始；品阶 白→绿→蓝→紫→橙→彩，彩0星为终点（5次进阶）",
            "基础效果按每升1星计；精神页进阶效果按每次真实进阶计",
            f"命轮碎片无限档位：{'、'.join(sorted(wheel_inf_rarities)) or '无'}",
            "进阶成本：绘梨衣15/次，其余限定SSR 30/次，UR 30/次；"
            "角色碎片逐角色∞开关：限定SSR/UR默认0需实报，常驻SSR默认无限，SR/R恒无限",
            "自选碎片：统计常驻SSR需求——全常驻组合按满轮，含限定SSR/UR组合按可进阶轮次（无角色碎片=不能进阶只计第1轮），按缺口（需求−库存）取 top5 分配，多余留存",
        ],
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
