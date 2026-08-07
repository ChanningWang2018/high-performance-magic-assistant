# hawarma/i18n/translator.py
"""
翻译器核心

形态：基于 JSON 目录的 key → 文本 轻量翻译。
每个语言一个 JSON 文件（locales/目录），键用 `.` 分隔的命名空间。
支持 `{var}` 模板插值。缺失键回退到 key 本身或调用方提供的 default。

用法：
    from hawarma.i18n import t
    t("menu.quit")                       # 静态文本
    t("game.device_connect_failed", error=str(e))   # 带插值

语言切换：set_language("en-US") 全局生效。

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新，
同时更新所属目录的 md（i18n/ARCHITECTURE.md）
"""

import json
from functools import lru_cache
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANGUAGE = "zh-CN"


@lru_cache(maxsize=None)
def supported_languages() -> tuple[str, ...]:
    """扫描 locales 目录，返回支持的语言代码（按文件名排序）"""
    return tuple(sorted(p.stem for p in LOCALES_DIR.glob("*.json")))


class Translator:
    """单个语言的翻译器"""

    def __init__(self, language: str):
        self.language = language
        path = LOCALES_DIR / f"{language}.json"
        if not path.exists():
            raise FileNotFoundError(f"Unsupported locale: {language}")
        with open(path, encoding="utf-8") as f:
            self._strings = json.load(f)

    def translate(self, key: str, default: str | None = None, **params) -> str:
        """按 key 查找文本，支持 {param} 插值；缺失时回退到 default 或 key"""
        text = self._lookup(key)
        if text is None:
            text = key if default is None else default
        if params:
            text = text.format(**params)
        return text

    def _lookup(self, key: str) -> str | None:
        node = self._strings
        for part in key.split("."):
            if isinstance(node, dict):
                node = node.get(part)
            else:
                return None
        return node if isinstance(node, str) else None


_current: Translator | None = None


def get_translator(language: str | None = None) -> Translator:
    """返回全局翻译器；传入 language 时同时切换全局语言"""
    global _current
    if language is not None:
        _current = Translator(language)
    elif _current is None:
        _current = Translator(DEFAULT_LANGUAGE)
    return _current


def set_language(language: str) -> Translator:
    """切换全局语言，返回新翻译器"""
    return get_translator(language)


def translate(key: str, default: str | None = None, **params) -> str:
    """全局翻译入口（等价于 t()）"""
    return get_translator().translate(key, default=default, **params)


def t(key: str, default: str | None = None, **params) -> str:
    """全局翻译入口的短别名，供 UI 层使用"""
    return get_translator().translate(key, default=default, **params)