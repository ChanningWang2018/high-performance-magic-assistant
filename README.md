# Hawarma - 烹饪游戏自动化 Agent

自动识别订单、管理烹饪流水线、最优策略配菜的自动化机器人。

[![中文](https://img.shields.io/badge/lang-中文-red.svg)](README.md)
[![English](https://img.shields.io/badge/lang-English-blue.svg)](README.en.md)

## 快速开始

### 1. 安装 Git

下载并安装 **Git**：https://git-scm.com/downloads

> Windows 安装时保持默认选项即可。Mac 可用 `brew install git`，Linux 可用 `apt install git`。

### 2. 安装 Python

下载并安装 **Python 3.10+**：https://www.python.org/downloads/

> Windows 安装时请勾选 **"Add Python to PATH"**

### 3. 下载项目

```bash
git clone https://github.com/hpma-bits/hawarma.git
cd hawarma
```

### 4. 一键安装

**Windows:**
```bash
setup.bat
```

**Mac/Linux:**
```bash
chmod +x setup.sh
./setup.sh
```

### 5. 启动

**方式一：双击脚本**
- Windows: `run.bat`
- Mac/Linux: `./run.sh`

**方式二：命令行**
```bash
# TUI 图形界面（推荐）
python -m hawarma.tui

# CLI 命令行界面
python -m hawarma
```

## 前置要求

- **Python 3.10+**
- **Git**
- **Android 模拟器**（如 MuMu、雷电、夜神）
- 模拟器需开启 ADB 调试，默认连接地址 `127.0.0.1:16384`
- 游戏已安装在模拟器中

## 使用方式

### TUI 仪表板（推荐）

```bash
python -m hawarma.tui
```

提供完整的图形界面：
- 配方选择
- 配置面板
- 游戏控制（开始/暂停/停止）
- 实时日志

### CLI 命令行

```bash
python -m hawarma                    # 默认策略
python -m hawarma --strategy cpm     # 指定策略
python -m hawarma --station dessert  # 甜点站
```

### 策略列表

| 策略名 | 说明 |
|--------|------|
| `gastronome` | CPM 增强瀑布策略（推荐） |
| `dessert` | 甜点站策略 |
| `default` | 默认策略 |

### 模拟器基准测试（无需设备）

```bash
python -m playground run --seed 42
python -m playground bench --games 50 --strategies gastronome,dessert
```

## 项目结构

```
hawarma/
├── configs/config.yaml    # 配置文件（屏幕坐标、策略参数等）
├── data/                  # 游戏数据（配方、分数表）
├── static/img/            # 模板图片
├── src/hawarma/           # 核心代码
│   ├── cli.py             # CLI 入口
│   ├── tui.py             # TUI 入口
│   ├── config.py          # 配置管理
│   ├── paths.py           # 路径解析
│   ├── agent/             # 策略引擎
│   ├── game/              # 游戏桥接（扫描、操作、验证）
│   ├── core/              # 数据模型
│   └── services/          # 配方管理等
├── playground/            # 模拟器基准测试
├── tests/                 # 单元测试
└── setup.bat / setup.sh   # 一键安装脚本
```

## 配置

编辑 `configs/config.yaml` 修改：
- ADB 连接地址
- 屏幕分辨率和坐标
- 匹配阈值
- 策略参数

## 运行测试

```bash
python -m unittest discover tests
```

## 许可证

MIT License
