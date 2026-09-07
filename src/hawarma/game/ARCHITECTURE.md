# src/hawarma/game 目录架构

## 📁 目录概述

此目录包含真实游戏环境的状态追踪和管理层。

## ⚠️ 重要提示

**一旦本目录有变化（新增/删除/重命名文件），务必对开头注释进行相应的必要更新，同时更新所属目录的md**

## 📄 文件列表

### `__init__.py`
- **地位**: 包初始化文件
- **功能**: 导出所有游戏组件
- **导出**: `GameEnv`, `Scanner`, `Operator`, `Runner`, `CookerState`, `AssemblyState`, `StockpileSlot`, `Order`, `MixingBowlState`

### `env.py`
- **地位**: 模块文档（不再定义 ABC）
- **功能**: 说明真实环境和模拟环境通过 UnifiedState + Action 共享数据契约，不定义行为接口

### `game_env.py`
- **地位**: 真实游戏状态追踪器
- **功能**:
  - 独立类（不继承 ABC），追踪灶台、组装站、搅拌盆、库存、订单、调料
  - 通过 `get_unified_state()` 产出 `UnifiedState` 供 Strategy 决策
  - 异步环境：状态由 Scanner/Runner 外部更新，不实现 `step()`
- **输入**: UI操作结果、订单检测结果
- **输出**: `UnifiedState` 快照供 Strategy 决策
- **关键类**: `GameEnv`

### `scanner.py`
- **地位**: 订单扫描器
- **状态**: ✅ 完成（异步化 snapshot）
- **功能**:
  - 检测屏幕上的订单
  - 识别配方和加急状态
  - 检测游戏开始（timer图标）
  - **异步截图**：`detect_timer()`, `scan_orders()`, `scan_new_orders()` 均为 async 方法，使用 `asyncio.to_thread()` 避免阻塞事件循环
- **输入**: 屏幕截图、配置对象
- **输出**: 检测到的订单信息
- **关键类**: `Scanner`, `DetectedOrder`

### `verifier.py`
- **地位**: 组装站提交验证器（可插拔组件）
- **状态**: ✅ 新增
- **功能**:
  - 通过模板匹配检测组装站区域是否为空
  - 验证送餐操作是否成功提交菜品
  - ROI: `(1150, 720, 1600, 1030)`
  - 模板: `static/img/empty_assembly.jpg`
- **输入**: 配置对象
- **输出**: 验证结果（成功/失败）
- **关键类**: `Verifier`
- **关键方法**: `is_assembly_empty()` — 返回 True 表示提交成功

### `operator.py`
- **地位**: UI操作执行器
- **状态**: ✅ 完成
- **功能**:
  - 封装所有swipe操作
  - 从config.yaml读取坐标配置
  - 提供异步执行接口
  - **动态坐标映射**：根据菜谱选择顺序确定元素位置
  - 使用 Airtest 内置的 minitouch 触摸方法
  - **垃圾桶坐标配置化**：`clear_cooker()` 和 `clear_assembly()` 使用 `config.screen.trash_position`
  - **clear_assembly 增强**：使用 `duration=0.4, steps=8` 确保拖拽动作被游戏识别
- **输入**: 符号化操作（食材名、灶台名等）
- **输出**: swipe操作执行结果
- **关键类**: `Operator`

#### 坐标映射规则

Operator根据菜谱选择顺序动态确定各元素坐标：

1. **Cookers**：
   - 按菜谱顺序收集`cookers_layout`并去重
   - 根据种类数量选择槽位：1种→[1]，2种→[1,2]，3种→[0,1,2]，4种→[0,1,2,3]
   
2. **Ingredients**：
   - 按菜谱顺序收集`raw_ingredients`并去重
   - **反转顺序**后分配索引（从下到上、从左到右）
   
3. **Condiments**：
   - 按菜谱顺序收集`condiments`并去重
   - 按顺序分配索引（从下到上、从左到右）

详细规则参见 `docs/game_rules.md` 第2节。

