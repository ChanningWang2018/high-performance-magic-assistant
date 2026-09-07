# src/hawarma Directory Architecture

## 📁 目录概述

此目录是 Hawarma 项目的核心，包含 Agent 自动化烹饪游戏的全部逻辑。

## ⚠️ 重要提示

**一旦本目录有变化（新增/删除/重命名文件），请立即更新本文档！**

## 📄 文件列表

### `__init__.py`
- **地位**: 包初始化文件

### `__main__.py`
- **地位**: `python -m hawarma` 入口点
- **功能**: 调用 `cli.py:main()`

### `cli.py`
- **地位**: CLI 入口
- **功能**: 命令行界面，配方选择，游戏循环

### `tui.py`
- **地位**: TUI 入口
- **功能**: Textual 图形界面，配方选择、配置、游戏控制

### `config.py`
- **地位**: 配置管理模块
- **功能**: 从 YAML 文件加载配置，Pydantic 验证，保存配置

### `paths.py`
- **地位**: 项目路径解析模块
- **功能**: 统一计算项目根目录，提供资源路径查找（CWD 优先，`__file__` 回退）

### `device.py`
- **地位**: 设备连接模块
- **功能**: ADB 设备初始化和连接

### `log.py`
- **地位**: 日志配置模块
- **功能**: 使用 loguru 配置终端 + 文件日志

### `patches.py`
- **地位**: 兼容性补丁模块
- **功能**: 修复 airtest 框架的兼容性问题

### `recipe.py`
- **地位**: 配方数据模型
- **功能**: Recipe、Station 等数据结构定义

### `core/` 子目录
- **地位**: 数据层，定义 Action、State、Model、Reward
- **文件**:
  - `actions.py`: 所有 Action 定义（按 station 分组）
  - `models.py`: CookerState、AssemblyState、MixingBowlState、StockpileSlot、Order
  - `state.py`: UnifiedState，env → strategy 的数据契约
  - `reward.py`: RecipeRewardLookup、RecipeTimeoutLookup，精确分数和超时查表
  - `__init__.py`: 导出所有类型

### `agent/` 子目录
- **地位**: Agent 策略注册 + Strategy 基类
- **文件**:
  - `strategy.py`: Strategy ABC（策略接口）
  - `registry.py`: 策略注册表（工厂模式，按名称获取策略实例）

### `agent/strategies/` 子目录
- **地位**: 策略实现
- **文件**:
  - `gastronome.py`: GastronomeStrategy，美食站唯一策略 — 10 级贪心瀑布 + CPM + visibility 阈值跨越 + 单食材优先 + 延迟感知（用户级：gastronome）
  - `dessert.py`: DessertStrategy，甜点策略，搅拌盆流水线决策逻辑

### `game/` 子目录
- **地位**: Agent 与真实游戏的桥接层
- **功能**: 扫描、状态追踪、UI 执行、生命周期管理
- **文件**:
  - `runner.py`: Runner，三循环并行架构（scan + timeout + agent）
  - `game_env.py`: GameEnv，独立类（不再继承 ABC），程序逻辑追踪状态
  - `scanner.py`: Scanner，图像检测订单（食材、rush 检测、冲突解决）
  - `operator.py`: Operator，swipe 坐标映射和执行
  - `verifier.py`: Verifier，组装站清空验证
  - `timing.py`: ExecutionTiming，执行耗时观测（分段聚合，只观测不改行为）
  - `env.py`: 模块文档（真实环境和模拟环境通过 UnifiedState + Action 共享数据契约，不定义 ABC）
  - `patch_maxtouch.py`: Maxtouch swipe 补丁

### `services/` 子目录
- **地位**: 服务层
- **文件**:
  - `recipe_manager.py`: 从 JSON 加载配方数据，按 slug 查询

### `i18n/` 子目录
- **地位**: 多语言支持（TUI / CLI 界面文案翻译）
- **文件**:
  - `translator.py`: Translator 类 + 全局翻译器，key→文本 + `{var}` 插值
  - `locales/zh-CN.json`, `locales/en-US.json`: 翻译目录（zh-CN 默认）
