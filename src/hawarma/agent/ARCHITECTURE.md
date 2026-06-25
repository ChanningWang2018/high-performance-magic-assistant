# src/hawarma/agent 目录架构

## 📁 目录概述

此目录包含烹饪 Agent 的决策逻辑，负责在每个决策点选择最优动作。

## ⚠️ 重要提示

**一旦本目录有变化（新增/删除/重命名文件），请立即更新本文档！**

## 📄 文件列表

### `__init__.py`
- **地位**: 包初始化文件
- **功能**: 导出所有 Agent 类和动作类型

### `strategy.py` (原 `agent.py` → 已拆分)
- **地位**: Strategy 抽象基类 + 策略注册
- **状态**: ✅ 完成
- **功能**:
  - **Strategy ABC**：定义 `decide(state) -> Action` 纯决策接口
  - **策略注册**：通过 `strategy_registry` 按名称查找策略类
  - **`on_game_start()`**：可选钩子，接收当前局 Recipe dict
- **输入**: UnifiedState
- **输出**: Action | None

### ⚠️ Rush 优先级 Tiebreaker（2026-06-03 修复）

**问题**：旧 6 个 Gastronome 策略中的 3 个 CPM 变体（`CPMCascadeStrategy` / `VisibilityAwareCascadeStrategy` / `CPMEnhancedCascadeStrategy`）的 `_prioritized_orders` 仅按 CP 排序，当两个同 recipe 订单（同 CP）时退化为稳定排序保留原 slot 顺序，导致 Rush 未被优先。

**修复**：在 sort key 中追加 `rush_priority`（0=Rush, 1=Normal）作为 tiebreaker：

```python
scored.append((cp, rush_priority, slot_idx, order))
scored.sort(key=lambda x: (x[0], x[1]))
```

**回归测试**：`tests/test_rush_tiebreaker.py`（4 个 case，覆盖统一 `GastronomeStrategy` 的 `_prioritized_orders` 和 `decide` 路径）

### ⚠️ 落地安全窗口（2026-06-04 修复）

**问题**：游戏内 5s 过期窗口在食材"落地"瞬间判定，但 `decide()` 只能在决策瞬间检查 `CookerState.is_expired(state.time)`。两者之间存在 dispatch + Maxtouch swipe + 落地动画的尾段延迟（合计约 0.3-0.5s），导致决策时"未过期"但落地时已糊掉（< 1 次/局，assembly 居多）。

**修复**：在 `_try_move_to_assembly` / `_try_store_to_stockpile` 的过期过滤处用 `_is_arrival_safe(state.time, cooker.expired_at)` 代替 `is_expired(state.time)`，提前 `MOVE_SAFETY_MARGIN = 0.5s` 收紧窗口：

```python
def _is_arrival_safe(self, state_time, expired_at):
    if expired_at is None:
        return False
    return state_time + self.MOVE_SAFETY_MARGIN < expired_at
```

**未改**：`_try_clear_expired` 保持严格 `is_expired` 判定（语义不同：清的是已过期灶台）；`_try_serve` 等不受影响；dessert 策略不动。

---

## 🎯 Agent Shell 架构

### 重构后架构

Runner 不再包含决策逻辑，而是作为 **Agent Shell** 运行：

```
Runner
    │
    ├─→ Runner (Agent Shell)
    │       │
    │       ├── _build_unified_state()  ──→ UnifiedState
    │       │
    │       └── strategy.decide(state) ──→ Action
    │               │
    │               └─→ GastronomeStrategy (贪心瀑布决策架构)
    │
    └─→ _execute_action() - 执行 Agent 返回的动作
```

### Agent Shell 职责

| 职责 | 方法 | 说明 |
|------|------|------|
| 状态封装 | `_build_unified_state()` | 将 GameEnv 转换为 UnifiedState |
| 决策委托 | `step()` → `strategy.decide(state)` | 纯透传，不干预决策 |
| 停滞检测 | `step_with_diagnostics()` | 5秒无动作输出诊断日志 |
| 统计维护 | `stats` / `on_order_served()` | 追踪订单完成、超时、得分 |

### 设计原理