### `runner.py`
- **地位**: 真实游戏桥接器
- **状态**: ✅ 完成（三循环并行 + 事件驱动决策 + 送餐验证重试 + 耗时观测）
- **功能**:
  - 协调 Agent、环境、扫描器和 UI 执行器
  - 管理游戏生命周期
  - 运行三个并行循环（scan / timeout / agent）并执行 Agent 动作
  - **事件驱动决策**：`_agent_loop()` 由 `_agent_wakeup` 事件唤醒，空闲时 0.5s 兜底
  - **送餐验证重试**：`_serve_with_verify()` 带分段耗时打点（验证等待 / 截图 / 重试 / 清理）
  - **Action 调度**：`dispatch_batch()` 经共享 `schedule_actions` 排序/过滤后逐个 `_execute_action()`；
    已取消动作直接跳过（Ticket #11 / Spec #7 S3），与 SimEnv 回放语义一致
  - **异步扫描**：`_sync_orders_from_scan()` 为 async 方法，await scanner 的异步截图
- **输入**: 配置对象、配方列表
- **输出**: 游戏统计结果（含 scoring 统计与 execution timing）
- **关键类**: `Runner`

### `timing.py`
- **地位**: 执行耗时观测模块（Ticket #9 / Spec #7 S2）
- **功能**:
  - `SegmentTiming` / `ExecutionTiming`：分段耗时聚合（count / total / avg / max）
  - `Timer`：拉起即计时、record 落账的上下文助手
  - 只观测不改行为；供 Runner 送餐验证路径与 Operator 滑动往返打点

## 🔗 模块间关系

```
Runner (主控制器)
    ├── GameEnv (状态追踪)
    ├── Scanner (订单检测)
    ├── Operator (UI操作)
    └── Runner (决策逻辑)
```

## 数据流

```
Scanner.scan_orders()
    ↓
DetectedOrder
    ↓
GameEnv.add_order()
    ↓
Runner.step()
    ↓
Action
    ↓
Runner._execute_action()（已取消直接跳过，无 UI 调用、无状态变化）
    ↓
Operator.swipe()
    ↓
GameEnv状态更新
    ↓
Verifier.is_assembly_empty() (仅送餐后)
```

### 送餐验证流程

`_exec_serve_order()` 使用 `_serve_with_verify()` 执行带验证的送餐：

```
1. 执行 UI 送餐操作
2. 等待 0.5s（动画窗口）
3. Verifier.is_assembly_empty() 验证
   ├── 为空 → 成功 → env.serve_order() 更新状态
   └── 不为空 → 警告 → 重新扫描订单找匹配槽位
       ├── 找到匹配槽位 → 重试送餐（最多 2 次重试）
       └── 无匹配 → 重试原槽位
           └── 全部失败 → 清理组装站，继续游戏
```

**设计原则**：
- `env.serve_order()` 只在验证成功后调用，保证环境状态一致性
- 最多重试 2 次，避免无限循环
- 重试时重新扫描订单，处理订单槽位偏移问题
- 最终失败时清理组装站，防止卡死

### ⚠️ 订单同步动画窗口保护（2026-06-03 修复）

**问题**：`_sync_orders_from_scan()` 在动画窗口期间可能捕获到送餐前的旧帧（视觉上 slot 0 还能看到刚送走的订单，slot 1 是 RUSH），导致 ghost 防护失效或订单被错位放置。

**修复**：在 `_sync_orders_from_scan()` 开头和 `await scanner.scan_new_orders()` 之后双重检查 `is_in_animation_window()`，处于动画期则跳过本次 sync。

**设计权衡**：
- 动画期（1.5s）不 sync → env 保持 `_shift_orders_left` 后的内部状态
- 策略在此期间看到 env 内部状态（可能与屏幕视觉差 1.5s）
- 动画结束后下次 sync 重新对齐视觉与 env

**影响范围**：
- 动画期可能延迟发现 1 个新订单（最坏 1.5s 滞后）
- 消除了视觉/env 错位导致的 strategy 误判

## 得分系统（与模拟器对齐）

真实环境与模拟器使用**同一套** `RecipeRewardLookup` 计算得分，公式见 `docs/game_rules.md:194-226`：

```
order_score = (base_points + visibility) * multiplier(spawned_at_visibility, is_rush)
```

关键约束：**`spawned_at_visibility` 必须在订单生成瞬间锁定**（不是 serve 瞬间），因为倍率依赖该值，订单一旦生成其倍率就不再变化。

### 实现机制

