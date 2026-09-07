"""
Playground Core Runner

游戏循环和基准测试运行器。

输入: GameEnv, Agent/Strategy, 配置
输出: EpisodeResult / BenchmarkResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from hawarma.core.actions import Action
    from playground.env.game_env import GameEnv
    from hawarma.agent.strategy import Strategy

from playground.bench.metric import EfficiencyMetrics, MetricsCollector
from hawarma.core.scoring import SCORING_VERSION, finalize_score


@dataclass
class EpisodeResult:
    """单局游戏结果"""

    total_reward: float
    """单步累积：订单得分之和（v1 口径，不含终局 visibility 加成）"""
    steps: int
    actions_taken: int
    orders_served: int
    orders_timeout: int
    final_time: float
    seed: int
    strategy_name: str
    history: list[tuple[float, object, Action | None]] = field(default_factory=list)
    """[(time, state, action), ...] 用于 replay"""
    metrics: EfficiencyMetrics | None = None
    """效率指标（详见 playground/bench/metrics.py）"""
    total_visibility: float = 0.0
    """终局总 visibility（已完成订单 visibility 之和）"""
    final_score: float = 0.0
    """终局分数 = total_reward + total_visibility（v2 口径）"""
    scoring_version: str = SCORING_VERSION
    """计分口径版本（v1=逐单和，v2=逐单和+终局visibility）"""

    def __post_init__(self) -> None:
        # 默认构造时保持终局一致性：final = sum + visibility；
        # 显式传入的非零 final_score 予以保留（回放/旧数据重建）。
        if self.final_score == 0.0 and (self.total_reward != 0.0 or self.total_visibility != 0.0):
            self.final_score = finalize_score(self.total_reward, self.total_visibility)


def run_episode(
    env: GameEnv,
    agent: Agent,
    seed: int,
    record_history: bool = False,
    max_steps: int = 2000,
    recipe_slugs: list[str] | None = None,
    collect_metrics: bool = False,
) -> EpisodeResult:
    """
    运行单局游戏。

    Args:
        env: 游戏环境
        agent: Agent（包含 Strategy）
        seed: 随机种子
        record_history: 是否记录完整历史（用于 replay）
        max_steps: 最大步数（安全上限）
        collect_metrics: 是否收集效率指标（cooker idle, stockpile turnover 等）

    Returns:
        EpisodeResult: 游戏结果
    """
    obs, info = env.reset(seed=seed, recipe_slugs=recipe_slugs)
    agent.reset()
    agent.strategy.on_game_start(info.get("recipes", {}))

    total_reward = 0.0
    steps = 0
    actions_taken = 0
    orders_served = 0
    orders_timeout = 0
    history = []

    # 效率指标收集器
    total_cookers = len(obs.cookers)
    total_stockpile = len(obs.stockpile)
    collector = MetricsCollector(total_cookers=total_cookers, total_stockpile_slots=total_stockpile) if collect_metrics else None

    while steps < max_steps:
        action = agent.act(obs)

        if record_history:
            history.append((obs.time, obs, action))

        result = env.step(action)
        agent.observe(result.observation, result.reward, result.terminated, result.info)

        total_reward += result.reward
        steps += 1

        if action is not None:
            actions_taken += 1

        # 统计订单事件
        for event in result.info.get("events", []):
            from playground.env_simulator_types import EventType
            if event.event_type == EventType.ORDER_SERVED:
                orders_served += 1
            elif event.event_type == EventType.ORDER_TIMEOUT:
                orders_timeout += 1

        # 效率指标收集
        if collector is not None:
            collector.update(result.observation, action, result)

        if result.terminated or result.truncated:
            break

        obs = result.observation

    total_visibility = float(getattr(obs, "total_visibility", 0.0) or 0.0)
    return EpisodeResult(
        total_reward=total_reward,
        steps=steps,
        actions_taken=actions_taken,
        orders_served=orders_served,
        orders_timeout=orders_timeout,
        final_time=obs.time if hasattr(obs, 'time') else 0.0,
        seed=seed,
        strategy_name=type(agent.strategy).__name__,
        history=history if record_history else [],
        metrics=collector.summarize() if collector else None,
        total_visibility=total_visibility,
        final_score=finalize_score(total_reward, total_visibility),
        scoring_version=SCORING_VERSION,
    )


def run_benchmark(
    env_factory: Callable[[], GameEnv],
    strategies: dict[str, Strategy],
    num_games: int = 50,
    seeds: list[int] | None = None,
    recipe_slugs: list[str] | None = None,
) -> dict[str, list[EpisodeResult]]:
    """
    运行多策略基准测试。

    Args:
        env_factory: 创建 GameEnv 的工厂函数
        strategies: {策略名: Strategy 实例}
        num_games: 每策略测试局数
        seeds: 自定义种子列表，None 则使用 0..num_games-1

    Returns:
        {策略名: [EpisodeResult, ...]}
    """
    from playground.agents.base import Agent

    if seeds is None:
        seeds = list(range(num_games))

    results = {name: [] for name in strategies}

    for seed in seeds:
        for name, strategy in strategies.items():
            env = env_factory()
            agent = Agent(strategy)
            result = run_episode(env, agent, seed=seed, recipe_slugs=recipe_slugs, collect_metrics=True)
            results[name].append(result)

    return results
