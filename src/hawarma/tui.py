"""
Hawarma TUI - 烹饪游戏自动化 Agent 的文本用户界面

使用 Textual 框架构建的完整仪表板界面，包含：
- 菜单栏
- 配方选择界面
- 配置面板
- 游戏控制界面
- 日志区域

界面文案通过 i18n 目录翻译（zh-CN 默认，en-US 可选），
引擎日志（runner/agent 的 loguru 输出）保持英文不翻译。

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新，
同时更新所属目录的 md（src/hawarma/ARCHITECTURE.md）
"""

import asyncio

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Log,
    Select,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.selection_list import Selection
from loguru import logger
from textual.worker import Worker, WorkerState

from hawarma.config import load_config, save_config
from hawarma.i18n import get_translator, set_language, supported_languages, t
from hawarma.recipe import Recipe, Station
from hawarma.services.recipe_manager import RecipeManager
from hawarma.device import setup_device
from hawarma.patches import apply_patch
from hawarma.log import setup_logging
from hawarma.utils.order_parser import parse_order_input, validate_order_input

# 将 SelectionList 的选中标记从 "X" 改为 "✓"
from textual.widgets._toggle_button import ToggleButton
ToggleButton.BUTTON_INNER = "✓"


def _station_display(station: Station) -> str:
    """station slug → 本地化显示名（找不到时回退到 slug 本身）"""
    return t(f"display.station.{station.value}", default=station.value)


def _strategy_display(strategy: str) -> str:
    """策略 slug → 本地化显示名（找不到时回退到 slug 本身）"""
    return t(f"display.strategy.{strategy}", default=strategy)


def _recipe_display(recipe: Recipe) -> str:
    """recipe slug → 本地化显示名（找不到时回退到数据源英文名）"""
    return t(f"display.recipe.{recipe.slug}", default=recipe.name)


class MainMenuScreen(Screen):
    """主菜单屏幕"""

    def __init__(self) -> None:
        super().__init__()
        # BINDINGS 在实例化时求值，语言切换后重建屏幕即可刷新 Footer 描述
        self.BINDINGS = [
            Binding("q", "quit", t("bindings.quit")),
            Binding("r", "recipes", t("bindings.recipes")),
            Binding("c", "config", t("bindings.config")),
            Binding("g", "game", t("bindings.game")),
            Binding("l", "language", t("bindings.language")),
        ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static(t("app.title"), classes="title"),
            Static(t("app.subtitle"), classes="subtitle"),
            Button(t("menu.recipes"), id="recipes", variant="primary"),
            Button(t("menu.config"), id="config", variant="default"),
            Button(t("menu.game"), id="game", variant="success"),
            Button(t("menu.quit"), id="quit", variant="error"),
            Button(t("menu.language"), id="language", variant="default"),
            classes="menu-container",
        )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "recipes":
            self.app.push_screen("recipes")
        elif event.button.id == "config":
            self.app.push_screen("config")
        elif event.button.id == "game":
            self.app.push_screen("game")
        elif event.button.id == "language":
            self.app.toggle_language()
        elif event.button.id == "quit":
            self.app.exit()

    def action_recipes(self) -> None:
        self.app.push_screen("recipes")

    def action_config(self) -> None:
        self.app.push_screen("config")

    def action_game(self) -> None:
        self.app.push_screen("game")

    def action_language(self) -> None:
        self.app.toggle_language()