1. **`Order.spawned_at_visibility`** (`core/models.py:Order`)：新增字段，`immutable since creation` —— 通过 `__setattr__` 重写阻止二次赋值，确保域语义「生成瞬间快照」不被破坏。
2. **`GameEnv.add_order()`**：每次新增订单时，把当前 `_scoring.total_visibility` 写入 `order.spawned_at_visibility`。
3. **`ScoringContext`** (`core/scoring.py`，S1 抽离)：统计查询、总分、终局结算的唯一持有者。`GameEnv` 只转发（`_orders_served/_total_score/_total_visibility` 为兼容属性），改计分只碰此处。
4. **`GameEnv.on_order_served(order, has_condiments)`**：
   - `score = RecipeRewardLookup.get_score(order.recipe_slug, has_condiments, order.is_rush, total_visibility=order.spawned_at_visibility)`
   - `_scoring.record_serve(score, get_visibility(...))`
5. **`GameEnv.finalize()`**（幂等）：`final_score = total_score + total_visibility`；`get_stats()` 同时返回 `total_score`（逐单和）、`total_visibility`、`final_score`、`scoring_version`。
6. **`Runner._exec_serve_order()` / `_exec_serve_from_cooker()`**：在 `env.serve_order` 清空 assembly 之前用 `env.assembly.condiments` 读取 `has_condiments`，然后调用 `env.on_order_served(order, has_condiments)`。甜点单食材直送（`serve_from_cooker`）时 `has_condiments=False`。
7. **`Runner._sync_orders_from_scan()`**：扫描新建订单时同样锁定 `spawned_at_visibility=_scoring.total_visibility`（与 `add_order` 同语义）；`run()` 返回前先 `finalize()`。

### 与模拟器的差异

| 方面 | 模拟器 | 真实环境 |
|------|--------|----------|
| spawned_at_visibility 存储 | `order.spawned_at_visibility`（域字段为准，`_order_visibility` 字典仅兼容回退） | `order.spawned_at_visibility`（域字段） |
| total_visibility 累加 | `state.total_visibility`（+ `_total_score` 逐单和） | `ScoringContext.total_visibility`（+ `total_score` 逐单和） |
| 终局 total_visibility 加分 | `finalize()` 幂等：`_total_score + state.total_visibility`（v2） | `finalize()` 幂等：`total_score + total_visibility`（v2） |

终局口径 `v2 = 逐单和 + 总 visibility`（`docs/game_rules.md:226`），`scoring_version` 随 Episode/基准 CSV/JSON 隔离展示（v1=历史逐单和口径）。

## 与模拟器的区别

| 方面 | 模拟器 (GameSimulator) | 真实环境 (GameEnv) |
|------|------------------------|---------------------------|
| 状态追踪 | 内置状态机 | 程序逻辑追踪 |
| 时间推进 | tick()方法 | 真实时间 |
| 操作执行 | 符号操作 | UI swipe操作 |
| 订单生成 | 随机生成 | 屏幕检测 |
| 食材过期 | expired_at 字段，强制阻止 | expired_at 字段，move_to_assembly/stockpile 拒绝过期 |
| 配方校验 | 模拟器内部校验 | add_to_assembly/add_condiment 校验目标配方 |
| 用途 | 测试、算法优化 | 实际游戏 |

---

## 🔄 三循环并行架构详解

### 架构概述

Runner 采用 **三循环并行架构**，通过 `asyncio.gather()` 同时运行三个独立的异步循环：
扫描循环、超时循环、决策循环。

```python
async def run(self) -> dict:
    # 1. 等待游戏开始
    await self._wait_for_game_start()

    # 2. 启动三个并行循环
    self._running = True
    try:
        await asyncio.gather(
            self._scan_loop(),      # 订单扫描循环
            self._timeout_loop(),   # 订单超时检测循环
            self._agent_loop(),     # Agent 决策循环（事件驱动）
        )
    except asyncio.CancelledError:
        pass
    finally:
        self._running = False

    # 3. 终局结算（幂等）后返回统计（含 total_score / total_visibility /
    #    final_score / scoring_version / timing）
    self.env.finalize()
    return self.env.get_stats()
```

### 扫描循环 (_scan_loop)

**职责**：检测屏幕上新出现的订单，更新环境状态

**频率**：自适应（0.4s / 0.5s），动画窗口期间暂停

```python
async def _scan_loop(self) -> None:
    """订单扫描循环（自适应频率），动画窗口期间暂停"""
    while self._running and not self.env.is_game_over():
        if self.env.is_in_animation_window():
            remaining = self.env.animation_window_remaining()
            if remaining > 0:
                await asyncio.sleep(remaining + 0.1)
            self._agent_wakeup.set()
            continue

        await self._sync_orders_from_scan()
        self._agent_wakeup.set()
        interval = self._compute_scan_interval()
        await asyncio.sleep(interval)
```

