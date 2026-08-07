# hawarma/i18n/__init__.py
"""
i18n 多语言支持模块

职责：为 UI 层（TUI / CLI）提供 key 目录驱动的轻量翻译服务。

本模块**不翻译引擎日志**（runner/agent 的 loguru 输出保持英文）。
UI 只翻译自己生成的文案，数据标识符（station/strategy/recipe/cooker 的
slug）不翻译原值，仅做显示名映射。

用法：
    from hawarma.i18n import t, set_language, supported_languages

    set_language("zh-CN")          # 全局切换
    t("menu.quit")                 # 静态文本
    t("game.error", error=str(e))  # 模板插值

⚠️ 一旦本目录内容有更新，务必更新本注释及相关 ARCHITECTURE.md
"""

from hawarma.i18n.translator import (
    DEFAULT_LANGUAGE,
    Translator,
    get_translator,
    set_language,
    supported_languages,
    t,
    translate,
)

__all__ = [
    "DEFAULT_LANGUAGE",
    "Translator",
    "get_translator",
    "set_language",
    "supported_languages",
    "t",
    "translate",
]