1. **Strategy 可插拔**：同一套决策逻辑可在 Playground 和真实游戏中复用
2. **Agent 无决策**：`step()` 只负责构建状态和调用 `strategy.decide()`
3. **诊断独立**：停滞检测不影响 Strategy 决策，仅用于调试

### 动作类型定义

```python
@dataclass
class CookAction(Action):
    """烹饪动作"""
    ingredient: str      # 食材名称
    cooker: str          # 灶台名称
    duration: float      # 烹饪时长（秒）
    order_id: Optional[int] = None  # 关联订单ID

@dataclass
class MoveToAssemblyAction(Action):
    """移动到组装站"""
    cooker: str
    order_id: Optional[int] = None

@dataclass
class MoveToStockpileAction(Action):
    """移动到库存"""
    cooker: str
    slot: str

@dataclass
class PullFromStockpileAction(Action):
    """从库存取用"""
    slot: str
    ingredient: str

@dataclass
class AddCondimentAction(Action):
    """添加调料"""
    condiment: str

@dataclass
class ServeOrderAction(Action):
    """送餐"""
    slot_idx: int

@dataclass
class ClearCookerAction(Action):
    """清理灶台"""
    cooker: str
```

---

## 🔄 Agent 与环境交互流程

### 单步决策流程（重构后）

```python
def step(self) -> Optional[Action]:
    """
    单步决策：Agent Shell + Strategy 注入模式
    
    流程：
    1. 构建 UnifiedState 从 env
    2. 调用 Strategy.decide(state) 获取动作
    3. 返回动作
    """
    state = self._build_unified_state()
    action = self._strategy.decide(state)
    return action
```

### Strategy 默认实现

决策逻辑位于 `src/hawarma/agent/strategies/gastronome.py` 的 `GastronomeStrategy`（贪心瀑布架构）：

```python
def decide(self, state: UnifiedState) -> Action | None:
    # 0. 检查组装站是否需要清理
    if action := self._try_clear_assembly(state, assembly_ings):
        return action

    # 1. 送餐
    if action := self._try_serve(state, assembly_ings):
        return action

    # 2. 清理过期食材
    if action := self._try_clear_expired(state):
        return action

    # 3. 移动完成食材到组装站
    if action := self._try_move_to_assembly(state, assembly_ings):
        return action

    # 4. 开始烹饪（多订单并行）
    if action := self._try_parallel_cooking(state, assembly_ings):
        return action

    # 5. 添加调料
    if action := self._try_add_condiment_urgent(state, assembly_ings):
        return action

    # 6. 从库存取用
    if action := self._try_pull_from_stockpile_urgent(state):
        return action

    # 7. 预烹饪
    if action := self._try_precook(state, assembly_ings):
        return action

    # 8. 存入 stockpile
    if action := self._try_store_to_stockpile(state):
        return action

    # 9. 从库存取用（回退）
    if action := self._try_pull_from_stockpile(state):
        return action

    return None
```

---

## 📊 状态查询接口

Agent Shell 通过 `self.env` 访问 GameEnv 提供的状态接口，并封装为 `UnifiedState` 供 Strategy 使用：

### 环境状态（内部使用）
- `self.env.time` - 当前游戏时间（秒）
- `self.env.orders` - 订单列表（4个槽位）
- `self.env.cookers` - 灶台状态字典
- `self.env.assembly` - 组装站状态
- `self.env.stockpile` - 库存状态字典

### UnifiedState（Strategy 输入）
- `time` - 当前游戏时间
- `orders` - 订单元组（4个槽位）
- `cookers` - 灶台状态字典
- `assembly` - 组装站状态
- `stockpile` - 库存状态字典
- `recipes` - 配方字典
- `game_duration` - 游戏总时长
- `is_in_animation_window` - 是否在动画窗口期

### Agent Shell 保留的辅助方法
- `self._get_free_cookers()` - 获取空闲灶台列表（诊断用）
- `self._prioritized_orders()` - 按优先级排序订单（诊断用）
- `self._infer_recipe_from_assembly()` - 根据组装站食材推断配方（诊断用）

---

## 🔧 配方推断机制

