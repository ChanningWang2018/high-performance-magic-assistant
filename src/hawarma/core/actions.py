"""
核心动作类型定义

Strategy 产出 Action，Env 消费 Action。
Action 是 Strategy 和 Env 之间的操作契约。

调度表达（Ticket #11 / Spec #7 S3）：
- 现有子类字段名冻结、零改名；基类仅增 `priority` / `cancelled`
  （kw_only 带默认值，旧位置构造不受影响），`action_type` 为只读属性。
- `schedule_actions` 为双端共享的调度函数：过滤已取消 +
  按优先级降序稳定排序。Runner 与 SimEnv 同调此函数，保证回放一致。

按 station 分组：
  - 共享：ClearCookerAction
  - Gastronome 专用：AddCondimentAction, ClearAssemblyAction, CookAction,
    MoveToAssemblyAction, MoveToStockpileAction, PullFromStockpileAction,
    ServeOrderAction
  - Dessert 专用：MoveToMixingBowlAction, AddCondimentToMixingBowlAction,
    StirAction, MoveMixingBowlToCookerAction, ServeFromCookerAction,
    ClearMixingBowlAction

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新，同时更新所属目录的md
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Action:
    """动作基类：调度表达只增不改，默认优先级 0、默认不取消。"""

    priority: int = field(default=0, kw_only=True)
    """调度优先级（越大越先执行；只参与排序，不改变动作语义）"""

    cancelled: bool = field(default=False, kw_only=True)
    """取消标记（True 则分发时跳过，不执行、不计分）"""

    @property
    def action_type(self) -> str:
        """动作类型名（即子类类名，供分发与日志使用）"""
        return type(self).__name__


def schedule_actions(actions: list[Action]) -> list[Action]:
    """双端共享调度：过滤已取消，按优先级降序稳定排序。

    输入列表保持不动，返回新列表。优先级相同时保留输入顺序。
    Runner.dispatch_batch 与 SimEnv.run_action_sequence 同调此函数。
    """
    eligible = [a for a in actions if not a.cancelled]
    return sorted(eligible, key=lambda a: a.priority, reverse=True)


# ── 共享（两种 station 都用） ──

@dataclass
class ClearCookerAction(Action):
    """清理灶台"""
    cooker: str


# ── Gastronome 专用 ──

@dataclass
class AddCondimentAction(Action):
    """调料区 → 组装站"""
    condiment: str


@dataclass
class ClearAssemblyAction(Action):
    """清空组装站"""


@dataclass
class CookAction(Action):
    """食材区 → 灶台烹饪"""
    ingredient: str
    cooker: str
    duration: float
    order_id: int | None = None


@dataclass
class MoveToAssemblyAction(Action):
    """灶台 → 组装站"""
    cooker: str
    order_id: int | None = None


@dataclass
class MoveToStockpileAction(Action):
    """灶台 → 库存"""
    cooker: str
    slot: str


@dataclass
class PullFromStockpileAction(Action):
    """库存 → 组装站"""
    slot: str
    ingredient: str


@dataclass
class ServeOrderAction(Action):
    """组装站 → 取餐台"""
    slot_idx: int


# ── Dessert 专用 ──

@dataclass
class MoveToMixingBowlAction(Action):
    """食材区 → 搅拌盆"""
    ingredient: str


@dataclass
class AddCondimentToMixingBowlAction(Action):
    """调料区 → 搅拌盆"""
    condiment: str


@dataclass
class StirAction(Action):
    """搅拌（从搅拌盆坐标向左水平滑动）"""
    distance: float = 400.0
    duration: float = 1.5
    steps: int = 10


@dataclass
class MoveMixingBowlToCookerAction(Action):
    """搅拌盆 → 灶台"""
    cooker: str


@dataclass
class ServeFromCookerAction(Action):
    """灶台 → 取餐台"""
    cooker: str
    slot_idx: int


@dataclass
class ClearMixingBowlAction(Action):
    """清空搅拌盆"""
    pass