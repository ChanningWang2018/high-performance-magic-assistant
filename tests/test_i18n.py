"""
i18n 翻译测试

覆盖：
1. 所有 locale 文件的键集合完全一致
2. translate / t / set_language / supported_languages 基本行为
3. tui.py 与 cli.py 中引用的所有静态键都在 locale 中存在
4. 各 locale 下 TUI 应用可完整启动并导航到所有屏幕（headless）
"""

import asyncio
import json
import pathlib
import re
import unittest

from hawarma.config import load_config
from hawarma.i18n import set_language, supported_languages, translate
from hawarma.recipe import Recipe, Station
from hawarma.services.recipe_manager import RecipeManager

LOCALES_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "hawarma" / "i18n" / "locales"
SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "hawarma"


def _flatten(data: dict, prefix: str = "") -> set[str]:
    """将嵌套 JSON 拍平成 `a.b.c` 形式的键集合"""
    keys: set[str] = set()
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys |= _flatten(value, full)
        else:
            keys.add(full)
    return keys


def _load_catalogs() -> dict[str, dict]:
    return {
        language: json.loads(
            (LOCALES_DIR / f"{language}.json").read_text(encoding="utf-8")
        )
        for language in supported_languages()
    }


def _source_keys(filepath: pathlib.Path) -> set[str]:
    """提取源文件里所有 t("...") 静态键（动态键由运行时 fallback 兜底）"""
    text = filepath.read_text(encoding="utf-8")
    return set(re.findall(r'\bt\("([^"]+)"\)', text))


def _recipe(slug: str = "r1", name: str = "Recipe1") -> Recipe:
    return Recipe(
        slug=slug,
        name=name,
        raw_ingredients=["ing"],
        cookers=["cooker"],
        cookers_layout=["cooker"],
        cook_durations=[1.0],
        condiments={},
        station=Station.GASTRONOME,
    )


class TestI18nCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = _load_catalogs()
        cls.languages = list(cls.catalogs)

    def test_key_parity_between_locales(self):
        base = _flatten(self.catalogs[self.languages[0]])
        for language in self.languages[1:]:
            other = _flatten(self.catalogs[language])
            self.assertEqual(
                base, other,
                f"[{language}] 与 [{self.languages[0]}] 键集合不一致: "
                f"多出={sorted(other - base)} 缺失={sorted(base - other)}",
            )

    def test_static_keys_used_by_ui_exist_in_all_locales(self):
        used: set[str] = set()
        for file in ("tui.py", "cli.py"):
            used |= _source_keys(SRC_DIR / file)
        self.assertTrue(used, "未提取到任何 t() 键，请检查提取正则")
        for language, catalog in self.catalogs.items():
            missing = used - _flatten(catalog)
            self.assertEqual(missing, set(), f"[{language}] 缺失键: {sorted(missing)}")

    def test_all_recipe_slugs_have_display_names(self):
        """数据源中每个 recipe slug 都必须有 display.recipe.* 键（两种语言）"""
        from hawarma.services.recipe_manager import RecipeManager

        manager = RecipeManager()
        slugs = {r.slug for r in manager.get_all_recipes()}
        self.assertTrue(slugs, "未加载到任何配方")

        for language, catalog in self.catalogs.items():
            keys = _flatten(catalog)
            missing = {s for s in slugs if f"display.recipe.{s}" not in keys}
            self.assertEqual(
                missing, set(),
                f"[{language}] 缺少 display.recipe 键: {sorted(missing)}",
            )

    def test_translate_fallback_and_params(self):
        set_language("en-US")
        self.assertEqual(translate("app.title"), "Hawarma - Cooking Assistant Agent")
        self.assertEqual(translate("no.such.key", default="d"), "d")
        self.assertEqual(
            translate("game.device_connect_failed", error="boom"),
            "Device connection failed: boom",
        )


class TestI18nTUI(unittest.TestCase):
    def test_app_navigates_all_screens_in_all_locales(self):
        from hawarma.tui import HawarmaApp, OrderInvalidModal

        async def _run() -> None:
            for language in supported_languages():
                set_language(language)
                app = HawarmaApp()
                app.config.language = language
                set_language(language)
                app.title = translate("app.title")
                async with app.run_test() as pilot:
                    await pilot.pause()
                    self.assertIsNotNone(app.screen, f"[{language}] 主菜单未显示")

                    await app.push_screen("recipes")
                    await pilot.pause()
                    await app.pop_screen()
                    await pilot.pause()

                    await app.push_screen("config")
                    await pilot.pause()
                    await app.pop_screen()
                    await pilot.pause()

                    await app.push_screen("game")
                    await pilot.pause()
                    await app.pop_screen()
                    await pilot.pause()

                    await app.push_screen(OrderInvalidModal("err", [_recipe()]))
                    await pilot.pause()
                    await app.pop_screen()
                    await pilot.pause()

                    app.exit()

        asyncio.run(_run())

    def test_toggle_language_rebuilds_main_menu(self):
        from unittest import mock

        from hawarma.i18n import get_translator
        from hawarma.tui import HawarmaApp, MainMenuScreen

        async def _run() -> None:
            # 不落盘真实 config.yaml
            with mock.patch("hawarma.tui.save_config"):
                app = HawarmaApp()
                async with app.run_test() as pilot:
                    await pilot.pause()
                    self.assertIsInstance(app.screen, MainMenuScreen)
                    before = get_translator().language

                    app.toggle_language()
                    await pilot.pause()

                    self.assertNotEqual(get_translator().language, before)
                    self.assertEqual(app.config.language, get_translator().language)
                    self.assertIsInstance(app.screen, MainMenuScreen)
                    app.exit()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