### 问题背景

当 `assembly.target_recipe_slug` 为 `None` 时（可能由状态同步延迟或边缘操作序列导致），`_try_add_condiment()` 和 `_try_serve()` 需要能够根据已有食材推断出目标配方，避免 Agent 决策瘫痪。

### 推断策略

```python
def _infer_recipe_from_assembly(self, state: UnifiedState) -> str | None:
    """根据组装站食材推断匹配的配方（精确匹配）"""
    assembly = state.assembly
    if not assembly.ingredients_cookers:
        return None
    assembly_pairs = {(ing[0], ing[1]) for ing in assembly.ingredients_cookers}

    for _, order in self._prioritized_orders(state):
        ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
        recipe_pairs = {(n, c) for n, c, _ in ics}
        if assembly_pairs == recipe_pairs:
            return order.recipe_slug
    return None
```

**设计原则**：
- 按订单优先级遍历（rush 优先、timeout 近的优先）
- 组装站食材必须与配方食材**精确匹配**（数量、名称、灶台类型全部一致）
- 返回第一个匹配的订单配方

### 使用场景

| 方法 | 何时使用推断 |
|------|-------------|
| `_try_add_condiment()` | `target_recipe_slug` 为 None 时，推断后检查食材是否齐全并添加调料 |
| `_try_serve()` | 不依赖推断，直接遍历订单匹配（已有容错） |

### 与环境层推断的关系

环境层（`GameEnv`）在 `pull_from_stockpile()` 和 `add_to_assembly()` 中也做推断，这是**预防层**。Agent 层的推断是**恢复层**。两层配合确保即使环境层推断失败，Agent 仍能正常工作。

---

## 🎮 预烹饪策略（已验证无效）

> **经验教训**：预烹饪策略在实际测试中效果不佳，预烹饪的食材可能与订单不匹配，反而降低性能。建议使用按需响应策略。

### ~~高频食材预存配置~~（不推荐使用）

```python
# 以下配置仅供参考，实际测试表明预烹饪会降低性能
DEFAULT_STOCKPILE = [
    ("creamfield_rice", "pot", 2.0),      # 米饭，pot灶台，2秒
    ("clearwater_fish", "oven", 3.0),     # 鱼，oven灶台，3秒
    ("wild_mushroom", "skillet", 2.0),    # 蘑菇，skillet灶台，2秒
]
```

### ~~预烹饪触发条件~~（不推荐使用）

1. 所有订单需要的食材都已在烹饪或库存中
2. 仍有空闲灶台
3. 库存中某食材数量 < `PRECOOK_THRESHOLD`（默认2）

### 测试结果

| 策略 | 完成订单 | 超时率 | 效果 |
|------|----------|--------|------|
| 按需响应（推荐） | 13.4个 | 2.2% | 基准 |
| 预烹饪到assembly | 失败 | 失败 | -30% |
| 预烹饪到stockpile | 13.1个 | 5% | -2% |

**结论**：在不确定的环境中，按需响应比预测更可靠。

---

## 📈 统计信息

Agent 维护以下统计数据：

```python
self.stats = {
    "orders_served": 0,      # 已完成订单数
    "total_score": 0,        # 总得分
    "orders_timeout": 0,     # 超时订单数
    "actions_taken": 0,      # 执行动作数
}
```

通过 `get_stats()` 方法获取包含游戏时间的完整统计：

```python
def get_stats(self) -> dict:
    return {
        "time": self.env.time,
        "orders_served": self.stats["orders_served"],
        "total_score": self.stats["total_score"],
        "orders_timeout": self.stats["orders_timeout"],
        "actions_taken": self.stats["actions_taken"],
    }
```

---

## 🔗 与其他模块的关系

```
Runner (game/runner.py)
    │
    ├─→ Strategy (agent/strategy.py) ← 本模块
    │       │
    │       ├─→ GameEnv (game/game_env.py) - 读取状态
    │       └─→ 返回 Action 对象
    │
    └─→ _execute_action() - 执行 Agent 返回的动作
            │
            ├─→ Operator - 执行 UI 操作
            └─→ GameEnv - 更新状态
```
