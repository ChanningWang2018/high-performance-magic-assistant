"""Ticket #8 终局计分与度量抽离：TDD 失败测试（先行）.

只断言外部可观测行为：
- 终局分数 = 订单得分和 + 总 visibility（docs/game_rules.md:226）
- 终局结算幂等（重复查询不重复加总）
- 双端（真实 GameEnv / 模拟器）同序列终局一致
- 订单可见度快照以 Order 域字段为准
- 基准口径按 scoring_version 隔离
"""

import pytest

from hawarma.core.reward import RecipeRewardLookup

RECIPE = "braisedNewYearFish"


class TestFinalScoringContract:
    def test_real_env_final_score_includes_visibility_bonus(self):
        """终局 = 订单得分和 + 总 visibility."""
        from hawarma.game.game_env import GameEnv

        env = GameEnv(cooker_names=["fryer", "pot"], stockpile_slots=3)
        env.add_order(RECIPE, is_rush=False)
        order = env.orders[0]
        assert order is not None
        env.on_order_served(order, has_condiments=False)

        stats = env.get_stats()
        lookup = RecipeRewardLookup()
        per_order = lookup.get_score(RECIPE, False, False, total_visibility=0.0)
        vis = lookup.get_visibility(RECIPE, False)
        assert stats["total_score"] == pytest.approx(per_order)
        assert stats["total_visibility"] == pytest.approx(vis)
        assert env.finalize() == pytest.approx(per_order + vis)
        assert stats["final_score"] == pytest.approx(per_order + vis)

    def test_finalize_is_idempotent(self):
        """重复终局结算返回同一分数，不重复加总."""
        from hawarma.game.game_env import GameEnv

        env = GameEnv(cooker_names=["fryer", "pot"], stockpile_slots=3)
        env.add_order(RECIPE, is_rush=False)
        order = env.orders[0]
        assert order is not None
        env.on_order_served(order, has_condiments=False)

        first = env.finalize()
        second = env.finalize()
        third = env.get_stats()["final_score"]
        assert first == pytest.approx(second)
        assert second == pytest.approx(third)

    def test_sim_and_real_score_same_sequence(self):
        """同序列双端单订单得分一致（快照语义一致）."""
        from hawarma.game.game_env import GameEnv
        from playground.env_simulator import GameSimulator

        real = GameEnv(cooker_names=["fryer", "pot"], stockpile_slots=3)
        real.add_order(RECIPE, is_rush=False)
        real_order = real.orders[0]
        assert real_order is not None
        real.on_order_served(real_order, has_condiments=False)
        real_final = real.finalize()

        sim = GameSimulator()
        sim.load_recipes("data/recipes.json")
        slugs = sim.select_recipes(count=4, random_seed=1)
        assert RECIPE in sim._recipes or len(slugs) > 0
        recipe = sim._recipes.get(RECIPE, list(sim._recipes.values())[0])
        sim._state.total_visibility = 50.0
        sim.inject_order(0, recipe, is_rush=False)
        sim_order = sim._state.orders[0]
        assert sim_order is not None
        # 快照必须落在 Order 域字段上（弃用旁路字典为主语义）
        assert sim_order.spawned_at_visibility == pytest.approx(50.0)
        sim_score = sim._calculate_score(sim_order, {})
        sim_vis = sim._reward_lookup.get_visibility(recipe.slug, False) if sim._reward_lookup else 0
        # 用同一 recipe 在真实端复算对比（若 recipe 回退则跳过严格相等）
        if recipe.slug == RECIPE:
            lookup = RecipeRewardLookup()
            # 50 落在 [40, 80) → 非 rush 1.1×
            real_per_order = lookup.get_score(RECIPE, False, False, total_visibility=50.0)
            assert sim_score == pytest.approx(real_per_order)
            assert real_final == pytest.approx(
                lookup.get_score(RECIPE, False, False, total_visibility=0.0) + sim_vis
            )

    def test_snapshot_prefers_order_field_over_legacy_dict(self):
        """域字段优先：无字典条目时以 Order.spawned_at_visibility 为准."""
        from playground.env_simulator import GameSimulator
        from hawarma.core.models import Order

        sim = GameSimulator()
        sim.load_recipes("data/recipes.json")
        recipe = sim._recipes[RECIPE]
        sim._order_recipes[4242] = recipe
        order = Order(
            order_id=4242, recipe_slug=RECIPE, is_rush=False,
            created_at=0.0, timeout_at=70.0, spawned_at_visibility=200.0,
        )
        # 200 落在 [160, 240) → 非 rush 1.3×
        lookup = RecipeRewardLookup()
        assert sim._calculate_score(order, {}) == pytest.approx(
            lookup.get_score(RECIPE, False, False, total_visibility=200.0)
        )

    def test_episode_result_carries_scoring_version(self):
        """Episode 与基准统计携带 scoring_version，新旧口径隔离."""
        from playground.core.episode import EpisodeResult
        from hawarma.core.scoring import SCORING_VERSION

        r = EpisodeResult(
            total_reward=100.0, steps=10, actions_taken=5,
            orders_served=1, orders_timeout=0, final_time=90.0,
            seed=1, strategy_name="gastronome",
        )
        assert r.scoring_version == SCORING_VERSION
        assert r.final_score == pytest.approx(100.0 + r.total_visibility)

    def test_benchmark_csv_has_version_column(self):
        """基准 CSV 导出包含 scoring_version 列."""
        from playground.core.episode import EpisodeResult
        from playground.bench.compare import export_csv
        from hawarma.core.scoring import SCORING_VERSION

        r = EpisodeResult(
            total_reward=120.0, steps=10, actions_taken=5,
            orders_served=1, orders_timeout=0, final_time=90.0,
            seed=0, strategy_name="gastronome",
            total_visibility=14.0, final_score=134.0,
            scoring_version=SCORING_VERSION,
        )
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "out.csv")
            export_csv({"gastronome": [r]}, p)
            with open(p, encoding="utf-8") as f:
                header = f.readline()
            assert "scoring_version" in header
