# src/hawarma/i18n/ 目录架构

## 📁 目录职责

多语言支持模块。为 UI 层（TUI / CLI）提供基于 JSON 目录的轻量级
key → 文本 翻译服务，支持 `{var}` 模板插值。

## ⚠️ 重要提示

**一旦本目录有变化（新增/删除/重命名文件、新增 locale），请立即更新本文档！**

## 📄 文件列表

| 文件 | 功能 |
|------|------|
| `__init__.py` | 对外 API：`t` / `translate` / `set_language` / `get_translator` / `supported_languages` |
| `translator.py` | `Translator` 类 + 全局翻译器状态；键查找、参数插值、缺失回退 |
| `locales/zh-CN.json` | 简体中文（默认语言）翻译目录 |
| `locales/en-US.json` | 英文翻译目录 |

## 🔑 API 约定

```python
from hawarma.i18n import t, set_language, supported_languages

t("menu.quit")                        # 静态文本
t("game.device_connect_failed", error=str(e))  # {var} 插值
t("display.station.foo", default="foo")        # 动态映射键 + 显式默认值
set_language("en-US")                 # 全局切换
supported_languages()                 # ('en-US', 'zh-CN')，由 locales/*.json 扫描得出
```

行为：
- 键用 `.` 分隔命名空间（如 `game.stat_time`）
- 缺失键回退到 `key` 本身，或调用方传入的 `default`
- 键集合必须两个 locale 完全一致（由 `tests/test_i18n.py` 的 parity 测试保证）

## 🔗 与外部的关系

```
config.yaml `language` → TUI/CLI 启动时 set_language()
        ↓
   t("...") 调用点（tui.py / cli.py 的界面文案）
        ↓
   输出带参数插值后的本地化文本

不翻译：runner/agent 的 loguru 引擎日志（保持英文）
不翻译：数据标识符 slug（station/strategy/recipe/cooker），仅 display.* 映射用于显示名
```

## 🗺️ 设计决策

1. **JSON 目录而非 gettext**：项目体量小，避免 PO/MO 编译链，保持简单。
2. **键名回退而非英文源串回退**：避免英文源串与数据（slug、配方名）语义冲突。
3. **`display.*` 显示名映射**：数据标识符永不翻译，只在展示时映射为本地化名称。
   - `display.station.*` / `display.strategy.*` / `display.recipe.*`
   - **`display.recipe.*` 必须覆盖 `data/recipes.json` 中的全部 slug**（新增配方时同步补充，
     由 `tests/test_i18n.py::test_all_recipe_slugs_have_display_names` 强制校验）；
     查找不到的 slug 回退到数据源英文名。
4. **引擎日志不翻译**：TUI/CLI 只翻译自己生成的 chrome 文案，控制翻译范围。

## ✅ 测试

见 `tests/test_i18n.py`：
- 所有 locale 键集合一致性
- `tui.py` / `cli.py` 引用的静态键全部存在
- 各 locale 下 TUI 应用可无异常启动并导航全部屏幕