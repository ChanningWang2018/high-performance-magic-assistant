# 真实游戏交互实现文档

> 本文档梳理当前与真实游戏交互的技术栈、数据结构、算法和性能优化策略。
> 供后续讨论如何进一步提升游戏性能。

---

## 1. 技术栈概览

| 类别 | 技术/库 | 用途 |
|------|---------|------|
| **UI 自动化** | Airtest (Poco) | 屏幕截图、图像识别、触摸操作 |
| **截图方法** | MinicapApk | 通过 APK 进行高效屏幕截图 |
| **触控方法** | Maxtouch | Android 10+ 高性能触摸协议 |
| **并发框架** | asyncio | 三循环并行（scan/timeout/agent）、异步操作 |
| **数据验证** | Pydantic | 配置加载、类型安全 |
| **日志系统** | loguru | 结构化日志、彩色输出 |
| **配置管理** | YAML | config.yaml 统一配置管理 |
| **图像匹配** | Template Matching (Airtest) | 订单检测、组装站验证 |

### 1.1 设备方法检测

程序启动时会自动检测并记录使用的截图和触控方法：

```python
# 截图方法检测
screen_class = type(device.screen_proxy.screen_method).__name__  # MinicapApk

# 触控方法检测  
touch_class = type(device.touch_proxy.touch_method.base_touch).__name__  # Maxtouch
```

日志输出示例：
```
Screenshot method: MinicapApk
Touch method: Maxtouch
```

### 1.2 延迟初始化

MinicapApk 采用延迟初始化策略：
- 第一次调用 `snapshot()` 时才建立 stream 连接
- 避免在程序初始化时预热导致帧缓冲区积累
- 减少截图延迟累积问题

---

## 2. 核心数据结构

### 2.1 配置层 (config.py + config.yaml)

```python
AppConfig
├── image_directory: str           # 图像资源目录
├── log_directory: str             # 日志输出目录
├── recipes_data_path: str        # 配方数据路径
├── episode_duration: int         # 游戏时长 (90s, 可配置)
├── cookers: tuple[str]            # 可用灶台 ["grill","oven","skillet","pot"]
├── screen: ScreenConfig           # 屏幕坐标配置
│   ├── raw_ingredients_positions  # 食材区坐标 (动态映射)
│   ├── cookers_positions         # 灶台区坐标
│   ├── condiments_positions      # 调料区坐标
│   ├── orders_regions            # 订单区域 ROI
│   ├── pickup_stations_positions  # 取餐台坐标
│   └── trash_position            # 垃圾桶坐标
├── matching: MatchingConfig      # 图像匹配配置
└── device: DeviceConfig          # 设备配置 (minitouch开关)
```

**动态坐标映射规则** (见 `docs/game_rules.md` 第2节):
- **灶台**: 按菜谱顺序收集 `cookers_layout` 并去重，根据数量选择槽位
- **食材**: 收集 `raw_ingredients` 并去重，**反转顺序**后分配索引
- **调料**: 收集 `condiments` 并去正序分配索引

### 2.2 环境状态层 (game_env.py)

```python
GameEnv(Env)
├── orders: list[OrderInfo | None]      # 4个订单槽位
├── cookers: dict[str, CookerState]      # 灶台状态
├── assembly: AssemblyState              # 组装站状态
├── stockpile: dict[str, StockpileSlot]  # 3个库存槽位
├── _game_start_time: float | None       # 游戏开始时间
├── _game_duration: float               # 游戏时长 (默认90s)
└── _animation_until: float              # 动画窗口截止时间
```

```python
@dataclass
class CookerState:
    busy: bool                           # 是否正在烹饪
    ingredient_name: str | None         # 食材名称
    cooker_type: str | None             # 灶台类型
    started_at: float | None           # 开始时间
    done_at: float | None              # 烹饪完成时间
    expired_at: float | None           # 过期时间 (5秒后)
    
    def is_done(self, current_time: float) -> bool:
        """检查烹饪是否已完成"""
        return self.done_at is not None and current_time >= self.done_at
    
    def is_expired(self, current_time: float) -> bool:
        """检查食材是否已过期"""
        return self.expired_at is not None and current_time >= self.expired_at
```

```python
@dataclass
class AssemblyState:
    ingredients_cookers: list[tuple[str, str]] = field(default_factory=list)  # (食材, 灶台) 列表
    target_recipe_slug: str | None         # 目标配方 (关键!)
    owner_order_id: int | None            # 关联订单
    condiments: dict[str, int]           # 调料计数
    
    @property
    def is_free(self) -> bool:
        """组装站是否空闲"""
        return len(self.ingredients_cookers) == 0 and self.target_recipe_slug is None
```

```python
@dataclass
class OrderInfo:
    """订单信息（统一数据结构）"""
    order_id: int
    recipe_slug: str
    is_rush: bool
    created_at: float
    timeout_at: float
    done: bool = False
```