**自适应频率策略**（`_compute_scan_interval()`，实码为准）：

| 游戏状态 | 扫描间隔 | 原因 |
|----------|----------|------|
| 有活跃订单 + 有灶台空闲 | 0.4s | 快速发现新订单，立即启动烹饪 |
| 无活跃订单 | 0.5s | 中速等待新订单出现 |
| 所有灶台都在忙 | 0.5s | 等烹饪完成再处理 |

**关键点**：
- 使用 `Scanner` 进行图像检测
- 只检测订单，其他状态通过程序逻辑维护
- 在动画窗口期暂停扫描，睡到窗口结束后唤醒 agent
- **`_sync_orders_from_scan()` 是 async 方法**，内部 `await self.scanner.scan_new_orders()`
- **`G.DEVICE.snapshot()` 通过 `asyncio.to_thread()` 异步执行**，不阻塞 agent_loop
- **扫描不会重叠**：每次扫描完成后才计算间隔并 sleep

### 超时循环 (_timeout_loop)

**职责**：检查并移除超时订单，有变化时唤醒 agent

**频率**：每 0.3 秒

```python
async def _timeout_loop(self) -> None:
    """订单超时检测循环（每 0.3s），有变化时唤醒 agent"""
    while self._running and not self.env.is_game_over():
        timed_out = self.env.check_and_remove_timed_out_orders()
        if timed_out:
            for order_id in timed_out:
                self.env.on_order_timeout(order_id)
            self._agent_wakeup.set()
        await asyncio.sleep(0.3)
```

### 决策循环 (_agent_loop)

**职责**：调用 Strategy 决策，执行 Action，跳过动画窗口期间的送餐

**频率**：事件驱动（`_agent_wakeup` 事件唤醒；空闲时 0.5s 被动兜底检查）

```python
async def _agent_loop(self) -> None:
    """Agent 决策循环（事件驱动），只在有信息变化时唤醒"""
    while self._running and not self.env.is_game_over():
        if self._executing_action:
            await asyncio.wait_for(self._agent_wakeup.wait(), timeout=2.0)
            self._agent_wakeup.clear()
            continue

        try:
            await asyncio.wait_for(self._agent_wakeup.wait(), timeout=0.5)
            self._agent_wakeup.clear()
        except asyncio.TimeoutError:
            pass  # 0.5s 无事件：被动兜底唤醒，防止死锁

        in_animation = self.env.is_in_animation_window()
        action = self.strategy.decide(self.env.get_unified_state())
        if action:
            action_type = type(action).__name__
            if in_animation and action_type in (
                "ServeOrderAction", "ServeFromCookerAction"
            ):
                continue  # 动画窗口期间跳过送餐，允许烹饪继续

            self.env.on_action_taken()
            self._executing_action = True
            await self._execute_action(action)
            self._executing_action = False
            self._agent_wakeup.set()
```

**醒来事件**：扫描完成 / 动作执行完成 / 动画结束 / 订单超时 / 0.5s 兜底定时器，任一触发都会 `_agent_wakeup.set()`。

### 为什么采用三循环？

| 设计选择 | 原因 |
|----------|------|
| 扫描与决策分离 | 图像检测较慢（~100ms），不应阻塞决策 |
| 超时独立循环 | 超时判定是高频时间敏感项，独立 0.3s 轮询不干扰其他循环 |
| 决策事件驱动 | 只在状态变化时决策，避免空转浪费；兜底 0.5s 防止死锁 |
| 异步并发 | 避免 I/O 阻塞，最大化利用等待时间 |
| 动画窗口检查 | 扫描与决策各自检查动画窗口，双重保护防止冲突 |

---

## ⏱️ 时间推进机制

### 实时时间计算

GameEnv 使用 **系统时钟** 计算游戏时间，而非模拟 tick：

```python
@property
def time(self) -> float:
    """当前游戏时间（秒）"""
    if self._game_start_time is None:
        return 0.0
    return time.time() - self._game_start_time
```

### 游戏生命周期

```python
def start_game(self) -> None:
    """开始游戏计时"""
    self._game_start_time = time.time()
    logger.info("Game started!")

def is_game_over(self) -> bool:
    """游戏是否结束"""
    if self._game_start_time is None:
        return False
    return self.time >= self._game_duration  # 默认90秒
```

