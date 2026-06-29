# Hawarma - Cooking Game Automation Agent

An automation bot that recognizes orders, manages cooking pipelines, and optimizes recipe strategies.

[![中文](https://img.shields.io/badge/lang-中文-red.svg)](README.md)
[![English](https://img.shields.io/badge/lang-English-blue.svg)](README.en.md)

## Quick Start

### 1. Install Git

Download and install **Git**: https://git-scm.com/downloads

> On Windows, use the default options. On Mac: `brew install git`. On Linux: `apt install git` or `yum install git`.

### 2. Install Python

Download and install **Python 3.10+**: https://www.python.org/downloads/

> On Windows, check **"Add Python to PATH"** during installation.

### 3. Clone the Project

```bash
git clone https://github.com/hpma-bits/hawarma.git
cd hawarma
```

### 4. One-Click Setup

**Windows:**
```bash
setup.bat
```

**Mac/Linux:**
```bash
chmod +x setup.sh
./setup.sh
```

> **One-click setup failed?** Follow these manual steps:
>
> ```bash
> # 1. Create virtual environment
> python -m venv .venv
>
> # 2. Activate it
> # Windows:
> .venv\Scripts\activate
> # Mac/Linux:
> source .venv/bin/activate
>
> # 3. Install dependencies (uv is faster)
> pip install uv
> uv pip install -e .
> # If uv fails, fall back to pip:
> pip install -e .
> ```

### 5. Launch

**Option 1: Double-click the script**
- Windows: `run.bat`
- Mac/Linux: `./run.sh`

**Option 2: Command Line**
```bash
# TUI Dashboard (Recommended)
python -m hawarma.tui

# CLI
python -m hawarma
```

## Prerequisites

- **Python 3.10+**
- **Git**
- **Android Emulator** (e.g., MuMu, LDPlayer, Nox)
- Enable ADB debugging on the emulator (default address `127.0.0.1:16384`)
- Game must be installed on the emulator

## Usage

### TUI Dashboard (Recommended)

```bash
python -m hawarma.tui
```

Full graphical interface:
- Recipe selection
- Configuration panel
- Game controls (Start/Pause/Stop)
- Real-time logs

### CLI

```bash
python -m hawarma                      # Default strategy
python -m hawarma --strategy cpm       # Specify strategy
python -m hawarma --station dessert    # Dessert station
```

### Strategies

| Strategy | Description |
|----------|-------------|
| `gastronome` | CPM Enhanced Cascade (Recommended) |
| `dessert` | Dessert Station |
| `default` | Default |

### Simulator Benchmark (No Device Needed)

```bash
python -m playground run --seed 42
python -m playground bench --games 50 --strategies gastronome,dessert
```

## Project Structure

```
hawarma/
├── configs/config.yaml    # Config (screen coords, strategy params, etc.)
├── data/                  # Game data (recipes, score tables)
├── static/img/            # Template images
├── src/hawarma/           # Core code
│   ├── cli.py             # CLI entry
│   ├── tui.py             # TUI entry
│   ├── config.py          # Config management
│   ├── paths.py           # Path resolution
│   ├── agent/             # Strategy engine
│   ├── game/              # Game bridge (scan, action, verify)
│   ├── core/              # Data models
│   └── services/          # Recipe management, etc.
├── playground/            # Simulator benchmarks
├── tests/                 # Unit tests
└── setup.bat / setup.sh   # One-click setup
```

## Configuration

Edit `configs/config.yaml` to modify:
- ADB connection address
- Screen resolution and coordinates
- Matching thresholds
- Strategy parameters

## Running Tests

```bash
python -m unittest discover tests
```

## License

MIT License