```python
@dataclass
class CookerState:
    busy: bool                           # 是否正在烹饪
    ingredient_name: str | None         # 食材名称
    cooker_type: str | None             # 灶台类型
    started_at: float | None           # 开始时间
    done_at: float | None              # 烹饪完成时间
    expired_at: float | None           # 过期时间 (5秒后)

@dataclass
class AssemblyState:
    ingredients: list[str]              # 已添加食材
    target_recipe_slug: str | None     # 目标配方 (关键!)
    owner_order_id: int | None         # 关联订单
    condiments: dict[str, int]         # 调料计数

@dataclass  
class Order:
    id: int
    recipe_slug: str
    created_at: float
    is_rush: bool
    done: bool = False
```

### 2.3 检测层 (scanner.py)

```python
@dataclass
class DetectedOrder:
    slot_idx: int        # 槽位索引 (0-3)
    recipe_slug: str     # 配方标识
    is_rush: bool        # 是否加急
    confidence: float    # 匹配置信度
```

---

## 3. 核心算法

### 3.1 订单检测算法

**流程**:
1. 截图 (async via `asyncio.to_thread`)
2. 对每个 slot ROI 进行模板匹配
3. 遍历所有 recipe，取置信度最高者
4. 检测 rush 状态 (像素红色值 < 180)

**复杂度**: O(4 × R), R = 配方数量

### 3.2 动态扫描频率算法

在 `runner.py` 中实现：

```python
def _compute_scan_interval(self) -> float:
    active_orders = [o for o in self.env.orders if o and not o.done]
    free_cookers = [c for c in self.env.cookers.values() if not c.busy]
    
    if not active_orders:
        return 0.5   # 无订单 → 中速
    if free_cookers:
        return 0.4   # 有订单且有空闲灶台 → 快扫
    return 0.5       # 灶台全忙 → 中速
```

| 游戏状态 | 扫描间隔 | 原因 |
|----------|----------|------|
| 有活跃订单 + 有空闲灶台 | 0.4s | 快速发现新订单 |
| 无活跃订单 | 0.5s | 等待新订单 |
| 所有灶台都忙 | 0.5s | 等烹饪完成再处理 |

### 3.3 送餐验证 + 快速重试算法

在 `runner.py` 中实现，使用快速重试机制（不依赖扫描），沿途对分段耗时打点
（Ticket #9，只观测不改行为）：

```
_serve_with_verify(slot_idx):
    1. 执行 UI 送餐操作 (Operator.serve_order，swipe 自身由 Operator 计时)
    2. 等待 0.2s 动画渲染            → serve_verify_wait 段计时
    3. 多点 snapshot 验证             → serve_verify_snapshot 段计时
       验证通过 → 返回 slot_idx
    4. 验证失败 → 重试相邻左侧 slot (slot_idx - 1)   → serve_retry 段计时
       等待 0.05s → 再次 serve → 等待 0.2s → 多点 snapshot 验证
       成功 → 返回 retry_slot
    5. 两次都失败 → clear_assembly 丢弃菜品   → serve_cleanup 段计时
       返回 None
```

**关键设计**：
- 不用扫描匹配订单，只重试相邻左侧一个 slot（处理槽位左移错位）
- 多点 snapshot 避免单次截图的时效性问题（`_verify_with_multi_snapshot` 连取 3 张 warm-up 帧后，
  用第 4 次 `Verifier.is_assembly_empty()` 的模板匹配结果判定）
- 无需检查动画窗口（直接使用 UI 操作结果）
- 分段耗时（验证等待 / 截图 / 重试 / 清理）汇总进 Runner 的 `execution timing`

### 3.4 食材过期检测

在 `game_env.py` 的 `add_to_assembly()` 方法中：

```python
def add_to_assembly(self, cooker_name: str) -> bool:
    cooker_state = self._cookers[cooker_name]
    
    # 检查是否过期
    if cooker_state.is_expired(self.time):
        logger.warning(f"拒绝过期食材: {cooker_name}")
        return False
    
    # 检查组装站是否可以接受
    if not self._can_add_to_assembly(cooker_state.ingredient_name, cooker_state.cooker_type):
        return False
    
    # 添加到组装站...
```

**关键设计**: 过期食材被 **程序逻辑拒绝**，而非依赖 UI 反馈。

---

## 4. 与 game_rules.md 的对应关系

| 游戏规则 | 实现方式 | 性能影响 |
|----------|----------|----------|
| **订单刷新间隔 4s** | 扫描循环 + 环境状态追踪 | 自适应频率减少无效扫描 |
| **Rush 订单时限 40s** | Order.is_rush + 过期检测 | 程序逻辑判断 |
| **普通订单时限 70s** | 同上 | 同上 |
| **烹饪完成后 5s 过期** | CookerState.expired_at | 拒绝过期食材操作 |
| **灶台并行烹饪** | GameEnv 维护多个 CookerState | 完全并行，无冲突 |
| **组装站单一配方** | AssemblyState.target_recipe_slug 校验 | 校验失败则拒绝添加 |
| **订单位移动画 1.5s** | set_animation_window() | 两个循环都检查动画窗口 |
| **游戏时长 90s** | env.is_game_over() | 动态可配置 |
| **食材区/调料区动态位置** | Operator 动态坐标映射 | 与游戏界面一致 |
| **最多 4 个订单** | 扫描检测 + 环境维护 | 检测到则添加 |