### 与模拟器 tick 的对比

| 方面 | 真实环境 (GameEnv) | 模拟器 (GameSimulator) |
|------|---------------------------|------------------------|
| 时间源 | `time.time()` 系统时钟 | `self._state.time` 模拟时间 |
| 推进方式 | 自动流逝 | 显式调用 `tick(dt)` |
| 精度 | 毫秒级 | 取决于 tick 间隔 |
| 确定性 | 非确定性（受系统影响） | 完全确定性 |
| 适用场景 | 真实游戏 | 测试和算法优化 |

### 为什么选择实时时间？

1. **简化设计**：不需要手动推进时间，系统自动处理
2. **真实同步**：与游戏实际时间保持同步
3. **减少错误**：避免 tick 间隔设置不当导致的时间跳跃
4. **自然超时**：订单超时、烹饪完成等事件自然触发

---

## 🛡️ 动画窗口保护机制

### 问题背景

送餐后游戏会播放 1.5 秒的动画（槽位前移），期间：
- 屏幕状态不稳定
- 操作可能导致冲突
- 订单检测可能误判

### 解决方案

```python
def is_in_animation_window(self) -> bool:
    """是否在动画窗口期间"""
    return time.time() < self._animation_until

def set_animation_window(self, duration: float = 1.5) -> None:
    """设置动画窗口"""
    self._animation_until = time.time() + duration
```

### 触发时机

`GameEnv.serve_order()` / `serve_from_cooker()` 内部调用 `set_animation_window(1.5)`；
Runner 的 `_exec_serve_from_cooker` / `_exec_serve_order`（`_serve_with_verify`）执行送餐后，
由 `env.serve_order()` 设置动画窗口。

### 保护范围

1. **扫描循环**：动画期间不检测新订单（防止捕获未刷新的屏幕），睡到窗口结束后唤醒 agent
2. **决策循环**（2026-04-19 优化）：动画期间**允许烹饪**，只**禁止送餐**
3. **Agent 决策**：`_agent_loop` 在动画窗口期间跳过 `ServeOrderAction` / `ServeFromCookerAction`

```python
# runner.py _agent_loop() 中的动画窗口保护
in_animation = self.env.is_in_animation_window()
action = self.strategy.decide(self.env.get_unified_state())
if action:
    action_type = type(action).__name__
    if in_animation and action_type in ("ServeOrderAction", "ServeFromCookerAction"):
        continue  # 跳过送餐，其他动作（烹饪/移动）正常执行
    # ... 执行动作
```

**优化原理**：根据 game_rules.md 规则，动画窗口期间"其他操作不受限"，烹饪是异步的可以让灶台提前工作。

---

## 🎯 状态追踪机制

### 核心设计原则

**只检测订单，其他状态通过程序逻辑维护**

| 状态类型 | 追踪方式 | 原因 |
|----------|----------|------|
| 订单 | 图像检测 | 订单内容变化不可预测 |
| 灶台 | 程序逻辑 | 状态变化由 Agent 控制 |
| 组装站 | 程序逻辑 | 状态变化由 Agent 控制 |
| 库存 | 程序逻辑 | 状态变化由 Agent 控制 |

### 灶台状态追踪

```python
@dataclass
class CookerState:
    """灶台状态"""
    busy: bool = False
    item_name: Optional[str] = None
    cooker_type: Optional[str] = None
    started_at: Optional[float] = None
    done_at: Optional[float] = None
    expired_at: Optional[float] = None

    def is_done(self, current_time: float) -> bool:
        return self.done_at is not None and current_time >= self.done_at

    def is_expired(self, current_time: float) -> bool:
        return self.expired_at is not None and current_time >= self.expired_at
```

**状态更新时机**：
- 开始烹饪时：`env.start_cooking(ingredient, cooker, duration)` → 设置 `done_at` 和 `expired_at`
- 移动食材时：`env.move_to_assembly(cooker)` → 过期食材被拒绝，返回 False
- 存入库存时：`env.move_to_stockpile(cooker, slot)` → 过期食材被拒绝，返回 False
- 清理灶台时：`env.clear_cooker(cooker)` → 重置所有字段

### 组装站状态追踪

```python
@dataclass
class AssemblyState:
    """组装站状态"""
    ingredients: list[str] = field(default_factory=list)
    target_recipe_slug: Optional[str] = None
    owner_order_id: Optional[int] = None
    condiments: dict[str, int] = field(default_factory=dict)
```

