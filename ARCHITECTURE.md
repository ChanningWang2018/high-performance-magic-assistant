# Hawarma 项目架构

> **上下文索引**：本文档是项目的入口索引。需要深入了解任何模块时，请先阅读对应的子目录 `ARCHITECTURE.md` 文件获取完整上下文。

## 🗺️ 上下文地图

| 上下文 | 文件 | 何时阅读 |
|--------|------|----------|
| **核心代码** | [`src/hawarma/ARCHITECTURE.md`](src/hawarma/ARCHITECTURE.md) | 修改核心逻辑、理解数据流和架构 |
| **Agent 决策** | [`src/hawarma/agent/ARCHITECTURE.md`](src/hawarma/agent/ARCHITECTURE.md) | 修改 Agent 策略、动作类型、优先级 |
| **桥接层** | [`src/hawarma/game/ARCHITECTURE.md`](src/hawarma/game/ARCHITECTURE.md) | 修改 UI 操作、状态追踪、扫描器、三循环架构 |
| **服务层** | [`src/hawarma/services/ARCHITECTURE.md`](src/hawarma/services/ARCHITECTURE.md) | 修改配方管理等服务组件 |
| **工具函数** | [`src/hawarma/utils/ARCHITECTURE.md`](src/hawarma/utils/ARCHITECTURE.md) | 修改图像处理工具 |
| **文档** | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 查找游戏规则、设计文档、模拟器文档 |
| **测试** | [`tests/ARCHITECTURE.md`](tests/ARCHITECTURE.md) | 添加或修改测试 |
| **实验** | [`experiments/ARCHITECTURE.md`](experiments/ARCHITECTURE.md) | 运行基准测试、查看实验记录 |

## 📁 目录结构

```
hawarma/
├── src/
│   └── hawarma/       # 核心代码
│       ├── agent/      # Agent 决策逻辑
│       ├── game/     # 桥接层（UI 操作、状态追踪、扫描器）
│       ├── services/   # 服务层（配方管理等）
│       └── utils/      # 工具函数（图像处理）
├── playground/        # RL 模拟与策略验证环境
├── experiments/       # 实验代码和报告
├── docs/              # 文档（游戏规则、策略设计、架构说明）
├── configs/           # 配置文件
├── data/              # 数据文件（recipes.json）
├── static/            # 静态资源（模板图片、APK）
├── tests/             # 测试代码
├── logs/              # 运行日志
└── AGENTS.md          # 上下文索引（入口文档）
```

## 📄 关键目录说明

### `src/hawarma/` — 核心代码
- **地位**: Agent 自动化烹饪游戏的全部逻辑
- **架构**: `cli.py` → `Runner` → 三循环并行（scan_loop + timeout_loop + agent_loop）
- **深入阅读**: [`src/hawarma/ARCHITECTURE.md`](src/hawarma/ARCHITECTURE.md)

### `docs/` — 项目文档
- **地位**: 游戏规则、策略设计、架构说明
- **关键文件**:
  - `game_rules.md` — 游戏规则（唯一依据）
  - `agent_strategy.md` — Agent 策略和实验结果
  - `agent_architecture.md` — Agent 架构设计
- **深入阅读**: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

### `tests/` — 测试代码
- **地位**: 单元测试和集成测试
- **深入阅读**: [`tests/ARCHITECTURE.md`](tests/ARCHITECTURE.md)

### `experiments/` — 实验代码和报告
- **地位**: 实验代码和报告
- **结构**: 每个实验一个子目录，包含 `design.md`、`script.py`、`report.md`
- **现有实验**: `parallel_strategy/`、`quick_serve/`
- **深入阅读**: [`experiments/ARCHITECTURE.md`](experiments/ARCHITECTURE.md)

### `configs/` — 配置文件
- `config.yaml` — YAML 配置，通过 `src/hawarma/config.py` 加载

### `data/` — 数据文件
- `recipes.json` — 配方数据

### `static/` — 静态资源
- `img/` — 模板图片（`ingredient-{name}.jpg`、`icon-{name}.jpg`）
- `apk/` — APK 文件

## ⚠️ 重要原则

**文档优先原则**：
- 把 `docs/` 作为真实信息源
- 思考问题从文档出发
- 代码与文档保持一致

详见 `AGENTS.md`