---

## 5. 性能优化策略

### 5.1 已实施的优化

| 优化项 | 效果 |
|--------|------|
| **Maxtouch 加速** | Android 10+ 高性能触摸，swipe 从 ~0.93s → ~0.1s |
| **MinicapApk 延迟初始化** | 第一次 snapshot 时才建立 stream，避免帧缓冲区积累 |
| **异步截图** | `asyncio.to_thread(G.DEVICE.snapshot)` 不阻塞事件循环 |
| **自定义扫描频率** | 根据灶台/订单状态动态调整：0.4s (空闲灶台) / 0.5s (其他) |
| **三循环并行** | scan / timeout / agent 三个循环独立运行，互不阻塞 |
| **动画窗口检查** | agent_loop 只禁止送餐，允许烹饪继续 |
| **UI 操作锁** | asyncio.Lock() 防止并发 swipe 冲突 |
| **多点snapshot验证** | 送餐后连续3次截图，避免单次截图时效性问题 |
| **快速重试机制** | 送餐失败依次尝试所有slot，无需扫描匹配 |

### 5.2 已实现或无需优化的方向

1. **模板匹配优化** ✅（已足够快）
   - 当前：对每个 slot 遍历所有 recipe
   - 实际性能：~30-50ms/slot，已满足需求

2. **状态缓存** ✅（已实现）
   - 环境状态由 GameEnv 维护，无需重复计算
   - Agent 通过属性访问状态，高效且一致

3. **预测性烹饪** ❌（无效优化）
   - 问题：预烹饪食材可能与订单不匹配
   - 教训：按需响应策略已经足够好（见 agent_strategy.md）

4. **多级验证** ✅（已实现）
   - 操作前：状态检查（动画窗口、过期检查）
   - 操作中：UI 操作执行
   - 操作后：多点 snapshot 验证

---

## 6. 数据流总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        真实游戏屏幕                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │ snapshot (async)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Scanner (scan_orders)                                     │
│  ├── ROI 模板匹配 (食材图标)                                    │
│  ├── Rush 检测 (像素红色值)                                      │
│  └── 返回: list[DetectedOrder]                                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ add_order()
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  GameEnv                                                │
│  ├── 订单状态维护 (orders: list[OrderInfo])                      │
│  ├── 灶台状态 (cookers: dict[str, CookerState])              │
│  ├── 组装站状态 (assembly: AssemblyState)                        │
│  ├── 库存状态 (stockpile: dict[str, StockpileSlot])          │
│  └── 时间/动画窗口管理                                         │
└──────────────────────────┬──────────────────────────────────────┘
                           │ step() / step_with_diagnostics()
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  CookingAgent                                                   │
│  ├── 订单优先级排序 (_prioritized_orders)                       │
│  ├── 动作生成 (7级优先级贪婪策略)                             │
│  ├── 停滞检测 (_consecutive_none)                              │
│  └── 返回: Action | None                                      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ _execute_action()
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Runner                                                 │
│  ├── 校验 (过期食材、动画窗口、游戏结束)                         │
│  ├── Operator.swipe() → maxtouch                              │
│  └── 状态更新 (env.add_to_assembly, env.serve_order)           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ is_assembly_empty()
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Verifier                                               │
│  ├── 截图 (assembly ROI)                                        │
│  └── 模板匹配 (empty_assembly.jpg)                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. 关键设计决策

1. **只检测订单，其他状态程序维护**
   - 原因: 订单来自屏幕，变化不可预测；灶台/组装站/库存由 Agent 控制
   - 效果: 减少图像检测开销，提高响应速度

2. **三循环并行架构**
   - 扫描循环 (0.4s/0.5s 自适应): 订单变化慢，低频检测
   - 超时循环 (0.3s): 订单超时时间敏感，独立轮询
   - 决策循环 (事件驱动): 只在状态变化时被唤醒决策，0.5s 兜底
   - 效果: 平衡检测开销和响应延迟

3. **实时时间 vs 模拟 tick**
   - 选择: 实时时间 (time.time())
   - 原因: 与游戏实际时间同步，简化超时处理

4. **目标配方推断机制**
   - 问题: pull_from_stockpile() 可能使 assembly.target_recipe_slug 为 None
   - 解决: 多处自动推断逻辑，防止决策链断裂

---

## 8. 待讨论问题

1. **预测性烹饪的可行性**: 基于历史订单预测下一步需求，提前烹饪高概率食材
2. **多灶台协同优化**: 多个灶台同时烹饪时的任务分配策略
3. **库存利用率提升**: 何时存入/取出库存以最大化效率
4. **Rush 订单优先级**: 是否应该为 rush 订单调整整个任务队列
5. **组装站批量操作**: 能否在食材烹饪完成前预判并规划组装路径