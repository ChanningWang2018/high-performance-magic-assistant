"""
Hawarma - 烹饪游戏自动化 Agent

通过图像检测识别订单，使用贪心策略在 90 秒内最大化订单完成数。

界面提示文案通过 i18n 翻译（zh-CN 默认，en-US 可选）；
引擎日志保持英文不翻译。

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新，
同时更新所属目录的 md（src/hawarma/ARCHITECTURE.md）
"""

import asyncio

import questionary
from loguru import logger

from hawarma.config import load_config
from hawarma.i18n import set_language, t
from hawarma.services.recipe_manager import RecipeManager
from hawarma.patches import apply_patch
from hawarma.log import setup_logging
from hawarma.device import setup_device
from hawarma.recipe import Station
from hawarma.utils.order_parser import parse_order_input


def _recipe_display(recipe):
    """recipe slug → 本地化显示名（找不到时回退到数据源英文名）"""
    return t(f"display.recipe.{recipe.slug}", default=recipe.name)


def get_recipe_selection(all_recipes):
    """Get user input for recipe selection and ordering."""
    display_names = [_recipe_display(r) for r in all_recipes]
    selected_names = questionary.checkbox(
        t("cli.select_recipes"), choices=display_names
    ).ask()

    if not selected_names:
        return None

    name_to_recipe = dict(zip(display_names, all_recipes))
    selected_recipes = [name_to_recipe[name] for name in selected_names]

    order_input = questionary.text(
        t("cli.order_input", n=len(selected_recipes))
    ).ask()

    return parse_order_input(selected_recipes, order_input)


async def run_game(config, ordered_recipes, strategy=None, station=Station.GASTRONOME):
    """Run the agent game loop.

    Args:
        config: AppConfig instance
        ordered_recipes: List of selected Recipe objects
        strategy: Optional strategy instance to use. Defaults to config.strategy.
        station: Station mode (GASTRONOME or DESSERT).
    """
    from hawarma.game import Runner
    from hawarma.game.game_env import GameEnv
    from hawarma.game.scanner import Scanner
    from hawarma.game.operator import Operator
    from hawarma.game.verifier import Verifier
    from hawarma.agent.registry import get_strategy

    recipes_dict = {r.slug: r for r in ordered_recipes}

    # 使用配置中的策略，可通过参数覆盖
    if strategy is None:
        strategy = get_strategy(config.strategy)

    # DI 组装
    operator = Operator(config, ordered_recipes, station)
    scanner = Scanner(config, ordered_recipes)
    verifier = Verifier(config)
    cooker_names = list(operator.cooker_positions.keys())
    stockpile_slots = 0 if station == Station.DESSERT else len(config.screen.stockpile_positions)
    env = GameEnv(
        cooker_names=cooker_names,
        stockpile_slots=stockpile_slots,
        game_duration=config.episode_duration,
        recipes=recipes_dict,
        cooker_retention=config.game.cooker_retention,
    )
    bridge = Runner(env, operator, scanner, verifier, strategy, recipes_dict)

    logger.info("=" * 60)
    logger.info(t("cli.starting"))
    logger.info(t("cli.station", station=t(f"display.station.{station.value}", default=station.value)))
    logger.info(t("game.recipes_line", names=str([_recipe_display(r) for r in ordered_recipes])))
    logger.info(t("game.cookers_line", names=str(cooker_names)))
    logger.info("=" * 60)

    stats = await bridge.run()

    logger.info("=" * 60)
    logger.info(t("game.game_over"))
    logger.info(t("game.stat_time", value=f"{stats['time']:.1f}"))
    logger.info(t("game.stat_orders", value=stats['orders_served']))
    logger.info(t("game.stat_score", value=stats['total_score']))
    logger.info(t("game.stat_timed_out", value=stats['orders_timeout']))
    logger.info(t("game.stat_actions", value=stats['actions_taken']))
    logger.info("=" * 60)

    return stats


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description=t("cli.arg_desc"))
    parser.add_argument(
        "--strategy",
        type=str,
        default=None,
        help=t("cli.arg_strategy"),
    )
    parser.add_argument(
        "--station",
        type=str,
        default="gastronome",
        choices=["gastronome", "dessert"],
        help=t("cli.arg_station"),
    )
    args = parser.parse_args()

    station = Station.DESSERT if args.station == "dessert" else Station.GASTRONOME

    setup_logging()

    config = load_config()
    set_language(config.language)
    setup_device(config.adb_address)
    apply_patch()

    if args.strategy:
        from hawarma.agent.registry import get_strategy
        strategy = get_strategy(args.strategy)
        logger.info(t("cli.using_strategy", strategy=args.strategy))
    else:
        strategy = None
        logger.info(t("cli.using_strategy_from_config", strategy=config.strategy))

    recipe_manager = RecipeManager()
    all_recipes = recipe_manager.get_all_recipes()
    # 按 station 过滤食谱
    filtered_recipes = [r for r in all_recipes if r.station == station]

    while True:
        ordered_recipes = get_recipe_selection(filtered_recipes)
        if not ordered_recipes:
            if questionary.confirm(t("cli.exit_prompt")).ask():
                break
            continue

        try:
            asyncio.run(run_game(config, ordered_recipes, strategy=strategy, station=station))
        except KeyboardInterrupt:
            logger.info(t("cli.interrupted"))
        except Exception as e:
            logger.error(t("cli.game_error", error=str(e)), exc_info=True)

        if not questionary.confirm(t("cli.play_again")).ask():
            break


if __name__ == "__main__":
    main()
