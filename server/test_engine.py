#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""engine.compute_demands 单元测试。

运行：python3 server/test_engine.py（需要 managed Python，engine 依赖 highspy/openpyxl 的 import 在模块级）

核心口径（2026-09-04 用户确认）：
未明确填写角色碎片的限定SSR/UR（进阶预算 0）= 不能进阶，
其组合里的常驻SSR需求只计第 1 轮（升到表定星级后卡住），而不是整组排除、也不是按满 5 轮虚增。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import ADV_TO_FINAL, compute_demands  # noqa: E402

COMMON = {"常驻A", "常驻B"}


def combo(stars: int, members: list[str], scarce: dict[str, int]) -> dict:
    return {"stars": stars, "members": members, "scarce": scarce}


class TestComputeDemands(unittest.TestCase):
    def test_no_role_frag_counts_as_cannot_advance(self):
        """限定SSR 未实报角色碎片（预算0）→ 只计第 1 轮，不按满轮、不排除。"""
        d, _ = compute_demands([combo(3, ["常驻A", "限定B"], {"限定B": 0})], COMMON)
        self.assertEqual(d["常驻A"], 3 * 1)

    def test_zero_budget_not_excluded(self):
        """预算0的组合不能把需求计成 0。"""
        d, _ = compute_demands([combo(5, ["常驻A", "限定B"], {"限定B": 0})], COMMON)
        self.assertEqual(d["常驻A"], 5)

    def test_common_only_combo_full_rounds(self):
        """全常驻SSR组合按满轮计。"""
        d, _ = compute_demands([combo(3, ["常驻A", "常驻B"], {})], COMMON)
        self.assertEqual(d["常驻A"], 3 * ADV_TO_FINAL)
        self.assertEqual(d["常驻B"], 3 * ADV_TO_FINAL)

    def test_budget_limits_rounds(self):
        """有角色碎片但不足拉满 → 轮次 = 预算+1。"""
        d, _ = compute_demands([combo(4, ["常驻A", "限定C"], {"限定C": 2})], COMMON)
        self.assertEqual(d["常驻A"], 4 * 3)

    def test_multi_scarce_uses_min_budget(self):
        """多个稀缺成员取最低预算。"""
        d, _ = compute_demands([combo(3, ["常驻A", "限定B", "限定C"], {"限定B": 10, "限定C": 1})], COMMON)
        self.assertEqual(d["常驻A"], 3 * 2)

    def test_budget_capped_at_full_rounds(self):
        """预算超过拉满所需也只计满轮。"""
        d, _ = compute_demands([combo(3, ["常驻A", "限定B"], {"限定B": 99})], COMMON)
        self.assertEqual(d["常驻A"], 3 * ADV_TO_FINAL)

    def test_demand_all_always_full_rounds(self):
        """demand_all 是理论满轮需求，不随预算变化。"""
        _, da = compute_demands([combo(3, ["常驻A", "限定B"], {"限定B": 0})], COMMON)
        self.assertEqual(da["常驻A"], 3 * ADV_TO_FINAL)

    def test_non_common_members_ignored(self):
        """只统计常驻SSR的需求。"""
        d, da = compute_demands([combo(3, ["限定B", "SR甲"], {"限定B": 0})], COMMON)
        self.assertEqual(d, {})
        self.assertEqual(da, {})

    def test_accumulate_across_combos(self):
        """同一常驻SSR在多个组合中的需求累加。"""
        combos = [
            combo(3, ["常驻A"], {}),                       # 15
            combo(3, ["常驻A", "限定B"], {"限定B": 0}),     # 3（不能进阶）
            combo(4, ["常驻A", "限定C"], {"限定C": 2}),     # 12
        ]
        d, _ = compute_demands(combos, COMMON)
        self.assertEqual(d["常驻A"], 3 * ADV_TO_FINAL + 3 * 1 + 4 * 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