**状态更新时机**：
- 添加食材时：`env.add_to_assembly(ingredient, cooker, order_id, recipe_slug)`
- 从库存取用时：`env.pull_from_stockpile(slot)` — 若组装站为空，自动推断 `target_recipe_slug`
- 送餐时：`env.serve_order(slot_idx)` 内部清空组装站

**⚠️ target_recipe_slug 完整性保证**（2026-04-05 修复）：

`target_recipe_slug` 是组装站的"灵魂"字段，缺失会导致 Agent 决策链完全瘫痪。以下方法会自动推断配方：

| 方法 | 推断时机 | 推断依据 |
|------|----------|----------|
| `pull_from_stockpile()` | 组装站为空时拉取食材 | 单食材匹配活跃订单 |
| `add_to_assembly()` | 组装站有食材但无目标时 | 多食材集合匹配活跃订单 |
| `add_to_assembly()` | 组装站为空且无 order_id/recipe_slug | 单食材匹配活跃订单 |

推断方法：
- `_infer_recipe_slug_from_ingredient(ingredient)` — 单食材匹配第一个活跃订单
- `_infer_recipe_slug_from_ingredients(ingredients)` — 多食材集合匹配活跃订单

### 库存状态追踪

```python
@dataclass
class StockpileSlot:
    """库存槽位"""
    item_name: Optional[str] = None
    cooker_type: Optional[str] = None
    count: int = 0
```

**状态更新时机**：
- 存入时：`env.move_to_stockpile(cooker, slot)`
- 取出时：`env.pull_from_stockpile(slot)`

---

## 🔌 异步并发设计

### asyncio.gather() 的作用

```python
await asyncio.gather(
    self._scan_loop(),
    self._agent_loop(),
)
```

**效果**：
- 两个协程并发执行
- 任一协程异常会导致整体取消
- 最大化利用等待时间

### 锁机制

Operator 使用异步锁确保 UI 操作的原子性：

```python
class Operator:
    def __init__(self, ...):
        self._lock = asyncio.Lock()
    
    async def swipe(self, start, end, duration=0.1) -> None:
        async with self._lock:
            # 执行 swipe 操作
            await asyncio.to_thread(swipe, start, end, duration)
            await asyncio.sleep(0.05)  # 操作间隔
```

**作用**：
- 防止多个 swipe 操作同时执行
- 避免 UI 冲突和混乱

### 频率选择依据

| 循环 | 频率 | 依据 |
|------|------|------|
| 扫描循环 | 0.4s / 0.5s 自适应 | 订单变化较慢，图像检测开销大 |
| 超时循环 | 0.3s | 超时判定时间敏感，独立轮询 |
| 决策循环 | 事件驱动（0.5s 兜底） | 只在状态变化时决策，避免空转 |
| UI操作间隔 | 0.05s | 游戏需要时间响应操作 |

### 异常处理

每个循环都有独立的异常处理，确保单点故障不影响整体：

```python
while self._running and not self.env.is_game_over():
    try:
        # 主要逻辑
        ...
    except Exception as e:
        logger.error(f"Loop error: {e}")
        await asyncio.sleep(0.5)  # 错误后等待再重试
```

---

## 📊 完整游戏流程

```
1. 初始化阶段
   ├── 创建 Runner
   ├── 初始化 GameEnv, Scanner, Operator
   └── 创建 Runner 并注入 env

2. 等待游戏开始
   ├── Scanner.detect_timer() 循环检测
   ├── 检测到 timer 图标后等待 3 秒
   └── 调用 env.start_game() 开始计时

3. 游戏运行阶段（三循环并行）
   ├── 扫描循环 (0.4s/0.5s 自适应)
   │   ├── 检查动画窗口（暂停到窗口结束）
   │   ├── Scanner.scan_new_orders()
   │   └── GameEnv 订单同步 / add_order()
   │
   ├── 超时循环 (0.3s)
   │   └── GameEnv.check_and_remove_timed_out_orders()
   │
   └── 决策循环 (事件驱动)
       ├── 等待 _agent_wakeup 事件
       ├── Strategy.decide() → Action
       └── Runner._execute_action()
           ├── UI 操作 (Operator.swipe)
           └── 状态更新 (GameEnv)

4. 游戏结束
   ├── env.is_game_over() 返回 True
   ├── 三循环自动退出
   └── 终局结算 + 返回统计（含 scoring 与 timing）
```