- **约定**: 只翻译 UI 文案，引擎日志（runner/agent 的 loguru 输出）保持英文；
  数据标识符 slug 不翻译，仅通过 `display.*` 映射显示名
- **入口**: `config.language` 指定语言；TUI 主菜单可运行时切换

### `utils/` 子目录
- **地位**: 工具函数
- **文件**:
  - `image_utils.py`: 本地模板匹配辅助函数

---

## 🔗 架构

```
cli.py / tui.py
  ↓
Runner (game/runner.py)
  │
  ├─ 三个并行循环:
  │   ├─ scan_loop (自适应 0.4s/0.5s) → Scanner → GameEnv.add_order()
  │   ├─ timeout_loop (0.3s)         → GameEnv.check_and_remove_timed_out_orders()
  │   └─ agent_loop (事件驱动)        → Strategy.decide() → _execute_action()
  │
  ├─ GameEnv (game/game_env.py)
  │     └─ 程序逻辑追踪: 灶台、组装站、搅拌盆、库存、订单、调料
  │
  ├─ Scanner (game/scanner.py)
  │     └─ Airtest 图像检测: 只检测订单
  │
  ├─ Operator (game/operator.py)
  │     └─ swipe 坐标映射和执行
  │
  └─ Strategy (agent/strategies/)
        ├─ GastronomeStrategy       (美食站：10 级贪心瀑布 + CPM + visibility + 单食材 + 延迟感知)
        └─ DessertStrategy          (甜点站：搅拌盆流水线)
```

### Agent 决策优先级（Gastronome）

```
1. 送餐 (ServeOrderAction)        ← 组装完成立即送
2. 移到组装站 (MoveToAssemblyAction) ← 完成烹饪尽快移走
3. 开始烹饪 (CookAction)          ← 尽早开始，让灶台异步工作
4. 添加调料 (AddCondimentAction)   ← 可在烹饪期间进行
5. 从库存取用 (PullFromStockpileAction) ← 库存有则直接用
6. 清理过期 (ClearCookerAction)    ← 5s 未取走则清理
7. 存入库存 (MoveToStockpileAction) ← 多余食材缓冲
```

### Agent 决策优先级（Dessert）

```
1. 送餐 (ServeFromCookerAction)    ← 灶台完成立即送
2. 清理过期 (ClearCookerAction)    ← 灶台过期清理
3. 移动搅拌盆到灶台 (MoveMixingBowlToCookerAction) ← 搅拌完成 + 灶台空闲
4. 搅拌 (StirAction)              ← 食材齐全 + 调料齐全 + 未搅拌
5. 添加调料 (AddCondimentAction)   ← 食材齐全 + 调料未齐全
6. 添加食材到搅拌盆 (MoveToMixingBowlAction) ← 搅拌盆未满
7. 清理搅拌盆 (ClearMixingBowlAction) ← 无匹配订单
```

### 数据流

```
屏幕截图
  ↓
Scanner.scan_new_orders()
  ↓
GameEnv.add_order()
  ↓
Runner.step()  ← 按优先级选最优动作
  ↓
Action
  ↓
Runner._execute_action()
  ├─→ Operator.swipe()        ← 执行 UI 操作
  └─→ GameEnv.*()      ← 更新内部状态
```

---

## 🚀 运行

```bash
python -m hawarma        # CLI 界面
python -m hawarma.tui    # TUI 图形界面
```

选择配方后自动运行游戏，结束后显示统计。
日志输出到 `logs/game_YYYYMMDD_HHmmss.log`。

### 路径解析

所有资源路径（配置、数据、图片、日志）通过 `paths.py` 统一解析：
- 优先使用 CWD 相对路径（git-clone 模式天然正确）
- CWD 下找不到时回退到包安装路径（`__file__` 计算）
- `config.py`、`log.py`、`reward.py`、`recipe_manager.py`、`scanner.py`、`verifier.py` 均通过此模块获取路径