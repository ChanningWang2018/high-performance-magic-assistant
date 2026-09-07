"""Ticket #11 Action 调度表达与双端对齐：TDD 测试.

只断言外部可观测行为：
- Action 携带类型 / 优先级 / 取消标记（默认值不改变现有构造）
- 现有 14 个 Action 子类字段名零改名、旧构造方式不变
- schedule_actions：过滤已取消 + 按优先级降序稳定排序
- Runner 分发跳过已取消（无 UI 调用、无状态变化）
- SimEnv 支持按 Action 序列直调（同种子同序列结果一致）
- 同一 Action 序列双端回放终局分数一致（#8 终局口径为 oracle）
"""

import asyncio

import pytest

from hawarma.core.actions import (
    Action,
    AddCondimentAction,
    AddCondimentToMixingBowlAction,
    ClearAssemblyAction,
    ClearCookerAction,
    ClearMixingBowlAction,
    CookAction,
    MoveMixingBowlToCookerAction,
    MoveToAssemblyAction,
    MoveToMixingBowlAction,
    MoveToStockpileAction,
    PullFromStockpileAction,
    ServeFromCookerAction,
    ServeOrderAction,
    StirAction,
    schedule_actions,
)
from hawarma.game.runner import Runner
from playground.env.sim import SimEnv

RECIPE = "braisedNewYearFish"


# ============================================================================
# Action 调度表达：类型 / 优先级 / 取消标记
# ============================================================================


class TestActionSchedulingFields:
    def test_defaults_keep_existing_construction(self):
        a = CookAction(ingredient="clearwater_fish", cooker="skillet", duration=4.0)
        assert a.ingredient == "clearwater_fish"
        assert a.cooker == "skillet"
        assert a.duration == pytest.approx(4.0)
        assert a.action_type == "CookAction"
        assert a.priority == 0
        assert a.cancelled is False

    def test_positional_construction_still_works(self):
        a = CookAction("clearwater_fish", "skillet", 4.0)
        assert (a.ingredient, a.cooker) == ("clearwater_fish", "skillet")
        s = ServeOrderAction(2)
        assert s.slot_idx == 2
        assert s.action_type == "ServeOrderAction"

    def test_all_14_subclasses_keep_field_names(self):
        cases: list[tuple[Action, str]] = [
            (ClearCookerAction(cooker="skillet"), "ClearCookerAction"),
            (AddCondimentAction(condiment="hearthspice"), "AddCondimentAction"),
            (ClearAssemblyAction(), "ClearAssemblyAction"),
            (CookAction("clearwater_fish", "skillet", 4.0), "CookAction"),
            (MoveToAssemblyAction(cooker="skillet"), "MoveToAssemblyAction"),
            (MoveToStockpileAction(cooker="skillet", slot="slot0"), "MoveToStockpileAction"),
            (PullFromStockpileAction(slot="slot0", ingredient="clearwater_fish"), "PullFromStockpileAction"),
            (ServeOrderAction(slot_idx=0), "ServeOrderAction"),
            (MoveToMixingBowlAction(ingredient="x"), "MoveToMixingBowlAction"),
            (AddCondimentToMixingBowlAction(condiment="y"), "AddCondimentToMixingBowlAction"),
            (StirAction(), "StirAction"),
            (MoveMixingBowlToCookerAction(cooker="oven"), "MoveMixingBowlToCookerAction"),
            (ServeFromCookerAction(cooker="oven", slot_idx=1), "ServeFromCookerAction"),
            (ClearMixingBowlAction(), "ClearMixingBowlAction"),
        ]
        for action, name in cases:
            assert action.action_type == name
            assert action.priority == 0
            assert action.cancelled is False


# ============================================================================
# schedule_actions：过滤已取消 + 优先级降序稳定排序
# ============================================================================


class TestScheduleActions:
    def test_cancelled_filtered(self):
        seq = [
            ServeOrderAction(slot_idx=0),
            ServeOrderAction(slot_idx=1, cancelled=True),
        ]
        scheduled = schedule_actions(seq)
        assert [a.slot_idx for a in scheduled] == [0]

    def test_priority_descending_stable(self):
        low = CookAction("clearwater_fish", "skillet", 4.0, priority=1)
        high = ServeOrderAction(slot_idx=0, priority=5)
        mid_a = ClearCookerAction(cooker="skillet", priority=3)
        mid_b = ClearAssemblyAction(priority=3)
        seq = [low, mid_a, high, mid_b]
        scheduled = schedule_actions(seq)
        assert scheduled[0] is high
        assert scheduled[1] is mid_a
        assert scheduled[2] is mid_b
        assert scheduled[3] is low

    def test_input_order_untouched(self):
        seq = [
            ServeOrderAction(slot_idx=0, priority=9),
            ServeOrderAction(slot_idx=1, priority=1),
        ]
        schedule_actions(seq)
        assert [a.slot_idx for a in seq] == [0, 1]


# ============================================================================
# Runner 分发：跳过已取消
# ============================================================================


class _StubEnv:
    def __init__(self) -> None:
        self.time = 0.0
        self.started: list[tuple[str, str, float]] = []

    def is_game_over(self) -> bool:
        return False

    def start_cooking(self, ingredient: str, cooker: str, duration: float) -> bool:
        self.started.append((ingredient, cooker, duration))
        return True


class _StubUI:
    def __init__(self) -> None:
        self.cook_calls: list[tuple[str, str]] = []

    async def cook(self, ingredient: str, cooker: str) -> None:
        self.cook_calls.append((ingredient, cooker))


