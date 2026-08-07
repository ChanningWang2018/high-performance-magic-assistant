# hawarma/config.py
"""
配置管理模块

地位：负责从YAML文件加载和验证应用配置，提供类型安全的配置访问

输入：YAML配置文件路径
输出：AppConfig对象（包含ScreenConfig和MatchingConfig）

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新，同时更新所属目录的md
"""

from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel, Field

from hawarma.paths import config_path as get_config_path


class ScreenConfig(BaseModel):
    resolution: tuple[int, int] = (1920, 1080)
    assembly_station_position: tuple[int, int] = (1375, 865)
    trash_position: tuple[int, int] = (25, 590)
    timer_region: tuple[int, int, int, int] = (0, 0, 400, 140)
    assembly_region: tuple[int, int, int, int] = (1150, 720, 1600, 1030)
    raw_ingredients_positions: list[tuple[int, int]] = Field(default_factory=lambda: [
        (115, 930), (265, 930), (150, 780), (300, 780),
        (200, 670), (350, 670), (255, 570), (390, 570),
    ])
    cookers_positions: list[tuple[int, int]] = Field(default_factory=lambda: [
        (595, 585), (850, 585), (1120, 585), (1370, 585),
    ])
    stockpile_positions: list[tuple[int, int]] = Field(default_factory=lambda: [
        (800, 900), (950, 900), (1100, 900),
    ])
    condiments_positions: list[tuple[int, int]] = Field(default_factory=lambda: [
        (1675, 915), (1825, 925), (1640, 800), (1800, 800),
        (1615, 685), (1775, 685), (1600, 550), (1725, 555),
    ])
    orders_regions: list[tuple[int, int, int, int]] = Field(default_factory=lambda: [
        (500, 80, 720, 210), (875, 80, 1095, 210),
        (1250, 80, 1470, 210), (1620, 80, 1840, 210),
    ])
    ingredients_regions: list[tuple[int, int, int, int]] = Field(default_factory=lambda: [
        (440, 250, 780, 385), (815, 250, 1155, 385),
        (1190, 250, 1530, 385), (1565, 250, 1905, 385),
    ])
    pickup_stations_positions: list[tuple[int, int]] = Field(default_factory=lambda: [
        (610, 135), (990, 135), (1360, 135), (1740, 135),
    ])


class MatchingConfig(BaseModel):
    ingredients_strategy: list[str] = Field(default_factory=lambda: ["tpl"])
    ingredients_threshold: float = 0.7
    save_best_match_images: bool = False
    assembly_threshold: float = 0.9
    timer_threshold: float = 0.7
    default_strategy: list[str] = Field(default_factory=list)


class GameConfig(BaseModel):
    cooker_retention: float = 5.0
    rush_red_threshold: int = 180
    rush_detection_positions: list[tuple[int, int]] = Field(default_factory=list)
    serve_verify_wait: float = 0.3
    swipe_params: dict[int, tuple[float, int]] = Field(
        default_factory=lambda: {
            400: (0.2, 10),
            600: (0.25, 12),
            800: (0.3, 15),
            1000: (0.35, 18),
        }
    )


class DebugConfig(BaseModel):
    save_order_screenshots: bool = False
    save_assembly_verify_screenshots: bool = False
    save_timer_screenshots: bool = False
    screenshot_directory: str = "logs/order_screenshots"
    timer_screenshot_directory: str = "logs/timer_screenshots"


class StirConfig(BaseModel):
    """搅拌操作配置（单次左滑）"""
    distance: int = 400
    duration: float = 1.5
    steps: int = 10


class DessertStationConfig(BaseModel):
    """甜点站点配置"""
    enabled: bool = True
    stir: StirConfig = Field(default_factory=StirConfig)
    mixing_bowl_position: tuple[int, int] = (1245, 870)
    cookers_positions: dict[str, tuple[int, int]] = Field(
        default_factory=lambda: {
            "dessert_oven": (715, 615),
            "cooling_plate": (1260, 590),
        }
    )
    cooker_retention: float = 5.0


class StationsConfig(BaseModel):
    """站点配置"""
    dessert: DessertStationConfig = Field(default_factory=DessertStationConfig)


class AppConfig(BaseModel):
    adb_address: str = "127.0.0.1:16384"
    image_directory: str = "static/img"
    log_directory: str = "logs"
    recipes_data_path: str = "data/recipes.json"
    episode_duration: int = 105
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    game: GameConfig = Field(default_factory=GameConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    strategy: str = "gastronome"
    """策略名称: gastronome (CPM enhanced cascade) 或 dessert"""
    language: str = "zh-CN"
    """界面语言（zh-CN / en-US），作用于 TUI 与 CLI"""
    stations: StationsConfig = Field(default_factory=StationsConfig)


def _default_config() -> AppConfig:
    """Create a default AppConfig with sensible defaults."""
    return AppConfig()


def load_config(config_path: Path | str | None = None) -> AppConfig:
    """Loads the application configuration from a YAML file.

    If the config file does not exist, generates a default one and saves it.
    """
    if config_path is None:
        config_path = get_config_path()

    path = Path(config_path)
    if not path.exists():
        logger.info(f"Config file not found at {path}, generating default config")
        config = _default_config()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_config(config, path)
        return config

    with open(path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
    return AppConfig.model_validate(config_data)


def save_config(config: AppConfig, config_path: Path | str | None = None) -> None:
    """Saves the application configuration to a YAML file."""
    if config_path is None:
        config_path = get_config_path()
    # model_dump with mode='json' converts tuples to lists for clean YAML output
    config_data = config.model_dump(mode="json")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# Example of how to use it (optional, for direct testing)
if __name__ == "__main__":
    config = load_config()
    print(config)
