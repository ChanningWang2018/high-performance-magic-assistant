"""Ticket #9 执行耗时观测与 SrvGap/Idle 分布基线测试.

只断言外部可观测行为，不锁定内部字段名（字段名以公开 to_dict 键为准）：
- 分段耗时聚合（count/total/avg/max）的存在性与合理性
- 动画窗口剩余时长可观测（正值 / 窗口外为 0）
- SrvGap / Idle 分布直方图：分桶总数守恒、空序列退化、单一值退化
- 送餐验证路径分段键齐全（验证等待 / 截图 / 重试 / 清理）
"""

import time

import pytest

from hawarma.game.game_env import GameEnv
from hawarma.game.timing import ExecutionTiming, SegmentTiming, Timer
from playground.bench.compare import histogram, print_distributions

RECIPE = "braisedNewYearFish"


# ============================================================================
# SegmentTiming / ExecutionTiming 聚合
# ============================================================================


class TestSegmentTiming:
    def test_record_aggregates_count_total_max(self):
        seg = SegmentTiming("x")
        seg.record(0.1)
        seg.record(0.3)
        seg.record(0.2)

        assert seg.count == 3
        assert seg.total == pytest.approx(0.6)
        assert seg.max == pytest.approx(0.3)
        assert seg.avg == pytest.approx(0.2)

    def test_empty_segment_avg_is_zero(self):
        seg = SegmentTiming("x")
        assert seg.avg == 0.0
        d = seg.to_dict()
        assert d["count"] == 0
        assert d["total"] == 0.0


class TestExecutionTiming:
    def test_to_dict_has_all_observable_segments(self):
        timing = ExecutionTiming()
        d = timing.to_dict()
        # 送餐验证各段 + 滑动往返都必须可观测
        assert set(d) == {
            "swipe",
            "serve_verify_wait",
            "serve_verify_snapshot",
            "serve_retry",
            "serve_cleanup",
        }
        for seg in d.values():
            assert "count" in seg and "avg" in seg

    def test_timer_records_positive_duration(self):
        timing = ExecutionTiming()
        t = Timer(timing.serve_verify_wait)
        time.sleep(0.01)
        dt = t.record()

        assert dt > 0
        assert timing.serve_verify_wait.count == 1

    def test_timer_record_is_idempotent(self):
        timing = ExecutionTiming()
        t = Timer(timing.serve_retry)
        first = t.record()
        second = t.record()

        assert first > 0
        assert second == 0.0
        assert timing.serve_retry.count == 1


# ============================================================================
# 动画窗口剩余时长可观测
# ============================================================================


class TestAnimationWindowRemaining:
    def test_positive_within_window(self):
        env = GameEnv(cooker_names=["fryer"], stockpile_slots=3)
        env.set_animation_window(1.5)
        remaining = env.animation_window_remaining()
        assert 0.0 < remaining <= 1.5
        assert env.is_in_animation_window() is True

    def test_zero_outside_window(self):
        env = GameEnv(cooker_names=["fryer"], stockpile_slots=3)
        env._animation_until = time.time() - 1.0
        assert env.animation_window_remaining() == 0.0
        assert env.is_in_animation_window() is False


# ============================================================================
# SrvGap / Idle 分布直方图（只观测，不卡阈值）
# ============================================================================


class TestHistogram:
    def test_counts_sum_to_total(self):
        values = [1.0, 2.0, 2.0, 3.0, 3.0, 3.0, 10.0]
        counts = histogram(values, bins=5)
        assert sum(counts) == len(values)
        assert all(c >= 0 for c in counts)

    def test_empty_returns_empty(self):
        assert histogram([]) == []

    def test_single_value_degrades(self):
        counts = histogram([5.0, 5.0, 5.0], bins=4)
        assert sum(counts) == 3

    def test_bins_honor_order(self):
        values = [0.0, 0.5, 1.0, 1.5, 2.0]
        counts = histogram(values, bins=2)
        assert sum(counts[:1]) > 0
        assert sum(counts[1:]) > 0


class TestPrintDistributions:
    def _make_episode(self, seed, serve_gap, idle_ratio):
        from playground.bench.metric import EfficiencyMetrics
        from playground.core.episode import EpisodeResult

        m = EfficiencyMetrics(
            cooker_idle_time={"fryer": 50.0},
            cooker_idle_ratio=idle_ratio,
            expired_ingredients=1,
            cleared_cooker_count=1,
            clear_assembly_count=0,
            stockpile_inserts=0,
            stockpile_pulls=0,
            stockpile_max_occupancy=0,
            avg_serve_interval=serve_gap,
            max_serve_interval=serve_gap * 2,
            none_ratio=0.8,
            total_steps=100,
            none_steps=80,
        )
        return EpisodeResult(
            total_reward=100.0,
            steps=100,
            actions_taken=20,
            orders_served=5,
            orders_timeout=0,
            final_time=90.0,
            seed=seed,
            strategy_name="gastronome",
            metrics=m,
        )

    def test_distributions_render_without_thresholds(self, capsys):
        results = {
            "gastronome": [self._make_episode(i, 4.0 + i * 0.5, 0.5 + i * 0.02) for i in range(30)]
        }
        print_distributions(results)
        out = capsys.readouterr().out

        assert "SrvGap" in out
        assert "Idle%" in out
        assert "30 games" in out
        # 直方图有柱状输出（计数），且不触发任何阈值断言/失败标记
        assert "#" in out

    def test_histogram_matches_srvgap_values(self):
        """同一样本序列的 SrvGap 直方图计数守恒且落在合理区间。"""
        vals = [round(4.0 + i * 0.5, 2) for i in range(30)]
        counts = histogram(vals, bins=10)
        assert sum(counts) == 30