def _make_runner() -> tuple[object, _StubEnv, _StubUI]:
    runner = Runner.__new__(Runner)
    runner.env = _StubEnv()
    runner.ui = _StubUI()
    Runner._build_action_handlers(runner)
    return runner, runner.env, runner.ui


class TestRunnerDispatch:
    def test_cancelled_action_skipped_without_ui_call(self):
        runner, env, ui = _make_runner()
        seq = [
            CookAction("clearwater_fish", "skillet", 4.0, cancelled=True),
        ]
        scheduled = asyncio.run(Runner.dispatch_batch(runner, seq))
        assert scheduled == []
        assert ui.cook_calls == []
        assert env.started == []

    def test_batch_executes_in_scheduled_order(self):
        runner, env, ui = _make_runner()
        seq = [
            CookAction("clearwater_fish", "skillet", 4.0, priority=1),
            CookAction("creamfield_rice", "pot", 2.0, priority=9),
        ]
        scheduled = asyncio.run(Runner.dispatch_batch(runner, seq))
        assert [a.ingredient for a in scheduled] == ["creamfield_rice", "clearwater_fish"]
        assert [c[0] for c in ui.cook_calls] == ["creamfield_rice", "clearwater_fish"]


# ============================================================================
# SimEnv：按 Action 序列直调 + 确定性回放
# ============================================================================

SLUGS = ["braisedNewYearFish", "gildedShoreRisotto", "farmsteadFeastRoast", "newYearJiaozi"]


def _fresh_sim(seed: int = 42):
    env = SimEnv()
    env.reset(seed=seed, recipe_slugs=SLUGS)
    return env


class TestSimActionSequence:
    def test_direct_sequence_executes_and_skips_cancelled(self):
        env = _fresh_sim()
        seq = [
            CookAction("clearwater_fish", "skillet", 4.0),
            CookAction("creamfield_rice", "pot", 2.0, cancelled=True),
        ]
        results = env.run_action_sequence(seq)
        assert len(results) == 1
        assert results[0].info["action_success"] is True
        assert env.get_stats()["actions_taken"] == 1

    def test_same_seed_same_sequence_same_stats(self):
        seq = [CookAction("clearwater_fish", "skillet", 4.0, priority=2)]

        env1 = _fresh_sim(seed=7)
        env1.run_action_sequence(seq)
        stats1 = env1.get_stats()
        final1 = env1.finalize()

        env2 = _fresh_sim(seed=7)
        env2.run_action_sequence(seq)
        stats2 = env2.get_stats()
        final2 = env2.finalize()

        assert stats1["actions_taken"] == stats2["actions_taken"] == 1
        assert final1 == pytest.approx(final2)


# ============================================================================
# 双端回放：同一 dispatched 送餐序列终局分数一致
# ============================================================================


class TestDualEndReplay:
    def test_same_dispatched_serves_same_final_score(self):
        from hawarma.core.reward import RecipeRewardLookup
        from hawarma.game.game_env import GameEnv

        seq = [
            ServeOrderAction(slot_idx=0, priority=1),
            ServeOrderAction(slot_idx=1, priority=5, cancelled=True),
            ServeOrderAction(slot_idx=2, priority=3),
        ]
        scheduled = schedule_actions(seq)
        assert len(scheduled) == 2  # 已取消的不参与回放计分

        lookup = RecipeRewardLookup()

        real = GameEnv(cooker_names=["skillet", "pot"], stockpile_slots=3)
        for _ in scheduled:
            real.add_order(RECIPE, is_rush=False)
            order = real.orders[0]
            assert order is not None
            real.on_order_served(order, has_condiments=False)
        real_final = real.finalize()

        sim = SimEnv()
        sim.reset(seed=11, recipe_slugs=SLUGS)
        total_vis = 0.0
        for _ in scheduled:
            score = lookup.get_score(RECIPE, False, False, total_visibility=total_vis)
            vis = lookup.get_visibility(RECIPE, False)
            sim.on_order_served(score, vis)
            total_vis += vis
        sim_final = sim.finalize()

        assert real_final == pytest.approx(sim_final)
        assert real.get_stats()["orders_served"] == 2
        assert sim.get_stats()["orders_served"] == 2

    def test_same_cook_sequence_same_execution_both_ends(self):
        """同一烹饪序列经两端分发入口执行顺序与灶台状态一致."""
        seq = [
            CookAction("clearwater_fish", "skillet", 4.0, priority=1),
            CookAction("creamfield_rice", "pot", 2.0, priority=9),
            CookAction("clearwater_fish", "skillet", 4.0, priority=5, cancelled=True),
        ]

        runner, _env, ui = _make_runner()
        scheduled = asyncio.run(Runner.dispatch_batch(runner, seq))
        runner_done = [(c, i) for i, c in ui.cook_calls]

        sim = _fresh_sim(seed=11)
        results = sim.run_action_sequence(seq)
        sim_cookers = sim.get_unified_state().cookers
        sim_busy = {
            (name, c.item_name) for name, c in sim_cookers.items() if c.busy
        }

        assert [type(a).__name__ for a in scheduled] == ["CookAction", "CookAction"]
        assert all(r.info["action_success"] for r in results)
        # 两端执行顺序一致（高优先级先），已取消的不执行
        assert runner_done == [("pot", "creamfield_rice"), ("skillet", "clearwater_fish")]
        assert sim_busy == set(runner_done)
