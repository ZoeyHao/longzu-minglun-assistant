#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出命轮组合明细 JSON（供网站「数据源详情」弹窗）。

用法：python3 combos_data.py  → stdout 输出 JSON。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

SKILL = Path(os.environ.get(
    "MINGLUN_SKILL_DIR",
    str(Path(__file__).resolve().parent / "skill"),
))
sys.path.insert(0, str(SKILL / "scripts"))

import query_minglun as QM  # noqa: E402

LIMITED_COST = {"绘梨衣": 15}
DEFAULT_LIMITED_COST = 30
ADV_TO_FINAL = 5  # 白→绿→蓝→紫→橙→彩


def main() -> None:
    wb_path = SKILL / "references" / "minglun-source.xlsx"
    pool = json.loads((SKILL / "references" / "ssr-pool.json").read_text(encoding="utf-8"))
    common_ssr = {QM.canonical_name(n) for n in pool["常驻SSR"]}
    limited_ssr = {QM.canonical_name(n) for n in pool["限定SSR"]}
    avatar = json.loads((SKILL / "references" / "avatar-map.json").read_text(encoding="utf-8"))
    rarity = {QM.canonical_name(p["canonical_name"]): p["rarity"] for p in avatar["portraits"]}

    def member_pool(m: str) -> str:
        if m in limited_ssr:
            return "限定SSR"
        if m in common_ssr:
            return "常驻SSR"
        return rarity.get(m) or "未分类"

    def advance_cost(m: str) -> int | None:
        """每人每次进阶的角色碎片成本；None 表示该池默认无限、不构成瓶颈。"""
        p = member_pool(m)
        if p == "限定SSR":
            return LIMITED_COST.get(m, DEFAULT_LIMITED_COST)
        if p == "UR":
            return DEFAULT_LIMITED_COST
        return None

    wb = QM.load(wb_path)
    rows = QM.all_combos(wb)
    extra = json.loads((SKILL / "references" / "supplemental-combos.json").read_text(encoding="utf-8"))
    rows.extend(extra["combos"])

    combos = []
    seen = set()
    for r in rows:
        key = (r["element"], r["wheel"], r["name"])
        if key in seen:
            continue
        seen.add(key)
        members = [QM.canonical_name(m) for m in r["members"]]
        if not members:
            continue
        stars = int(r["stars"])
        combos.append({
            "element": r["element"],
            "wheel": r["wheel"],
            "name": r["name"],
            "stars": stars,
            "members": [{"name": m, "pool": member_pool(m), "advance_cost": advance_cost(m)} for m in members],
            "effects": list(r.get("effects") or []),
            "advance_effects": list(r.get("advance_effects") or []),
            "wheel_per_member_round": stars,
            "wheel_round_total": stars * len(members),
            "wheel_full_total": stars * ADV_TO_FINAL * len(members),
            "missing_fields": list(r.get("missing_fields") or []),
            "source": r.get("source", ""),
        })

    order = {e: i for i, e in enumerate(["精神", "火", "风", "水", "土"])}
    wheel_order = {"创始": 0, "物质": 1, "执行": 2}
    combos.sort(key=lambda c: (order.get(c["element"], 9), wheel_order.get(c["wheel"], 9), c["name"]))

    counts: dict[str, int] = {}
    for c in combos:
        counts[c["element"]] = counts.get(c["element"], 0) + 1

    print(json.dumps({
        "workbook_sha256": hashlib.sha256(wb_path.read_bytes()).hexdigest()[:12],
        "combo_counts": counts,
        "combo_total": len(combos),
        "elements": ["精神", "火", "风", "水", "土"],
        "wheels": ["创始", "物质", "执行"],
        "rounds_to_final": ADV_TO_FINAL,
        "combos": combos,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
