"""
执行耗时观测（Ticket #9 / Spec #7 S2）

地位：在 Runner 送餐路径与操作执行层埋分段耗时打点，
      只观测、不改行为。把「时间花在哪里」变成可读的聚合指标。

输入：各执行段的单次耗时（秒）
输出：按段聚合的 count / total / avg / max（to_dict()）

设计原则：
- 纯观测：打点只取 perf_counter 时间戳，不改变任何控制流或 sleep 时长
- 增量聚合：只累计 count/total/max，不存全量历史（对齐 bench/metric.py 思路）
- 看似与 core/scoring.py 并列的「度量上下文」，但仅服务于执行层，
  故归属 game/ 而非 core/（core 只放数据契约）

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新，同时更新所属目录的md
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class SegmentTiming:
    """单段耗时聚合：样本数、累计时长、最大时长。"""

    name: str
    count: int = 0
    total: float = 0.0
    max: float = 0.0

    def record(self, dt: float) -> None:
        """记录一次该段的耗时（秒）。负值按 0 处理，避免时钟抖动污染统计。"""
        if dt < 0:
            dt = 0.0
        self.count += 1
        self.total += dt
        if dt > self.max:
            self.max = dt

    @property
    def avg(self) -> float:
        """平均耗时（秒）。无样本时为 0.0。"""
        return self.total / self.count if self.count else 0.0

    def to_dict(self) -> dict[str, int | float]:
        """扁平可读指标，供 stats 输出 / 日志展示。"""
        return {
            "count": self.count,
            "total": round(self.total, 4),
            "avg": round(self.avg, 4),
            "max": round(self.max, 4),
        }


class ExecutionTiming:
    """执行耗时上下文：滑动往返 + 送餐验证各段。"""

    def __init__(self) -> None:
        self.swipe = SegmentTiming("swipe")
        self.serve_verify_wait = SegmentTiming("serve_verify_wait")
        self.serve_verify_snapshot = SegmentTiming("serve_verify_snapshot")
        self.serve_retry = SegmentTiming("serve_retry")
        self.serve_cleanup = SegmentTiming("serve_cleanup")

    def to_dict(self) -> dict[str, dict[str, int | float]]:
        """所有分段的聚合指标。"""
        return {
            "swipe": self.swipe.to_dict(),
            "serve_verify_wait": self.serve_verify_wait.to_dict(),
            "serve_verify_snapshot": self.serve_verify_snapshot.to_dict(),
            "serve_retry": self.serve_retry.to_dict(),
            "serve_cleanup": self.serve_cleanup.to_dict(),
        }


class Timer:
    """拉起即计时的上下文助手：构造即标时间，record() 落账。

    用法：
        t = Timer(timing.serve_verify_wait)   # 启动计时
        ... await ...                        # 待测段
        t.record()                           # 落账
    """

    def __init__(self, segment: SegmentTiming) -> None:
        self._segment = segment
        self._start = time.perf_counter()
        self._done = False

    def record(self) -> float:
        """结束计时并落账到对应分段，返回本次耗时（秒）。幂等：重复调用只记一次。"""
        if self._done:
            return 0.0
        dt = time.perf_counter() - self._start
        self._segment.record(dt)
        self._done = True
        return dt