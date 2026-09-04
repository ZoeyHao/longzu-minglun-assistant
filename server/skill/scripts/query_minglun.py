#!/usr/bin/env python3
"""Read-only query and validation helper for the Longzu Minglun workbook."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
except ImportError as exc:  # pragma: no cover - environment-specific guidance
    raise SystemExit(
        "缺少 openpyxl。请改用 Codex workspace dependencies 返回的 managed Python 运行本脚本。"
    ) from exc


ELEMENT_SHEETS = ("火元素", "风元素", "水元素", "土元素", "精神元素")
WHEELS = ("创始", "物质", "执行")
STRATEGY_SHEETS = {
    "火": "火盘",
    "风": "风（有基础向）",
    "水": "水盘（更新后）",
    "土": None,
    "精神": "精神点法（老手向）",
    "攻击": "Sheet1",
}
ALIASES = {
    "恺撒": "凯撒",
    "诺诺": "陈墨瞳",
    "上杉绘梨衣": "绘梨衣",
    "象龟": "源稚生",
    "原稚女": "源稚女",
    "路鸣泽": "路明泽",
    "虾米": "夏弥",
    "苏恩熙": "苏恩曦",
    "花间琉璃": "风间琉璃",
    "Eva": "EVA",
    "eva": "EVA",
    "龙刃": "龙刃酒德麻衣",
    "龙麻": "龙刃酒德麻衣",
    "龙马樱井小幕": "龙马樱井小暮",
    "龙马小暮": "龙马樱井小暮",
    "天演苏恩熙": "天演苏恩曦",
    "释罪路明非": "弑罪路明非",
    "影镰凯撒": "影镰恺撒",
    "双源": "源稚生&源稚女",
    "剑苏": "剑御苏茜",
    "隐狩": "隐狩矢吹樱",
    "古德": "古德里安",
    "出生": "王将",
}
QUALITY_BY_UNIT_COST = {
    15: "限定SSR",
    30: "常驻SSR",
    60: "SR",
    120: "R",
}


def default_workbook() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "minglun-source.xlsx"


def skill_reference(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / "references" / name


def canonical_name(value: Any) -> str:
    text = str(value or "").strip()
    return ALIASES.get(text, text)


def normalize_element(value: str) -> str:
    text = value.strip().replace("元素", "")
    if text not in {"火", "风", "水", "土", "精神", "攻击"}:
        raise ValueError(f"未知元素/攻略类型：{value}")
    return text


def load(path: Path):
    if not path.exists():
        raise SystemExit(f"工作簿不存在：{path}")
    return load_workbook(path, read_only=False, data_only=True)


def parse_combo_rows(ws) -> list[dict[str, Any]]:
    combos: list[dict[str, Any]] = []
    wheel: str | None = None
    for row_no in range(1, ws.max_row + 1):
        name = ws.cell(row_no, 1).value
        star_value = ws.cell(row_no, 2).value
        if name in WHEELS:
            wheel = str(name)
            continue
        match = re.fullmatch(r"(\d+)星", str(star_value or "").strip())
        if not wheel or not isinstance(name, str) or not match:
            continue
        members = [
            str(ws.cell(row_no, col).value).strip()
            for col in range(6, 11)
            if ws.cell(row_no, col).value not in (None, "")
        ]
        effects = [
            str(ws.cell(row_no, col).value).strip()
            for col in range(3, 6)
            if ws.cell(row_no, col).value not in (None, "")
        ]
        advance_effects = [
            str(ws.cell(row_no, col).value).strip()
            for col in range(11, 14)
            if ws.cell(row_no, col).value not in (None, "")
        ]
        combos.append(
            {
                "element": ws.title.removesuffix("元素"),
                "wheel": wheel,
                "name": name.strip(),
                "stars": int(match.group(1)),
                "effects": effects,
                "advance_effects": advance_effects,
                "members": members,
                "source": f"{ws.title}!A{row_no}:J{row_no}",
            }
        )
    return combos


def all_combos(wb) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for sheet in ELEMENT_SHEETS:
        if sheet in wb.sheetnames:
            result.extend(parse_combo_rows(wb[sheet]))
    return result


def parse_element_totals(ws) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for name_col in (25, 29, 33):  # Y, AC, AG
        for row_no in range(2, ws.max_row + 1):
            name = ws.cell(row_no, name_col).value
            count = ws.cell(row_no, name_col + 1).value
            minglun = ws.cell(row_no, name_col + 2).value
            role = ws.cell(row_no, name_col + 3).value
            if not isinstance(name, str):
                continue
            if not isinstance(count, (int, float)) or not isinstance(minglun, (int, float)):
                continue
            count_int = int(count)
            role_int = int(role) if isinstance(role, (int, float)) else None
            unit_cost = role_int // count_int if role_int is not None and count_int else None
            totals[name] = {
                "source_name": name,
                "canonical_name": canonical_name(name),
                "combination_count": count_int,
                "minglun_fragments": int(minglun),
                "role_fragments": role_int,
                "role_fragments_per_combo": unit_cost,
                "quality": QUALITY_BY_UNIT_COST.get(unit_cost),
                "source": f"{ws.title}!{get_column_letter(name_col)}{row_no}:{get_column_letter(name_col + 3)}{row_no}",
            }
    return totals


def member_matches(source: str, query: str) -> bool:
    return canonical_name(source) == canonical_name(query)


def filter_combos(
    combos: Iterable[dict[str, Any]],
    element: str | None,
    wheel: str | None,
    name: str | None,
    member: str | None,
) -> list[dict[str, Any]]:
    result = []
    for combo in combos:
        if element and combo["element"] != element:
            continue
        if wheel and combo["wheel"] != wheel:
            continue
        if name and name not in combo["name"]:
            continue
        if member and not any(member_matches(item, member) for item in combo["members"]):
            continue
        result.append(combo)
    return result


def strategy_rows(ws) -> list[dict[str, Any]]:
    rows = []
    for row_no in range(1, ws.max_row + 1):
        cells = []
        for col_no in range(1, ws.max_column + 1):
            value = ws.cell(row_no, col_no).value
            if value in (None, ""):
                continue
            cells.append({"cell": f"{get_column_letter(col_no)}{row_no}", "value": value})
        if cells:
            rows.append({"row": row_no, "cells": cells})
    return rows


def output(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if isinstance(data, str):
        print(data)
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_overview(wb, path: Path, as_json: bool) -> None:
    counts = []
    total = 0
    all_star_targets = Counter()
    for sheet in ELEMENT_SHEETS:
        combos = parse_combo_rows(wb[sheet])
        by_wheel = Counter(item["wheel"] for item in combos)
        by_stars = Counter(item["stars"] for item in combos)
        all_star_targets.update(by_stars)
        row = {
            "sheet": sheet,
            **{wheel: by_wheel[wheel] for wheel in WHEELS},
            "total": len(combos),
            "star_targets": {f"{stars}星": count for stars, count in sorted(by_stars.items())},
        }
        counts.append(row)
        total += len(combos)
    data = {
        "workbook": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "sheets": wb.sheetnames,
        "combo_counts": counts,
        "total_combos": total,
        "star_target_counts": {f"{stars}星": count for stars, count in sorted(all_star_targets.items())},
        "strategy_sheets": STRATEGY_SHEETS,
    }
    if as_json:
        output(data, True)
        return
    print(f"工作簿：{path}")
    print(f"SHA-256：{data['sha256']}")
    print("元素页\t创始\t物质\t执行\t合计")
    for row in counts:
        print(f"{row['sheet']}\t{row['创始']}\t{row['物质']}\t{row['执行']}\t{row['total']}")
    print(f"总组合数：{total}")
    print("表定星级：" + "，".join(f"{stars}星 {count}" for stars, count in sorted(all_star_targets.items())))


def cmd_combos(wb, args) -> None:
    element = normalize_element(args.element) if args.element else None
    if element == "攻击":
        raise SystemExit("攻击不是基础元素；请改用 strategy --element 攻击。")
    combos = filter_combos(all_combos(wb), element, args.wheel, args.name, args.member)
    if args.json:
        output(combos, True)
        return
    if not combos:
        print("未找到匹配的基础页组合。")
        return
    for item in combos:
        advance = (
            f" | 进阶：{'；'.join(item['advance_effects'])}"
            if item["advance_effects"] else ""
        )
        print(
            f"[{item['source']}] {item['element']} / {item['wheel']} / {item['name']} / "
            f"{item['stars']}星 | {'；'.join(item['effects'])}{advance} | 成员：{'、'.join(item['members'])}"
        )


def find_total_for_member(totals: dict[str, dict[str, Any]], query: str) -> dict[str, Any] | None:
    matches = [value for key, value in totals.items() if member_matches(key, query)]
    if len(matches) == 1:
        return matches[0]
    exact = [value for value in matches if canonical_name(value["source_name"]) == canonical_name(query)]
    return exact[0] if len(exact) == 1 else None


def cmd_character(wb, args) -> None:
    element = normalize_element(args.element) if args.element else None
    if element == "攻击":
        raise SystemExit("攻击不是基础元素。")
    combos = filter_combos(all_combos(wb), element, args.wheel, None, args.character)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for combo in combos:
        grouped[combo["element"]].append(combo)
    results = []
    for element_name, items in sorted(grouped.items()):
        ws = wb[f"{element_name}元素"]
        total_entry = find_total_for_member(parse_element_totals(ws), args.character)
        source_spellings = sorted(
            {
                member
                for item in items
                for member in item["members"]
                if member_matches(member, args.character)
            }
        )
        unit_cost = total_entry["role_fragments_per_combo"] if total_entry else None
        result = {
            "element": element_name,
            "wheel": args.wheel or "全部",
            "query": args.character,
            "source_spellings": source_spellings,
            "combination_count": len(items),
            "minglun_fragments": sum(item["stars"] for item in items),
            "role_fragments_for_full_advance": len(items) * unit_cost if unit_cost is not None else None,
            "role_fragments_per_combo": unit_cost,
            "quality": total_entry["quality"] if total_entry else None,
            "combos": items,
            "element_total_source": total_entry["source"] if total_entry else None,
        }
        results.append(result)
    if args.json:
        output(results, True)
        return
    if not results:
        print("未在基础元素页找到该角色。可检查异名，或用 strategy 查看仅攻略页收录的角色。")
        return
    for result in results:
        role_cost = result["role_fragments_for_full_advance"]
        print(
            f"{result['element']} / {result['wheel']}：{result['combination_count']} 个组合，"
            f"单轮升至表定星级的命轮碎片 {result['minglun_fragments']}，进阶合计角色碎片（工作簿口径，游戏内实报为限定30/次、绘梨衣15/次）"
            f"{role_cost if role_cost is not None else '表内无法确定'}，"
            f"档位 {result['quality'] or '表内无法确定'}；源名称：{'、'.join(result['source_spellings'])}"
        )
        for combo in result["combos"]:
            print(f"  - [{combo['source']}] {combo['wheel']} / {combo['name']} / {combo['stars']}星")


def cmd_strategy(wb, args) -> None:
    element = normalize_element(args.element)
    sheet = STRATEGY_SHEETS[element]
    if not sheet:
        print(f"工作簿没有 {element} 的独立点法页。")
        return
    rows = strategy_rows(wb[sheet])
    if args.json:
        output({"sheet": sheet, "rows": rows}, True)
        return
    print(f"攻略页：{sheet}（以下是来源内容，不是无条件规则）")
    for row in rows:
        text = " | ".join(f"{cell['cell']}={cell['value']}" for cell in row["cells"])
        print(text)


def cmd_validate(wb, path: Path, as_json: bool) -> int:
    issues = []
    counts = {}
    for sheet in ELEMENT_SHEETS:
        if sheet not in wb.sheetnames:
            issues.append(f"缺少基础页：{sheet}")
            continue
        ws = wb[sheet]
        combos = parse_combo_rows(ws)
        counts[sheet] = len(combos)
        derived: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for combo in combos:
            if not combo["members"]:
                issues.append(f"{combo['source']} 没有成员")
            if not any(f"{combo['element']}元素" in effect for effect in combo["effects"]):
                issues.append(f"{combo['source']} 未找到对应元素效果")
            if sheet == "精神元素" and len(combo["advance_effects"]) != 3:
                issues.append(f"{combo['source']} 精神新版未找到3项进阶效果")
            for member in combo["members"]:
                derived[member][0] += 1
                derived[member][1] += combo["stars"]
        totals = parse_element_totals(ws)
        all_names = set(derived) | set(totals)
        for name in sorted(all_names):
            expected = totals.get(name)
            got = tuple(derived[name]) if name in derived else None
            expected_pair = (
                (expected["combination_count"], expected["minglun_fragments"])
                if expected
                else None
            )
            if got != expected_pair:
                issues.append(f"{sheet} {name}：逐组合={got}，汇总区={expected_pair}")
            if expected and expected["role_fragments_per_combo"] not in QUALITY_BY_UNIT_COST:
                issues.append(
                    f"{expected['source']} 无法识别每组合角色碎片成本："
                    f"{expected['role_fragments_per_combo']}"
                )

    avatar_map = json.loads(skill_reference("avatar-map.json").read_text(encoding="utf-8"))
    pool_map = json.loads(skill_reference("ssr-pool.json").read_text(encoding="utf-8"))
    portraits = avatar_map["portraits"]
    portrait_names = [item["canonical_name"] for item in portraits]
    rarity_counts = Counter(item["rarity"] for item in portraits)
    ssr_portrait_names = [
        item["canonical_name"] for item in portraits if item["rarity"] == "SSR"
    ]
    resident_names = pool_map["常驻SSR"]
    limited_names = pool_map["限定SSR"]
    classified_names = resident_names + limited_names
    if set(resident_names) & set(limited_names):
        issues.append("ssr-pool.json 的常驻SSR与限定SSR存在重复角色")
    if len(classified_names) != len(set(classified_names)):
        issues.append("ssr-pool.json 的角色分类存在重复项")
    if set(ssr_portrait_names) != set(classified_names):
        missing = sorted(set(ssr_portrait_names) - set(classified_names))
        extra = sorted(set(classified_names) - set(ssr_portrait_names))
        issues.append(f"SSR头像与池分类不一致：未分类={missing}，无头像={extra}")
    missing_assets = [
        item["icon_asset"]
        for item in avatar_map["portraits"]
        if not (Path(__file__).resolve().parent.parent / item["icon_asset"]).exists()
    ]
    if missing_assets:
        issues.append(f"缺少SSR头像资产：{missing_assets}")
    result = {
        "workbook": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "combo_counts": counts,
        "total_combos": sum(counts.values()),
        "avatar_portraits": len(portrait_names),
        "avatar_rarity_counts": dict(rarity_counts),
        "ssr_portraits": len(ssr_portrait_names),
        "ssr_pool_counts": {"常驻SSR": len(resident_names), "限定SSR": len(limited_names)},
        "status": "ok" if not issues else "failed",
        "issues": issues,
    }
    if as_json:
        output(result, True)
    else:
        print(f"校验：{result['status']}")
        print(f"组合数：{result['total_combos']} {counts}")
        print(f"头像映射：{result['avatar_portraits']} {result['avatar_rarity_counts']}")
        print(f"SSR池分类：{result['ssr_portraits']} {result['ssr_pool_counts']}")
        print(f"SHA-256：{result['sha256']}")
        for issue in issues:
            print(f"- {issue}")
    return 0 if not issues else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=default_workbook())
    subparsers = parser.add_subparsers(dest="command", required=True)

    overview = subparsers.add_parser("overview", help="显示页签、哈希和组合数量")
    overview.add_argument("--json", action="store_true")

    combos = subparsers.add_parser("combos", help="查询基础元素页组合")
    combos.add_argument("--element")
    combos.add_argument("--wheel", choices=WHEELS)
    combos.add_argument("--name")
    combos.add_argument("--member")
    combos.add_argument("--json", action="store_true")

    character = subparsers.add_parser("character", help="计算角色在目标范围的资源需求")
    character.add_argument("character")
    character.add_argument("--element")
    character.add_argument("--wheel", choices=WHEELS)
    character.add_argument("--json", action="store_true")

    strategy = subparsers.add_parser("strategy", help="读取对应攻略页的原始非空单元格")
    strategy.add_argument("--element", required=True)
    strategy.add_argument("--json", action="store_true")

    validate = subparsers.add_parser("validate", help="重算基础页并校验汇总区")
    validate.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        wb = load(args.workbook)
        if args.command == "overview":
            cmd_overview(wb, args.workbook, args.json)
        elif args.command == "combos":
            cmd_combos(wb, args)
        elif args.command == "character":
            cmd_character(wb, args)
        elif args.command == "strategy":
            cmd_strategy(wb, args)
        elif args.command == "validate":
            return cmd_validate(wb, args.workbook, args.json)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
