"""终局计分与度量上下文（Ticket #8 / Spec #7 S1）.

把统计查询、总分、终局结算从环境对象抽离为独立上下文，
真实环境与模拟器共用同一语义：

- 单步累积 ``total_score``：所有已完成订单得分之和
- 总 visibility ``total_visibility``：所有已完成订单 visibility 之和
- 终局分数 ``final_score`` = ``total_score`` + ``total_visibility``
  （docs/game_rules.md 「最终总分 = 所有已完成订单的得分之和 +
  游戏结束时的总 visibility」）

口径版本：
- ``v1``：逐单和（历史基准，未含终局 visibility 加成）
- ``v2``：逐单和 + 终局 visibility（当前口径）

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新，同时更新所属目录的md
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCORING_VERSION = "v2"
"""当前计分口径版本（v1=逐单和，v2=逐单和+终局visibility）."""

LEGACY_SCORING_VERSION = "v1"
"""历史基准口径（未含终局加成），仅用于隔离展示."""


def finalize_score(total_score: float, total_visibility: float) -> float:
    """纯函数：终局分数 = 订单得分和 + 总 visibility."""
    return total_score + total_visibility


@dataclass
class ScoringContext:
    """度量上下文：统计查询、总分、终局结算的唯一持有者.

    环境对象（真实 GameEnv / 模拟器侧）只转发、不自算；
    改计分只碰此处，策略行为不变.
    """

    orders_served: int = 0
    total_score: float = 0.0
    """单步累积：订单得分之和（不含终局加成）."""
    total_visibility: float = 0.0
    orders_timeout: int = 0
    actions_taken: int = 0
    scoring_version: str = SCORING_VERSION
    _finalized: bool = field(default=False, repr=False)
    _final_score: float = field(default=0.0, repr=False)

    def record_serve(self, score: float, visibility: float) -> None:
        """记录一次订单完成（只累加，不结算；新记录使终局缓存失效）."""
        self.orders_served += 1
        self.total_score += score
        self.total_visibility += visibility
        self._finalized = False

    def record_timeout(self) -> None:
        self.orders_timeout += 1

    def record_action(self) -> None:
        self.actions_taken += 1

    def invalidate(self) -> None:
        """使终局缓存失效（兼容层直接改数后调用）."""
        self._finalized = False

    def finalize(self) -> float:
        """终局结算，幂等：重复调用返回同一分数，不重复加总."""
        if not self._finalized:
            self._final_score = finalize_score(self.total_score, self.total_visibility)
            self._finalized = True
        return self._final_score

    @property
    def final_score(self) -> float:
        """终局分数（未 finalize 时实时计算，已 finalize 返回缓存）."""
        if self._finalized:
            return self._final_score
        return finalize_score(self.total_score, self.total_visibility)

    def get_stats(self) -> dict[str, object]:
        """统计快照（含单步累积与终局分数两个数字，查询无副作用）."""
        return {
            "orders_served": self.orders_served,
            "total_score": self.total_score,
            "total_visibility": self.total_visibility,
            "final_score": self.final_score,
            "scoring_version": self.scoring_version,
            "orders_timeout": self.orders_timeout,
            "actions_taken": self.actions_taken,
        }
