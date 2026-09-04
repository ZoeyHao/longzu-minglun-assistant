#!/usr/bin/env python3
"""Plan Minglun star cycles with a dependency-free hybrid integer solver.

The model assumes every unreported combination starts at white 0-star.  A full
cycle raises a combination to its workbook target star count.  Common SSR/SR/R
combinations may complete at most five cycles (white -> green -> blue -> purple
-> orange -> rainbow 0-star; user-corrected 2026-09-03).
Combinations containing a limited SSR or UR may complete one cycle but cannot
advance unless a future state file explicitly supplies that mechanism.

Optional common-SSR selectable fragments are modeled as one shared pool.  The
solver checks the aggregate deficit of eligible characters instead of
enumerating every possible allocation, then derives the allocation from the
winning plan.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import random
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
TIERS = ("白", "绿", "蓝", "紫", "橙", "彩")  # 2026-09-03 用户更正：蓝橙之间有紫阶
OBJECTIVE_NAMES = {"精神元素", "精神元素%", "攻击", "攻击%", "消耗", "命轮值"}


def load_query_module():
    path = ROOT / "scripts" / "query_minglun.py"
    spec = importlib.util.spec_from_file_location("query_minglun", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


QM = load_query_module()


@dataclass(frozen=True)
class Candidate:
    element: str
    wheel: str
    name: str
    stars: int
    members: tuple[str, ...]
    effects: tuple[str, ...]
    advance_effects: tuple[str, ...]
    attributes: tuple[tuple[str, float], ...]
    advance_attributes: tuple[tuple[str, float], ...]
    source: str
    missing_fields: tuple[str, ...]
    max_cycles: int
    advance_blocked: bool
    role_costs_for_full_advance: tuple[tuple[str, int], ...]
    unmodeled_advance_blocker: bool

    @property
    def key(self) -> str:
        return f"{self.element}/{self.wheel}/{self.name}"


@dataclass(frozen=True)
class SelectablePool:
    total: int = 0
    eligible: frozenset[str] = frozenset()


def selectable_used(inventory: dict[str, int], pool: SelectablePool) -> int:
    """Return how many shared selectable fragments cover current deficits."""
    return sum(max(0, -inventory.get(name, 0)) for name in pool.eligible)


def selectable_allocation(
    inventory: dict[str, int], pool: SelectablePool
) -> dict[str, int]:
    return {
        name: max(0, -inventory.get(name, 0))
        for name in sorted(pool.eligible)
        if inventory.get(name, 0) < 0
    }


def parse_attributes(
    effects: list[str] | tuple[str, ...], *, assume_percent: bool = False
) -> tuple[tuple[str, float], ...]:
    result = []
    for text in effects:
        normalized = re.sub(r"\s+", "", str(text))
        match = re.fullmatch(r"(.+?)\+?(\d+(?:\.\d+)?)(%)?", normalized)
        if match:
            base_name = match.group(1)
            is_percent = bool(match.group(3)) or (assume_percent and base_name != "命轮值")
            value = float(match.group(2))
            # The source has one obvious `125%` typo beside other 1.25% rows.
            if assume_percent and is_percent and value > 10:
                value /= 100
            result.append((base_name + ("%" if is_percent else ""), value))
    return tuple(result)


def load_inventory(
    path: Path | None,
) -> tuple[dict[str, int], dict[str, str], dict[str, int], dict[str, int]]:
    source = path or ROOT / "references" / "avatar-map.json"
    data = json.loads(source.read_text())
    if "portraits" in data:
        inventory = {
            QM.canonical_name(item["canonical_name"]): int(item["snapshot_count"])
            for item in data["portraits"]
        }
        rarity = {
            QM.canonical_name(item["canonical_name"]): item["rarity"]
            for item in data["portraits"]
        }
        role_fragments = {
            QM.canonical_name(k): int(v)
            for k, v in data.get("role_fragments", {}).items()
        }
        role_full_costs = {
            QM.canonical_name(k): int(v)
            for k, v in data.get("role_fragment_full_costs", {}).items()
        }
        return inventory, rarity, role_fragments, role_full_costs
    inventory = {QM.canonical_name(k): int(v) for k, v in data["inventory"].items()}
    rarity = {QM.canonical_name(k): v for k, v in data.get("rarity", {}).items()}
    role_fragments = {
        QM.canonical_name(k): int(v) for k, v in data.get("role_fragments", {}).items()
    }
    role_full_costs = {
        QM.canonical_name(k): int(v)
        for k, v in data.get("role_fragment_full_costs", {}).items()
    }
    return inventory, rarity, role_fragments, role_full_costs


def merged_rarity(explicit: dict[str, str]) -> dict[str, str]:
    """Merge custom inventory rarity with the maintained portrait catalog."""
    catalog = json.loads((ROOT / "references" / "avatar-map.json").read_text())
    result = {
        QM.canonical_name(item["canonical_name"]): item["rarity"]
        for item in catalog.get("portraits", [])
    }
    result.update(explicit)
    return result


def advance_requirements(
    members: tuple[str, ...], rarity: dict[str, str], limited: set[str],
    role_full_costs: dict[str, int],
) -> tuple[tuple[tuple[str, int], ...], bool]:
    """Return full-advance role costs and whether a blocker lacks a known cost."""
    costs = []
    unmodeled = False
    for name in members:
        if name in limited:
            # 限定SSR完整升满默认 30/次×5=150；绘梨衣例外 15/次×5=75（2026-09-03 用户实报）
            costs.append((name, role_full_costs.get(name, 75 if name == "绘梨衣" else 150)))
        elif rarity.get(name) == "UR":
            if name in role_full_costs:
                costs.append((name, role_full_costs[name]))
            else:
                unmodeled = True
    return tuple(costs), unmodeled


def load_candidates(workbook: Path, inventory: dict[str, int], rarity: dict[str, str],
                    elements: set[str] | None, include_supplemental: bool,
                    selectable: SelectablePool, role_inventory: dict[str, int],
                    role_full_costs: dict[str, int]) -> list[Candidate]:
    wb = QM.load(workbook)
    rows: list[dict[str, Any]] = QM.all_combos(wb)
    if include_supplemental:
        extra = json.loads((ROOT / "references" / "supplemental-combos.json").read_text())
        rows.extend(extra["combos"])
    pool = json.loads((ROOT / "references" / "ssr-pool.json").read_text())
    limited = {QM.canonical_name(name) for name in pool["限定SSR"]}
    candidates = []
    seen = set()
    for row in rows:
        if elements and row["element"] not in elements:
            continue
        key = (row["element"], row["wheel"], row["name"])
        if key in seen:
            continue
        seen.add(key)
        members = tuple(QM.canonical_name(name) for name in row["members"])
        role_costs, unmodeled = advance_requirements(
            members, rarity, limited, role_full_costs
        )
        blocked = bool(role_costs) or unmodeled
        max_cycles = 1 if unmodeled else 5  # 白→绿→蓝→紫→橙→彩共5轮（2026-09-03 用户更正）
        stars = int(row["stars"])
        if not members:
            continue
        effects = tuple(row.get("effects", ()))
        advance_effects = tuple(row.get("advance_effects", ()))
        candidate = Candidate(
            element=row["element"], wheel=row["wheel"], name=row["name"],
            stars=stars, members=members, effects=effects, advance_effects=advance_effects,
            attributes=parse_attributes(effects), source=row.get("source", ""),
            advance_attributes=parse_attributes(advance_effects, assume_percent=True),
            missing_fields=tuple(row.get("missing_fields", ())),
            max_cycles=max_cycles, advance_blocked=blocked,
            role_costs_for_full_advance=role_costs,
            unmodeled_advance_blocker=unmodeled,
        )
        if max_feasible(candidate, inventory, role_inventory, selectable) > 0:
            candidates.append(candidate)
    return candidates


def derive_selectable_characters(
    workbook: Path, elements: set[str] | None, minimum_full_demand: int,
    rarity: dict[str, str], role_inventory: dict[str, int],
    role_full_costs: dict[str, int],
) -> tuple[SelectablePool, dict[str, int]]:
    """Find common SSRs whose real reachable demand exceeds a floor.

    Pure common/SR/R combinations count four cycles.  A combination containing
    limited SSR or UR counts one cycle by default.  Its other three cycles count
    only when the explicitly supplied role-fragment stock can fully advance it.
    The workbook has only full four-advance costs, so partial extra tiers are not
    inferred.
    """
    wb = QM.load(workbook)
    pool_data = json.loads((ROOT / "references" / "ssr-pool.json").read_text())
    common = {QM.canonical_name(name) for name in pool_data["常驻SSR"]}
    limited = {QM.canonical_name(name) for name in pool_data["限定SSR"]}
    real_demand = Counter()
    upgrade_options: list[tuple[frozenset[str], int, tuple[tuple[str, int], ...]]] = []
    for row in QM.all_combos(wb):
        if elements and row["element"] not in elements:
            continue
        members = tuple(QM.canonical_name(member) for member in row["members"])
        role_costs, unmodeled = advance_requirements(
            members, rarity, limited, role_full_costs
        )
        blocked = bool(role_costs) or unmodeled
        stars = int(row["stars"])
        base_cycles = 1 if blocked else 5
        common_members = frozenset(name for name in members if name in common)
        for member in row["members"]:
            name = QM.canonical_name(member)
            if name in common:
                real_demand[name] += stars * base_cycles
        if blocked and not unmodeled and common_members:
            upgrade_options.append((common_members, stars * 4, role_costs))  # 首轮之外的 4 轮

    for target in common:
        relevant = [option for option in upgrade_options if target in option[0]]
        if not relevant:
            continue
        role_names = sorted({name for _, _, costs in relevant for name, _ in costs})
        start = tuple(role_inventory.get(name, 0) for name in role_names)

        @functools.lru_cache(maxsize=None)
        def best_extra(index: int, remaining: tuple[int, ...]) -> int:
            if index == len(relevant):
                return 0
            best = best_extra(index + 1, remaining)
            _, gain, costs = relevant[index]
            needed = dict(costs)
            if all(remaining[i] >= needed.get(name, 0) for i, name in enumerate(role_names)):
                after = tuple(
                    remaining[i] - needed.get(name, 0)
                    for i, name in enumerate(role_names)
                )
                best = max(best, gain + best_extra(index + 1, after))
            return best

        real_demand[target] += best_extra(0, start)
    eligible_demand = {
        name: real_demand[name]
        for name in sorted(real_demand)
        if real_demand[name] > minimum_full_demand
    }
    return SelectablePool(eligible=frozenset(eligible_demand)), eligible_demand


def add_tuple(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(a + b for a, b in zip(left, right))


def option_vector(c: Candidate, cycles: int, objective: tuple[str, ...]) -> tuple[float, ...]:
    attrs = dict(c.attributes)
    advance_attrs = dict(c.advance_attributes)
    consumed = len(c.members) * c.stars * cycles
    advancements = (
        0 if c.advance_blocked and cycles == 1
        else cycles
    )
    return tuple(
        consumed if name == "消耗"
        else attrs.get(name, 0) * c.stars * cycles
        + advance_attrs.get(name, 0) * advancements
        for name in objective
    )


def is_feasible(
    c: Candidate, cycles: int, inventory: dict[str, int],
    role_inventory: dict[str, int], selectable: SelectablePool
) -> bool:
    if cycles < 0 or cycles > c.max_cycles:
        return False
    if c.advance_blocked and cycles not in {0, 1, 5}:
        return False
    if cycles == 5:
        if c.unmodeled_advance_blocker:
            return False
        if any(
            role_inventory.get(name, 0) < cost
            for name, cost in c.role_costs_for_full_advance
        ):
            return False
    cost = c.stars * cycles
    allow_selectable = not (c.advance_blocked and cycles == 1)
    for member in c.members:
        if (member not in selectable.eligible or not allow_selectable) and inventory.get(member, 0) < cost:
            return False
    if not selectable.eligible or not allow_selectable:
        return True
    used_after = selectable_used(inventory, selectable)
    for member in c.members:
        if member not in selectable.eligible:
            continue
        before = max(0, -inventory.get(member, 0))
        after = max(0, -(inventory.get(member, 0) - cost))
        used_after += after - before
    return used_after <= selectable.total


def max_feasible(
    c: Candidate, inventory: dict[str, int], role_inventory: dict[str, int],
    selectable: SelectablePool,
) -> int:
    for cycles in feasible_cycle_values(c):
        if is_feasible(c, cycles, inventory, role_inventory, selectable):
            return cycles
    return 0


def feasible_cycle_values(c: Candidate) -> tuple[int, ...]:
    if c.advance_blocked:
        return (5, 1, 0) if c.max_cycles == 5 else (1, 0)
    return tuple(range(c.max_cycles, -1, -1))


def feasible_cycles(
    c: Candidate, inventory: dict[str, int], role_inventory: dict[str, int],
    selectable: SelectablePool,
) -> list[int]:
    return [
        cycles for cycles in feasible_cycle_values(c)
        if is_feasible(c, cycles, inventory, role_inventory, selectable)
    ]


def apply_cycles(
    c: Candidate, cycles: int, inventory: dict[str, int],
    role_inventory: dict[str, int] | None = None,
) -> None:
    for member in c.members:
        inventory[member] = inventory.get(member, 0) - c.stars * cycles
    if role_inventory is not None and abs(cycles) == 5 and c.advance_blocked:
        direction = 1 if cycles > 0 else -1
        for name, cost in c.role_costs_for_full_advance:
            role_inventory[name] = role_inventory.get(name, 0) - cost * direction


def choice_score(candidates: list[Candidate], choice: list[int],
                 objective: tuple[str, ...]) -> tuple[float, ...]:
    score = tuple(0.0 for _ in objective)
    for candidate, cycles in zip(candidates, choice):
        score = add_tuple(score, option_vector(candidate, cycles, objective))
    return score


def heuristic(candidates: list[Candidate], inventory: dict[str, int],
              role_inventory: dict[str, int], objective: tuple[str, ...],
              trials: int, seed: int, selectable: SelectablePool) -> tuple[tuple[int, ...], list[int]]:
    rng = random.Random(seed)
    best_score = tuple(0 for _ in objective)
    best_choice = [0] * len(candidates)
    base_order = list(range(len(candidates)))
    for trial in range(max(1, trials)):
        remaining = dict(inventory)
        role_remaining = dict(role_inventory)
        choice = [0] * len(candidates)
        if trial == 0:
            order = sorted(base_order, key=lambda i: option_vector(candidates[i], 1, objective), reverse=True)
        else:
            order = sorted(
                base_order,
                key=lambda i: (option_vector(candidates[i], 1, objective), rng.random()),
                reverse=True,
            )
            # Perturb equal and near-equal priorities without losing deterministic reproducibility.
            for start in range(0, len(order), 7):
                block = order[start:start + 7]
                rng.shuffle(block)
                order[start:start + 7] = block
        for i in order:
            options = [
                value for value in feasible_cycles(
                    candidates[i], remaining, role_remaining, selectable
                ) if value > 0
            ]
            if not options:
                continue
            take = options[0] if trial == 0 or rng.random() < 0.8 else rng.choice([0, *options])
            choice[i] = take
            apply_cycles(candidates[i], take, remaining, role_remaining)
        score = tuple(0 for _ in objective)
        for i, cycles in enumerate(choice):
            score = add_tuple(score, option_vector(candidates[i], cycles, objective))
        if score > best_score:
            best_score, best_choice = score, choice
    return best_score, best_choice


def solve(candidates: list[Candidate], inventory: dict[str, int],
          role_inventory: dict[str, int], objective: tuple[str, ...],
          solver: str, time_limit: float, trials: int, seed: int,
          selectable: SelectablePool, warm_primary: bool = True):
    # Rarest-resource candidates first makes infeasibility and bounds bite earlier.
    candidates.sort(
        key=lambda c: (
            option_vector(c, 1, objective),
            -min(
                (inventory.get(m, 0) + (selectable.total if m in selectable.eligible else 0))
                / c.stars
                for m in c.members
            ),
            len(c.members),
        ),
        reverse=True,
    )
    best_score, best_choice = heuristic(
        candidates, inventory, role_inventory, objective, trials, seed, selectable
    )
    # In all-element runs, first solve the positive-primary subset exactly when
    # practical, then greedily spend the remaining stock on zero-primary rows.
    # This guarantees a strong incumbent does not sacrifice the main element
    # merely because the full 197-row search times out early.
    primary = [c for c in candidates if option_vector(c, 1, objective)[0] > 0]
    if solver != "greedy" and warm_primary and primary and len(primary) < len(candidates):
        _, primary_choice, _, _, _ = solve(
            primary, inventory, role_inventory, objective, "hybrid", time_limit, trials, seed,
            selectable, warm_primary=False,
        )
        primary_by_key = {c.key: cycles for c, cycles in zip(primary, primary_choice)}
        combined = [primary_by_key.get(c.key, 0) for c in candidates]
        remaining = dict(inventory)
        role_remaining = dict(role_inventory)
        for c, cycles in zip(candidates, combined):
            if cycles:
                apply_cycles(c, cycles, remaining, role_remaining)
        secondary_indexes = [
            i for i, c in enumerate(candidates) if option_vector(c, 1, objective)[0] == 0
        ]
        secondary = [candidates[i] for i in secondary_indexes]
        _, secondary_choice = heuristic(
            secondary, remaining, role_remaining, objective, trials, seed + 1, selectable
        )
        for index, cycles in zip(secondary_indexes, secondary_choice):
            combined[index] = cycles
        combined_score = choice_score(candidates, combined, objective)
        if combined_score > best_score:
            best_score, best_choice = combined_score, combined
    if solver == "greedy":
        return best_score, best_choice, "heuristic", 0, False

    size = len(candidates)
    suffix = [tuple(0 for _ in objective) for _ in range(size + 1)]
    for i in range(size - 1, -1, -1):
        optimistic = option_vector(candidates[i], candidates[i].max_cycles, objective)
        suffix[i] = add_tuple(suffix[i + 1], optimistic)

    deadline = time.monotonic() + time_limit
    choice = [0] * size
    nodes = 0
    timed_out = False
    memo: dict[tuple[int, tuple[int, ...], tuple[int, ...]], tuple[int, ...]] = {}
    names = sorted({member for c in candidates for member in c.members})
    name_index = {name: i for i, name in enumerate(names)}
    role_names = sorted(
        {name for c in candidates for name, _ in c.role_costs_for_full_advance}
    )
    # Inventory beyond the maximum amount the suffix can consume is equivalent.
    # Capping it before memoization merges many otherwise identical states.
    suffix_demand = [[0] * len(names) for _ in range(size + 1)]
    for i in range(size - 1, -1, -1):
        suffix_demand[i] = suffix_demand[i + 1].copy()
        c = candidates[i]
        for member in c.members:
            suffix_demand[i][name_index[member]] += c.stars * c.max_cycles

    def inventory_bound(index: int, remaining: dict[str, int],
                        role_remaining: dict[str, int],
                        score: tuple[int, ...]) -> tuple[int, ...]:
        """Admissible component-wise bound that respects current stock per option.

        It still lets every combination spend the same stock independently, so it
        can overestimate but can never hide a better feasible solution.
        """
        bound = list(score)
        for j in range(index, size):
            c = candidates[j]
            cycles = max_feasible(c, remaining, role_remaining, selectable)
            if cycles:
                gain = option_vector(c, cycles, objective)
                for k, value in enumerate(gain):
                    bound[k] += value
        return tuple(bound)

    def dfs(index: int, remaining: dict[str, int], role_remaining: dict[str, int],
            score: tuple[int, ...]) -> None:
        nonlocal best_score, best_choice, nodes, timed_out
        nodes += 1
        if nodes % 2048 == 0 and time.monotonic() >= deadline:
            timed_out = True
            return
        if score > best_score:
            best_score, best_choice = score, choice.copy()
        if index == size or add_tuple(score, suffix[index]) <= best_score:
            return
        if inventory_bound(index, remaining, role_remaining, score) <= best_score:
            return
        state = (
            index,
            tuple(
                min(remaining.get(name, 0), suffix_demand[index][j])
                for j, name in enumerate(names)
            ),
            tuple(role_remaining.get(name, 0) for name in role_names),
        )
        previous = memo.get(state)
        if previous is not None and previous >= score:
            return
        memo[state] = score
        c = candidates[index]
        options = feasible_cycles(c, remaining, role_remaining, selectable)
        options.sort(key=lambda n: option_vector(c, n, objective), reverse=True)
        for cycles in options:
            if timed_out:
                return
            choice[index] = cycles
            if cycles:
                apply_cycles(c, cycles, remaining, role_remaining)
            dfs(
                index + 1, remaining, role_remaining,
                add_tuple(score, option_vector(c, cycles, objective)),
            )
            if cycles:
                apply_cycles(c, -cycles, remaining, role_remaining)
        choice[index] = 0

    dfs(0, dict(inventory), dict(role_inventory), tuple(0 for _ in objective))
    return best_score, best_choice, ("best-found" if timed_out else "optimal"), nodes, timed_out


def summarize(candidates: list[Candidate], choice: list[int], inventory: dict[str, int],
              role_inventory: dict[str, int]):
    remaining = dict(inventory)
    role_remaining = dict(role_inventory)
    attrs = Counter()
    rows = []
    for c, cycles in zip(candidates, choice):
        if cycles <= 0:
            continue
        apply_cycles(c, cycles, remaining, role_remaining)
        for name, value in c.attributes:
            attrs[name] += value * c.stars * cycles
        if not c.advance_blocked or cycles == 5:
            for name, value in c.advance_attributes:
                attrs[name] += value * cycles
        if c.advance_blocked and cycles == 1:
            final_state = f"白{c.stars}星（限定/UR组合仅点一层）"
        else:
            final_state = f"{TIERS[cycles]}0星"
        role_consumed = (
            dict(c.role_costs_for_full_advance)
            if c.advance_blocked and cycles == 5 else {}
        )
        rows.append({
            "element": c.element, "wheel": c.wheel, "name": c.name,
            "target_stars": c.stars, "cycles": cycles, "final_state": final_state,
            "members": list(c.members), "effects_per_cycle": list(c.effects),
            "advance_effects_per_advance": list(c.advance_effects),
            "role_fragments_consumed": role_consumed,
            "source": c.source, "missing_fields": list(c.missing_fields),
        })
    return rows, attrs, remaining, role_remaining


def main() -> int:
    parser = argparse.ArgumentParser(description="命轮库存组合优化器（贪婪种子 + 分支限界）")
    parser.add_argument("--workbook", type=Path, default=ROOT / "references" / "minglun-source.xlsx")
    parser.add_argument("--inventory", type=Path, help="avatar-map.json 或含 inventory 字段的 JSON")
    parser.add_argument("--elements", default="全部", help="全部，或逗号分隔：精神,火")
    parser.add_argument("--objective", default="精神元素,精神元素%,消耗,攻击,攻击%,命轮值",
                        help="字典序目标，支持：精神元素,精神元素%%,消耗,攻击,攻击%%,命轮值")
    parser.add_argument("--solver", choices=("hybrid", "greedy"), default="hybrid")
    parser.add_argument("--time-limit", type=float, default=20.0)
    parser.add_argument("--trials", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260903)
    parser.add_argument(
        "--selectable-fragments", type=int, default=0,
        help="可在合资格常驻SSR之间自由分配的命轮自选碎片数量",
    )
    parser.add_argument(
        "--selectable-min-demand", type=int, default=80,
        help="常驻SSR在所选元素全盘彩0需求严格超过此数时才可使用自选（默认80）",
    )
    parser.add_argument("--no-supplemental", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    objective = tuple(x.strip() for x in args.objective.split(",") if x.strip())
    unknown = set(objective) - OBJECTIVE_NAMES
    if unknown:
        raise SystemExit(f"未知目标：{sorted(unknown)}")
    if args.selectable_fragments < 0:
        raise SystemExit("--selectable-fragments 不能为负数")
    if args.selectable_min_demand < 0:
        raise SystemExit("--selectable-min-demand 不能为负数")
    elements = None if args.elements == "全部" else {x.strip().replace("元素", "") for x in args.elements.split(",")}
    inventory, rarity, role_inventory, role_full_costs = load_inventory(args.inventory)
    rarity = merged_rarity(rarity)
    eligible_demand: dict[str, int] = {}
    selectable = SelectablePool()
    if args.selectable_fragments:
        derived, eligible_demand = derive_selectable_characters(
            args.workbook, elements, args.selectable_min_demand, rarity,
            role_inventory, role_full_costs,
        )
        selectable = SelectablePool(
            total=args.selectable_fragments, eligible=derived.eligible
        )
    candidates = load_candidates(
        args.workbook, inventory, rarity, elements, not args.no_supplemental,
        selectable, role_inventory, role_full_costs,
    )
    score, choice, status, nodes, timed_out = solve(
        candidates, inventory, role_inventory, objective, args.solver,
        args.time_limit, args.trials, args.seed, selectable,
    )
    rows, attrs, remaining, role_remaining = summarize(
        candidates, choice, inventory, role_inventory
    )
    allocation = selectable_allocation(remaining, selectable)
    selectable_used_count = sum(allocation.values())
    result = {
        "status": status,
        "optimal_certified": status == "optimal",
        "nodes": nodes,
        "objective": list(objective),
        "objective_score": list(score),
        "assumption": "所有组合白0星；C:E基础效果按每升1星计，K:M按每次真实升阶计；常驻SSR/SR/R角色碎片无限；品阶为白→绿→蓝→紫→橙→彩共5次进阶；限定SSR逐次成本30（绘梨衣15），含限定SSR/UR的组合默认只计一层，只有显式角色碎片库存可支持的进阶才计后续层；命轮自选不补默认一层的伪需求",
        "selected_combinations": len(rows),
        "completed_star_cycles": sum(row["cycles"] for row in rows),
        "attributes": dict(attrs),
        "plan": rows,
        "remaining_inventory": {name: count for name, count in sorted(remaining.items()) if count > 0},
        "remaining_total": sum(count for count in remaining.values() if count > 0),
        "role_fragments": {
            "initial": role_inventory,
            "used": {
                name: role_inventory.get(name, 0) - role_remaining.get(name, 0)
                for name in sorted(role_inventory)
                if role_inventory.get(name, 0) != role_remaining.get(name, 0)
            },
            "remaining": {
                name: count for name, count in sorted(role_remaining.items()) if count > 0
            },
        },
        "selectable_fragments": {
            "available": selectable.total,
            "eligibility": f"所选元素真实可达命轮需求严格大于 {args.selectable_min_demand} 的常驻SSR；含限定SSR/UR组合默认仅计一层",
            "eligible_characters": eligible_demand,
            "allocation": allocation,
            "used": selectable_used_count,
            "remaining": selectable.total - selectable_used_count,
        },
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"求解状态：{status}；节点：{nodes}；最优性证明：{'是' if result['optimal_certified'] else '否'}")
    print("目标：" + " > ".join(objective) + "；得分：" + ", ".join(map(str, score)))
    print(f"组合：{len(rows)}；升星轮次：{result['completed_star_cycles']}；剩余碎片：{result['remaining_total']}")
    if selectable.total:
        eligible_text = "、".join(
            f"{name}({demand})" for name, demand in eligible_demand.items()
        ) or "无"
        allocation_text = "、".join(
            f"{name}{count}" for name, count in allocation.items()
        ) or "未使用"
        print(
            f"命轮自选：可用{selectable.total}，已用{selectable_used_count}，"
            f"剩余{selectable.total - selectable_used_count}；合资格：{eligible_text}；"
            f"分配：{allocation_text}"
        )
    for row in rows:
        missing = f"；缺失字段：{'、'.join(row['missing_fields'])}" if row["missing_fields"] else ""
        print(f"- {row['element']}/{row['wheel']}/{row['name']}：{row['cycles']}轮 → {row['final_state']}{missing}")
    print("属性：" + "；".join(f"{k}+{v}" for k, v in attrs.items()))
    print("剩余：" + "；".join(f"{k}{v}" for k, v in result["remaining_inventory"].items()))
    if timed_out:
        print("提示：达到时间上限，当前结果是可行下界，不是已证明的全局最优解。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
