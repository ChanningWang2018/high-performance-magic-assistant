"""
Playground Benchmark Comparison

策略统计对比，输出带显著性检验的结果。

输入: {strategy_name: [EpisodeResult, ...]}
输出: 打印表格 / 导出 CSV/JSON
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playground.core.episode import EpisodeResult

from hawarma.core.scoring import LEGACY_SCORING_VERSION


def _result_version(r: EpisodeResult) -> str:
    """口径版本回退（旧数据无该字段时视为 v1）."""
    return getattr(r, "scoring_version", LEGACY_SCORING_VERSION)

def _result_final(r: EpisodeResult) -> float:
    return getattr(r, "final_score", r.total_reward)

def _result_visibility(r: EpisodeResult) -> float:
    return getattr(r, "total_visibility", 0.0)


@dataclass
class StrategyStats:
    """单策略统计结果"""
    name: str
    games: int
    avg_reward: float
    std_reward: float
    avg_steps: float
    avg_actions: float
    max_reward: float
    min_reward: float
    scoring_version: str = LEGACY_SCORING_VERSION
    """计分口径版本（新旧基准隔离展示）"""
    # 效率指标（有 metrics 的游戏才统计）
    avg_idle_ratio: float = 0.0
    avg_expired: float = 0.0
    avg_clear_asm: float = 0.0
    avg_serve_gap: float = 0.0
    avg_none_ratio: float = 0.0
    avg_stockpile_in: float = 0.0
    avg_stockpile_out: float = 0.0
    avg_stockpile_max: float = 0.0
    has_metrics: bool = False


def compute_stats(results: list[EpisodeResult]) -> StrategyStats:
    """计算单策略统计指标"""
    rewards = [r.total_reward for r in results]
    steps = [r.steps for r in results]
    actions = [r.actions_taken for r in results]
    versions = {_result_version(r) for r in results}
    scoring_version = next(iter(versions)) if len(versions) == 1 else "mixed"

    # 效率指标
    has_metrics = any(r.metrics is not None for r in results)
    if has_metrics:
        metrics_list = [r.metrics for r in results if r.metrics is not None]
        idle_ratios = [m.cooker_idle_ratio for m in metrics_list]
        expireds = [m.expired_ingredients for m in metrics_list]
        clear_asms = [m.clear_assembly_count for m in metrics_list]
        serve_gaps = [m.avg_serve_interval for m in metrics_list]
        none_ratios = [m.none_ratio for m in metrics_list]
        stock_ins = [m.stockpile_inserts for m in metrics_list]
        stock_outs = [m.stockpile_pulls for m in metrics_list]
        stock_maxs = [m.stockpile_max_occupancy for m in metrics_list]

        return StrategyStats(
            name=results[0].strategy_name,
            games=len(results),
            avg_reward=mean(rewards),
            std_reward=stdev(rewards) if len(rewards) > 1 else 0.0,
            avg_steps=mean(steps),
            avg_actions=mean(actions),
            max_reward=max(rewards),
            min_reward=min(rewards),
            scoring_version=scoring_version,
            avg_idle_ratio=mean(idle_ratios),
            avg_expired=mean(expireds),
            avg_clear_asm=mean(clear_asms),
            avg_serve_gap=mean(serve_gaps),
            avg_none_ratio=mean(none_ratios),
            avg_stockpile_in=mean(stock_ins),
            avg_stockpile_out=mean(stock_outs),
            avg_stockpile_max=mean(stock_maxs),
            has_metrics=True,
        )

    return StrategyStats(
        name=results[0].strategy_name,
        games=len(results),
        avg_reward=mean(rewards),
        std_reward=stdev(rewards) if len(rewards) > 1 else 0.0,
        avg_steps=mean(steps),
        avg_actions=mean(actions),
        max_reward=max(rewards),
        min_reward=min(rewards),
        scoring_version=scoring_version,
    )


def paired_t_test(a: list[float], b: list[float]) -> tuple[float, float]:
    """
    配对 t-test。

    Returns:
        (t_statistic, p_value)
    """
    from math import sqrt

    if len(a) != len(b) or len(a) < 2:
        return 0.0, 1.0

    diffs = [x - y for x, y in zip(a, b)]
    d_mean = mean(diffs)
    d_std = stdev(diffs) if len(diffs) > 1 else 0.0

    if d_std == 0:
        return (9999.0, 0.0) if d_mean != 0 else (0.0, 1.0)

    t = d_mean / (d_std / sqrt(len(diffs)))

    # 简化 p-value 计算（使用正态近似）
    # 实际应该用 scipy.stats.t.cdf，但为了不引入依赖，用简化版
    # |t| > 2 对应 p < 0.05（约），|t| > 2.6 对应 p < 0.01（约）
    import math
    # 更精确的近似：使用 error function
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    # 但这不太对，让我用另一个方法
    # 简单规则：
    if abs(t) > 2.576:
        p = 0.01
    elif abs(t) > 1.96:
        p = 0.05
    elif abs(t) > 1.645:
        p = 0.10
    else:
        p = 1.0

    return t, p


def print_comparison(results: dict[str, list[EpisodeResult]]) -> None:
    """打印策略对比表格"""
    stats = {name: compute_stats(rs) for name, rs in results.items()}
    names = list(stats.keys())

    print("\n" + "=" * 80)
    print("Benchmark Results")
    versions = {s.scoring_version for s in stats.values()}
    print(f"Scoring version: {','.join(sorted(versions))} "
          f"(v1=sum only, v2=sum+final visibility)")
    print("=" * 80)

    # 表头
    print(f"\n{'Strategy':<20} {'Games':>6} {'Avg Reward':>12} {'Std':>8} {'Max':>8} {'Min':>8} {'Actions':>8}")
    print("-" * 80)

    # 按平均 reward 排序
    sorted_names = sorted(names, key=lambda n: stats[n].avg_reward, reverse=True)
    baseline_name = sorted_names[0]

    for name in sorted_names:
        s = stats[name]
        marker = " ★" if name == baseline_name else ""
        print(
            f"{s.name:<20} {s.games:>6} {s.avg_reward:>12.1f} {s.std_reward:>8.1f} "
            f"{s.max_reward:>8.0f} {s.min_reward:>8.0f} {s.avg_actions:>8.1f}{marker}"
        )

    # 配对对比
    if len(names) > 1:
        print("\n" + "-" * 80)
        print(f"Paired comparison (vs {baseline_name}):")
        print(f"{'Strategy':<20} {'Δ Reward':>10} {'t-stat':>8} {'p-value':>8} {'Significant'}")
        print("-" * 80)

        baseline_rewards = [r.total_reward for r in results[baseline_name]]

        for name in sorted_names[1:]:
            rewards = [r.total_reward for r in results[name]]
            t, p = paired_t_test(baseline_rewards, rewards)
            delta = stats[name].avg_reward - stats[baseline_name].avg_reward
            sig = "***" if p <= 0.01 else "**" if p <= 0.05 else "*" if p <= 0.10 else ""
            print(
                f"{name:<20} {delta:>+10.1f} {t:>8.2f} {p:>8.2f} {sig}"
            )

    # 效率指标表
    if any(s.has_metrics for s in stats.values()):
        print("\n" + "-" * 80)
        print("Efficiency Metrics (averages)")
        print(f"{'Strategy':<20} {'Idle%':>6} {'Expired':>8} {'ClrAsm':>7} {'SrvGap':>7} {'None%':>6} {'StkIn':>6} {'StkOut':>6} {'StkMax':>6}")
        print("-" * 80)
        for name in sorted_names:
            s = stats[name]
            if s.has_metrics:
                print(
                    f"{s.name:<20} {s.avg_idle_ratio*100:>5.1f}% {s.avg_expired:>8.1f} "
                    f"{s.avg_clear_asm:>7.1f} {s.avg_serve_gap:>7.1f}s {s.avg_none_ratio*100:>5.1f}% "
                    f"{s.avg_stockpile_in:>6.1f} {s.avg_stockpile_out:>6.1f} {s.avg_stockpile_max:>6.1f}"
                )

    print("=" * 80)


def export_csv(results: dict[str, list[EpisodeResult]], filepath: str) -> None:
    """导出为 CSV（含效率指标）"""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        header = ["strategy", "seed", "reward", "steps", "actions",
                  "final_score", "total_visibility", "scoring_version",
                  "idle_ratio", "expired", "clear_asm", "serve_gap",
                  "none_ratio", "stockpile_in", "stockpile_out", "stockpile_max"]
        writer.writerow(header)
        for name, rs in results.items():
            for r in rs:
                m = r.metrics
                row = [name, r.seed, r.total_reward, r.steps, r.actions_taken,
                       _result_final(r),
                       _result_visibility(r),
                       _result_version(r)]
                if m:
                    row.extend([
                        round(m.cooker_idle_ratio, 3),
                        m.expired_ingredients,
                        m.clear_assembly_count,
                        round(m.avg_serve_interval, 2),
                        round(m.none_ratio, 3),
                        m.stockpile_inserts,
                        m.stockpile_pulls,
                        m.stockpile_max_occupancy,
                    ])
                else:
                    row.extend([""] * 8)
                writer.writerow(row)

    print(f"Exported to {filepath}")


def export_json(results: dict[str, list[EpisodeResult]], filepath: str) -> None:
    """导出为 JSON"""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {}
    for name, rs in results.items():
        stats = compute_stats(rs)
        data[name] = {
            "stats": {
                "games": stats.games,
                "avg_reward": stats.avg_reward,
                "std_reward": stats.std_reward,
                "max_reward": stats.max_reward,
                "min_reward": stats.min_reward,
                "avg_steps": stats.avg_steps,
                "avg_actions": stats.avg_actions,
                "scoring_version": stats.scoring_version,
            },
            "games": [
                {
                    "seed": r.seed,
                    "reward": r.total_reward,
                    "final_score": _result_final(r),
                    "total_visibility": _result_visibility(r),
                    "scoring_version": _result_version(r),
                    "steps": r.steps,
                    "actions": r.actions_taken,
                }
                for r in rs
            ],
        }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Exported to {filepath}")