class RecipeSelectionScreen(Screen):
    """配方选择屏幕（入口层：选择模式 → 过滤菜谱 → 选择策略）"""

    def __init__(self, recipe_manager: RecipeManager):
        super().__init__()
        self.recipe_manager = recipe_manager
        self._all_recipes = recipe_manager.get_all_recipes()

    def compose(self) -> ComposeResult:
        station = self.app.station
        filtered = [r for r in self._all_recipes if r.station == station]

        # 基于当前 station 构建策略选项和默认值
        strategy_options, strategy_values = self._strategy_options_for_station(station)
        current_strategy = self._resolve_strategy_value(strategy_values)

        yield Header()
        yield Container(
            Static(t("recipes.title"), classes="title"),
            Static(t("recipes.step_mode"), classes="subtitle"),
            Select(
                [
                    (_station_display(Station.GASTRONOME), Station.GASTRONOME.value),
                    (_station_display(Station.DESSERT), Station.DESSERT.value),
                ],
                value=station.value, id="rs-station-select",
            ),
            Static(t("recipes.step_recipe"), classes="subtitle"),
            SelectionList[str](
                *[Selection(_recipe_display(r), r.slug) for r in filtered],
                id="rs-recipe-list",
            ),
            Static(t("recipes.step_order"), classes="subtitle"),
            Static(id="rs-order-hint", classes="order-hint"),
            Input(
                placeholder=t("recipes.order_placeholder"),
                id="rs-order-input",
                restrict=r"[0-9]*",
            ),
            Static(t("recipes.step_strategy"), classes="subtitle"),
            Select(
                options=strategy_options,
                value=current_strategy, id="rs-strategy-select",
            ),
            Horizontal(
                Button(t("recipes.confirm"), id="confirm", variant="primary"),
                Button(t("recipes.back"), id="back", variant="default"),
                classes="button-row",
            ),
            classes="recipe-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """挂载后初始化动态提示区"""
        self._update_order_hint()

    def _strategy_options_for_station(self, station: Station) -> tuple[list[tuple[str, str]], list[str]]:
        """根据 station 返回可用的策略选项列表"""
        if station == Station.GASTRONOME:
            options = [(_strategy_display("gastronome"), "gastronome")]
        else:
            options = [(_strategy_display("dessert"), "dessert")]
        return options, [v for _, v in options]

    def _resolve_strategy_value(self, strategy_values: list[str]) -> str:
        """解析当前策略值，fallback 到 config"""
        current = self.app.game_strategy or self.app.config.strategy
        return current if current in strategy_values else strategy_values[0]

    def _selected_recipes(self) -> list[Recipe]:
        """按全量菜谱顺序返回当前选中的菜谱（与 confirm 时使用的顺序一致）"""
        station = self.app.station
        sl = self.query_one("#rs-recipe-list", SelectionList)
        selected_slugs = set(sl.selected)
        return [
            r for r in self._all_recipes
            if r.slug in selected_slugs and r.station == station
        ]

    def _update_order_hint(self) -> None:
        """根据当前选中的菜谱刷新提示区与输入约束"""
        selected = self._selected_recipes()
        n = len(selected)
        hint = self.query_one("#rs-order-hint", Static)
        order_input = self.query_one("#rs-order-input", Input)

        order_input.max_length = n if n > 0 else 0
        order_input.disabled = n < 2

        if n == 0:
            hint.update(t("recipes.hint_none"))
        elif n == 1:
            hint.update(t("recipes.hint_single", index=0, name=_recipe_display(selected[0])))
        else:
            names = "\n".join(f"  [{i}] {_recipe_display(r)}" for i, r in enumerate(selected))
            seq0 = "".join(str(i) for i in range(n))
            seq1 = "".join(str(i + 1) for i in range(n))
            hint.update(t("recipes.hint_multi", names=names, n=n, seq0=seq0, seq1=seq1))

    def _rebuild_recipe_list(self) -> None:
        """根据当前 station 重建菜谱列表"""
        station = self.app.station
        filtered = [r for r in self._all_recipes if r.station == station]
        sl = self.query_one("#rs-recipe-list", SelectionList)
        sl.clear_options()
        for r in filtered:
            sl.add_option(Selection(_recipe_display(r), r.slug))
        self._update_order_hint()

    def _rebuild_strategy_options(self) -> None:
        """根据当前 station 重建策略选项"""
        station = self.app.station
        options, values = self._strategy_options_for_station(station)
        current_strategy = self._resolve_strategy_value(values)
        ss = self.query_one("#rs-strategy-select", Select)
        ss.set_options(options)
        ss.value = current_strategy

    def on_select_changed(self, event: Select.Changed) -> None:
        """Station 选择变化时刷新菜谱和策略"""
        if event.value is Select.NULL:
            return
        if event.select.id == "rs-station-select":
            self.app.station = Station(event.value)
            self._rebuild_recipe_list()
            self._rebuild_strategy_options()

    def on_selection_list_selected_changed(
        self, event: SelectionList.SelectedChanged
    ) -> None:
        """菜谱选中状态变化时刷新提示区"""
        self._update_order_hint()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            station = self.app.station
            self.app.station = station
            self.app.game_strategy = self.query_one("#rs-strategy-select", Select).value
            selected_recipes = self._selected_recipes()
            if not selected_recipes:
                return
            order_input = self.query_one("#rs-order-input", Input).value.strip()
            valid, error = validate_order_input(selected_recipes, order_input)
            if not valid:
                self._show_invalid_modal(error, selected_recipes)
                return
            self.app.selected_recipes = parse_order_input(selected_recipes, order_input)
            self.app.pop_screen()
        elif event.button.id == "back":
            self.app.pop_screen()

    def _show_invalid_modal(self, error: str, selected_recipes: list[Recipe]) -> None:
        """弹出错误 modal，由用户决定返回修改还是忽略并使用默认顺序"""

        def on_dismiss(ignore: bool | None) -> None:
            if ignore:
                self.app.selected_recipes = parse_order_input(selected_recipes, "")
                self.app.pop_screen()

        self.app.push_screen(
            OrderInvalidModal(error, selected_recipes),
            on_dismiss,
        )


class OrderInvalidModal(Screen):
    """准备顺序输入非法时的提示 modal"""

    def __init__(self, error: str, selected_recipes: list[Recipe]):
        super().__init__()
        self._error = error
        self._selected_recipes = selected_recipes
        self.BINDINGS = [Binding("escape", "dismiss_modal", t("bindings.back"))]

    def compose(self) -> ComposeResult:
        names = "\n".join(
            f"  [{i}] {_recipe_display(r)}" for i, r in enumerate(self._selected_recipes)
        )
        yield Container(
            Static(t("modal.title"), classes="title"),
            Static(self._error, classes="modal-error"),
            Static(t("modal.selected", names=names), classes="modal-info"),
            Horizontal(
                Button(t("modal.back"), id="back", variant="primary"),
                Button(t("modal.ignore"), id="ignore", variant="warning"),
                classes="button-row",
            ),
            classes="modal-container",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.dismiss(False)
        elif event.button.id == "ignore":
            self.dismiss(True)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class ConfigScreen(Screen):
    """配置屏幕"""

    def __init__(self, config):
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="config-container"):
            yield Static(t("config.title"), classes="title")
            with TabbedContent():
                with TabPane(t("config.tab_basic"), id="basic"):
                    yield Static(t("config.tab_basic"), classes="tab-title")
                    yield Input(value=self.config.adb_address, placeholder=t("config.placeholder_adb"), id="adb-address")
                    yield Input(value=self.config.image_directory, placeholder=t("config.placeholder_image"), id="image-directory")
                    yield Input(value=self.config.log_directory, placeholder=t("config.placeholder_log"), id="log-directory")
                    yield Input(value=self.config.recipes_data_path, placeholder=t("config.placeholder_recipes_data"), id="recipes-data-path")
                    yield Input(value=str(self.config.episode_duration), placeholder=t("config.placeholder_duration"), id="episode-duration")
                with TabPane(t("config.tab_screen"), id="screen"):
                    yield Static(t("config.tab_screen"), classes="tab-title")
                    yield Input(value=f"{self.config.screen.resolution[0]},{self.config.screen.resolution[1]}", placeholder=t("config.placeholder_resolution"), id="resolution")
                with TabPane(t("config.tab_matching"), id="matching"):
                    yield Static(t("config.tab_matching"), classes="tab-title")
                    yield Input(value=self.config.matching.ingredients_strategy[0], placeholder=t("config.placeholder_matching_strategy"), id="matching-strategy")
                    yield Input(value=str(self.config.matching.ingredients_threshold), placeholder=t("config.placeholder_threshold"), id="matching-threshold")
                    yield Input(value=str(self.config.matching.timer_threshold), placeholder=t("config.placeholder_timer_threshold"), id="timer-threshold")
                with TabPane(t("config.tab_game"), id="game"):
                    yield Static(t("config.tab_game"), classes="tab-title")
                    yield Input(value=str(self.config.game.cooker_retention), placeholder=t("config.placeholder_cooker_retention"), id="cooker-retention")
                    yield Input(value=str(self.config.game.rush_red_threshold), placeholder=t("config.placeholder_rush_threshold"), id="rush-threshold")
                with TabPane(t("config.tab_debug"), id="debug"):
                    yield Static(t("config.tab_debug"), classes="tab-title")
                    yield Checkbox(value=self.config.debug.save_order_screenshots, label=t("config.label_save_orders"), id="save-order-screenshots")
                    yield Checkbox(value=self.config.debug.save_assembly_verify_screenshots, label=t("config.label_save_assembly"), id="save-assembly-screenshots")
                    yield Checkbox(value=self.config.debug.save_timer_screenshots, label=t("config.label_save_timer"), id="save-timer-screenshots")
            with Horizontal(classes="button-row"):
                yield Button(t("config.save"), id="save", variant="primary")
                yield Button(t("config.back"), id="back", variant="default")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.save_config()
            self.app.pop_screen()
        elif event.button.id == "back":
            self.app.pop_screen()

    def save_config(self) -> None:
        # 基本设置
        self.config.adb_address = self.query_one("#adb-address", Input).value
        self.config.image_directory = self.query_one("#image-directory", Input).value
        self.config.log_directory = self.query_one("#log-directory", Input).value
        self.config.recipes_data_path = self.query_one("#recipes-data-path", Input).value
        self.config.episode_duration = int(self.query_one("#episode-duration", Input).value)
        
        # 屏幕设置
        resolution = self.query_one("#resolution", Input).value.split(",")
        self.config.screen.resolution = (int(resolution[0]), int(resolution[1]))

        # 匹配设置
        self.config.matching.ingredients_strategy = [self.query_one("#matching-strategy", Input).value]
        self.config.matching.ingredients_threshold = float(self.query_one("#matching-threshold", Input).value)
        self.config.matching.timer_threshold = float(self.query_one("#timer-threshold", Input).value)
        
        # 游戏设置
        self.config.game.cooker_retention = float(self.query_one("#cooker-retention", Input).value)
        self.config.game.rush_red_threshold = int(self.query_one("#rush-threshold", Input).value)

        # 调试设置
        self.config.debug.save_order_screenshots = self.query_one("#save-order-screenshots", Checkbox).value
        self.config.debug.save_assembly_verify_screenshots = self.query_one("#save-assembly-screenshots", Checkbox).value
        self.config.debug.save_timer_screenshots = self.query_one("#save-timer-screenshots", Checkbox).value
        
        # 保存到YAML文件
        save_config(self.config)


class GameControlScreen(Screen):
    """游戏控制屏幕"""

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.game_worker = None
        self.BINDINGS = [
            Binding("escape", "back", t("bindings.back")),
            Binding("r", "recipes", t("bindings.recipes")),
            Binding("c", "config", t("bindings.config")),
        ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static(t("game.title"), classes="title"),
            Horizontal(
                Button(t("game.start"), id="start", variant="success"),
                Button(t("game.stop"), id="stop", variant="error", disabled=True),
                Button(t("game.back"), id="back", variant="default"),
                classes="button-row",
            ),
            Log(id="game-log", auto_scroll=True),
            classes="game-container",
        )
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_recipes(self) -> None:
        self.app.push_screen("recipes")

    def action_config(self) -> None:
        self.app.push_screen("config")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.start_game()
        elif event.button.id == "stop":
            self.stop_game()
        elif event.button.id == "back":
            self.app.pop_screen()

    def start_game(self) -> None:
        self.query_one("#start", Button).disabled = True
        self.query_one("#stop", Button).disabled = False

        log = self.query_one("#game-log", Log)
        log.write_line(
            t(
                "game.station_summary",
                station=_station_display(self.app.station),
                strategy=_strategy_display(
                    self.app.game_strategy or self.config.strategy
                ),
            )
            + "\n"
        )
        log.write_line(t("game.connecting_device") + "\n")

        try:
            setup_device(self.config.adb_address)
            apply_patch()
            log.write_line(t("game.device_connected") + "\n")
        except Exception as e:
            log.write_line(t("game.device_connect_failed", error=str(e)) + "\n")
            self.query_one("#start", Button).disabled = False
            self.query_one("#stop", Button).disabled = True
            return
        
        self._tui_sink_id = logger.add(
            lambda msg: log.write_line(f"{msg.record['time'].strftime('%H:%M:%S.%f')[:-3]} | {msg.record['level'].name: <5} | {msg.record['message']}\n"),
            level="INFO",
            format="",
        )
        
        self.run_game()

    @work(exclusive=True, exit_on_error=False)
    async def run_game(self) -> None:
        """运行游戏逻辑"""
        from hawarma.game import Runner
        from hawarma.game.game_env import GameEnv
        from hawarma.game.scanner import Scanner
        from hawarma.game.operator import Operator
        from hawarma.game.verifier import Verifier
        from hawarma.agent.registry import get_strategy
        
        log = self.query_one("#game-log", Log)
        
        try:
            if not self.app.selected_recipes:
                log.write_line(t("game.no_recipes") + "\n")
                return
            
            recipes = self.app.selected_recipes
            recipes_dict = {r.slug: r for r in recipes}
            strategy_name = self.app.game_strategy or self.config.strategy
            strategy = get_strategy(strategy_name)
            log.write_line(t("game.using_strategy", strategy=_strategy_display(strategy_name)) + "\n")
            
            # DI 组装
            station = self.app.station
            operator = Operator(self.config, recipes, station)
            scanner = Scanner(self.config, recipes)
            verifier = Verifier(self.config)
            cooker_names = list(operator.cooker_positions.keys())
            stockpile_slots = 0 if station == Station.DESSERT else len(self.config.screen.stockpile_positions)
            env = GameEnv(
                cooker_names=cooker_names,
                stockpile_slots=stockpile_slots,
                game_duration=self.config.episode_duration,
                recipes=recipes_dict,
                cooker_retention=self.config.game.cooker_retention,
            )
            bridge = Runner(env, operator, scanner, verifier, strategy, recipes_dict)
            
            log.write_line("=" * 40 + "\n")
            log.write_line(t("game.scan_started") + "\n")
            log.write_line(t("game.recipes_line", names=str([_recipe_display(r) for r in recipes])) + "\n")
            log.write_line(t("game.cookers_line", names=str(cooker_names)) + "\n")
            log.write_line("=" * 40 + "\n")
            
            # 运行游戏
            stats = await bridge.run()
            
            log.write_line("=" * 40 + "\n")
            log.write_line(t("game.game_over") + "\n")
            log.write_line(t("game.stat_time", value=f"{stats['time']:.1f}") + "\n")
            log.write_line(t("game.stat_orders", value=stats['orders_served']) + "\n")
            log.write_line(t("game.stat_score", value=stats['total_score']) + "\n")
            log.write_line(t("game.stat_timed_out", value=stats['orders_timeout']) + "\n")
            log.write_line(t("game.stat_actions", value=stats['actions_taken']) + "\n")
            log.write_line("=" * 40 + "\n")
            
        except asyncio.CancelledError:
            log.write_line(t("game.cancelled") + "\n")
        except Exception as e:
            log.write_line(t("game.error", error=str(e)) + "\n")

    def stop_game(self) -> None:
        if hasattr(self, '_tui_sink_id'):
            logger.remove(self._tui_sink_id)
            del self._tui_sink_id
        
        for worker in self.app.workers:
            if worker.name == "run_game":
                worker.cancel()
                break
        
        self.query_one("#start", Button).disabled = False
        self.query_one("#stop", Button).disabled = True
        
        log = self.query_one("#game-log", Log)
        log.write_line(t("game.stopping") + "\n")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Worker 状态变化时更新 UI"""
        if event.state in (WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
            if hasattr(self, '_tui_sink_id'):
                logger.remove(self._tui_sink_id)
                del self._tui_sink_id
            self.query_one("#start", Button).disabled = False
            self.query_one("#stop", Button).disabled = True


class HawarmaApp(App):
    """Hawarma TUI 应用"""

    CSS = """
    Screen {
        layout: vertical;
    }

    .title {
        text-align: center;
        text-style: bold;
        margin: 1 0;
    }

    .subtitle {
        text-align: center;
        margin: 1 0;
    }

    .menu-container {
        align: center middle;
        width: 100%;
        height: 100%;
    }

    .recipe-container, .config-container, .game-container {
        width: 100%;
        height: 1fr;
        overflow-y: auto;
    }

    .button-row {
        align: center middle;
        margin: 1 0;
    }

    #rs-station-select {
        width: 60%;
        max-width: 30;
        min-width: 16;
        margin: 0 0 1 0;
    }

    #rs-recipe-list {
        height: auto;
        max-height: 60%;
        border: solid green;
    }

    #rs-strategy-select {
        width: 60%;
        max-width: 30;
        min-width: 16;
        margin: 0 0 1 0;
    }

    #rs-order-input {
        width: 40%;
        max-width: 20;
        min-width: 10;
        margin: 0 0 1 0;
    }

    .order-hint {
        margin: 0 0 1 2;
        color: $text-muted;
    }

    .modal-container {
        align: center middle;
        width: 70%;
        max-width: 80;
        height: auto;
        padding: 1 2;
        border: thick $warning;
        background: $surface;
    }

    .modal-error {
        color: $error;
        text-style: bold;
        margin: 1 0;
    }

    .modal-info {
        color: $text-muted;
        margin: 0 0 1 0;
    }

    Button {
        margin: 0 1;
    }

    #recipe-list {
        height: 1fr;
        border: solid green;
    }

    Log {
        height: 1fr;
        border: solid blue;
    }
    """

    def __init__(self):
        super().__init__()
        self.theme = "catppuccin-frappe"
        setup_logging(terminal=False, log_name="tui")
        self.config = load_config()
        set_language(self.config.language)
        self.title = t("app.title")
        self.recipe_manager = RecipeManager()
        self.selected_recipes: list[Recipe] = []
        self.station: Station = Station.GASTRONOME
        self.game_strategy: str | None = None

    def _install_screens(self) -> None:
        """注册全部屏幕（以当前语言实例化）"""
        self.install_screen(MainMenuScreen(), name="main")
        self.install_screen(RecipeSelectionScreen(self.recipe_manager), name="recipes")
        self.install_screen(ConfigScreen(self.config), name="config")
        self.install_screen(GameControlScreen(self.config), name="game")

    def on_mount(self) -> None:
        self._install_screens()
        self.push_screen("main")

    def toggle_language(self) -> None:
        """循环切换界面语言并重建屏幕"""
        languages = supported_languages()
        current = get_translator().language
        if current not in languages:
            current = languages[0]
        next_lang = languages[(languages.index(current) + 1) % len(languages)]
        set_language(next_lang)
        self.config.language = next_lang
        save_config(self.config)
        self.title = t("app.title")
        self._reinstall_screens()

    def _reinstall_screens(self) -> None:
        """以新语言重建屏幕（仅在主菜单调用；会丢弃旧 GameControl 屏幕状态，请在游戏停止后切换）"""
        # Textual 8: uninstall 会拒绝仍在屏幕栈中的屏，必须先弹出
        while self.screen in self._installed_screens.values():
            self.pop_screen()
        for name in ("main", "recipes", "config", "game"):
            if name in self._installed_screens:
                self.uninstall_screen(name)
        self._install_screens()
        self.push_screen("main")


def main():
    """TUI 入口点"""
    app = HawarmaApp()
    app.run()


if __name__ == "__main__":
    main()
