#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输出网站前端所需的游戏数据 JSON：角色（含档位/卡池/头像/别名）、元素、组合概览。"""
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

RECOGNIZABLE = "命盘截图识别可识别的背包物品"


def main() -> None:
    refs = SKILL / "references"
    wb_path = refs / "minglun-source.xlsx"
    avatar = json.loads((refs / "avatar-map.json").read_text(encoding="utf-8"))
    pool = json.loads((refs / "ssr-pool.json").read_text(encoding="utf-8"))
    common = {QM.canonical_name(n) for n in pool["常驻SSR"]}
    limited = {QM.canonical_name(n) for n in pool["限定SSR"]}

    characters = []
    for p in avatar["portraits"]:
        name = QM.canonical_name(p["canonical_name"])
        r = p["rarity"]
        if r == "SSR":
            ppool = "常驻SSR" if name in common else ("限定SSR" if name in limited else "SSR待确认")
        else:
            ppool = r
        icon = p.get("icon_asset") or ""
        icon = icon.replace("assets/avatar-icons/", "avatars/")
        characters.append({
            "name": name,
            "rarity": r,
            "pool": ppool,
            "icon": icon,
            "aliases": p.get("aliases") or [],
        })

    aliases = dict(QM.ALIASES)
    for k, v in (pool.get("aliases") or {}).items():
        aliases.setdefault(k, v)
    for p in avatar["portraits"]:
        for a in p.get("aliases") or []:
            aliases.setdefault(a, QM.canonical_name(p["canonical_name"]))

    wb = QM.load(wb_path)
    counts = {}
    for sheet in QM.ELEMENT_SHEETS:
        rows = QM.parse_combo_rows(wb[sheet])
        counts[sheet.removesuffix("元素")] = len(rows)

    print(json.dumps({
        "workbook_sha256": hashlib.sha256(wb_path.read_bytes()).hexdigest()[:12],
        "characters": characters,
        "aliases": aliases,
        "elements": ["精神", "火", "风", "水", "土"],
        "wheels": ["创始", "物质", "执行"],
        "combo_counts": counts,
        "recognizable_count": len(characters),
        "recognizable_note": RECOGNIZABLE,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
