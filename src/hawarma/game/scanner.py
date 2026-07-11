"""
订单扫描器

地位：从屏幕截图中检测订单，识别配方和加急状态
      轻量级实现，只检测订单，其他状态通过程序追踪

输入：屏幕截图、配方列表、配置对象
输出：检测到的订单信息

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新，同时更新所属目录的md
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from airtest.core.api import G, Template
from airtest.aircv import crop_image
from loguru import logger

from hawarma.config import AppConfig
from hawarma.paths import resolve_path
from hawarma.recipe import Recipe, Station
from hawarma.utils.image_utils import local_match


@dataclass
class DetectedOrder:
    """检测到的订单"""

    slot_idx: int
    recipe_slug: str
    is_rush: bool
    confidence: float


class Scanner:
    """
    订单扫描器

    只检测订单信息，不涉及其他状态（灶台、组装站、库存）。
    """

    def __init__(self, config: AppConfig, recipes: list[Recipe]):
        """
        初始化订单扫描器

        Args:
            config: 应用配置
            recipes: 配方列表
        """
        self.config = config
        self.recipes = recipes
        self.image_dir = resolve_path(config.image_directory)

        # 配方 slug -> Recipe 映射
        self._recipe_by_slug = {r.slug: r for r in recipes}

        # 首个食材 -> 菜谱列表映射（用于冲突检测）
        self._recipes_by_first_ingredient: dict[str, list[Recipe]] = {}
        for r in recipes:
            first_ing = r.raw_ingredients[0] if r.raw_ingredients else None
            if first_ing:
                self._recipes_by_first_ingredient.setdefault(first_ing, []).append(r)

        # 找出需要 cooker 二次匹配的冲突菜谱组（首个食材相同的菜谱）
        self._conflict_groups: list[tuple[Recipe, ...]] = []
        for ing, recipe_list in self._recipes_by_first_ingredient.items():
            if len(recipe_list) > 1:
                self._conflict_groups.append(tuple(recipe_list))

        logger.debug(
            f"Conflict groups (same first ingredient): {[(g[0].raw_ingredients[0], [r.slug for r in g]) for g in self._conflict_groups]}"
        )

        # Rush 检测配置
        self._rush_detection_positions = config.game.rush_detection_positions
        self._rush_red_threshold = config.game.rush_red_threshold

        # 调试配置
        self._save_screenshots = config.debug.save_order_screenshots
        self._screenshot_dir = Path(config.debug.screenshot_directory)
        self._save_timer_screenshots = config.debug.save_timer_screenshots
        self._timer_screenshot_dir = resolve_path(config.debug.timer_screenshot_directory)
        self._last_timer_save = 0.0

        # 创建截图目录
        if self._save_screenshots and not self._screenshot_dir.exists():
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        if self._save_timer_screenshots and not self._timer_screenshot_dir.exists():
            self._timer_screenshot_dir.mkdir(parents=True, exist_ok=True)

        # 注意：minicap 截图是延迟初始化的
        # 第一次调用 snapshot 时才会建立 stream 连接
        # 避免在初始化时预热导致帧缓冲区积累

        logger.info(
            f"Scanner initialized with {len(recipes)} recipes "
            f"(timer_threshold={self.config.matching.timer_threshold})"
        )

    async def detect_timer(self) -> bool:
        """
        检测 timer 图标（游戏开始标志）

        Returns:
            是否检测到 timer
        """
        screen = await asyncio.to_thread(G.DEVICE.snapshot)
        if screen is None:
            return False

        timer_path = self.image_dir / "icon-timer.jpg"
        if not timer_path.exists():
            logger.warning(f"Timer image not found: {timer_path}")
            return False

        roi = tuple(self.config.screen.timer_region)
        threshold = self.config.matching.timer_threshold
        match = local_match(Template(str(timer_path), threshold=threshold), roi, screen)
        if self._save_timer_screenshots and time.time() - self._last_timer_save >= 1.0:
            self._save_timer_screenshot(screen, roi, match is not None)
            self._last_timer_save = time.time()
        return match is not None

    def _save_timer_screenshot(
        self, screen, roi: tuple[int, int, int, int], matched: bool
    ) -> None:
        """保存 timer 检测时的截图（含 timer_region 裁剪），用于调试匹配失败"""
        try:
            import cv2
            import time

            cropped = crop_image(screen, roi)
            timestamp = int(time.time() * 1000)
            status = "hit" if matched else "miss"
            filename = f"timer_{timestamp}_{status}.jpg"
            cv2.imwrite(str(self._timer_screenshot_dir / filename), cropped)
            logger.debug(f"Saved timer debug screenshot: {filename}")
        except Exception as e:
            logger.warning(f"Failed to save timer debug screenshot: {e}")

    async def scan_orders(self) -> list[Optional[DetectedOrder]]:
        """
        扫描所有订单槽位

        Returns:
            4个槽位的检测结果列表
        """
        screen = await asyncio.to_thread(G.DEVICE.snapshot)
        if screen is None:
            return [None] * 4

        results = []
        detected_orders = []
        for slot_idx in range(4):
            order = self._detect_order(slot_idx, screen)
            results.append(order)
            if order is not None:
                detected_orders.append(order)

        # 如果检测到新订单且开启了调试选项，保存截图
        if detected_orders and self._save_screenshots:
            self._save_screenshot(detected_orders)

        return results

    async def scan_new_orders(self) -> list[DetectedOrder]:
        """
        扫描当前屏幕上的所有订单（返回快照，不做去重）
        去重由调用方（bridge）根据 environment 的订单状态决定。

        Returns:
            当前屏幕上的所有订单列表
        """
        current_orders = await self.scan_orders()
        result = []
        for slot_idx, order in enumerate(current_orders):
            if order is not None:
                result.append(order)
        return result

    def _detect_order(self, slot: int, screen) -> Optional[DetectedOrder]:
        """
        检测单个订单槽位

        Args:
            slot: 槽位索引 (0-3)
            screen: 屏幕截图

        Returns:
            检测到的订单，如果没有则返回 None
        """
        x1, y1, x2, y2 = self.config.screen.ingredients_regions[slot]
        ing_roi = (x1, y1, x1 + (x2 - x1) // 4, y2)

        best_match = None
        best_confidence = 0.0

        for recipe in self.recipes:
            ing_name = recipe.raw_ingredients[0] if recipe.raw_ingredients else None
            if not ing_name:
                continue

            ing_path = self.image_dir / f"ingredient-{ing_name}.jpg"
            if not ing_path.exists():
                ing_path = self.image_dir / f"icon-{ing_name}.jpg"
                if not ing_path.exists():
                    continue

            match = local_match(Template(str(ing_path)), ing_roi, screen)
            if match:
                confidence = float(match.get("confidence", 0))
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = recipe

        if best_match and best_confidence > 0.7:
            first_ing = best_match.raw_ingredients[0]
            candidates = self._recipes_by_first_ingredient.get(first_ing, [])

            if len(candidates) > 1:
                resolved = self._resolve_conflict(candidates, slot, screen)
                if resolved:
                    best_match = resolved

            is_rush = self._detect_rush(slot, screen)

            return DetectedOrder(
                slot_idx=slot,
                recipe_slug=best_match.slug,
                is_rush=is_rush,
                confidence=best_confidence,
            )

        return None

    def _resolve_conflict(
        self, candidates: list[Recipe], slot: int, screen
    ) -> Optional[Recipe]:
        """
        根据站点类型选择冲突解决策略。

        Gastronome: 通过 cooker 图标区分（同食材不同厨具 = 不同菜谱）
        Dessert:    通过第二食材区分（甜点无 cooker 图标）
        """
        if all(r.station == Station.GASTRONOME for r in candidates):
            return self._resolve_gastronome_conflict(candidates, slot, screen)
        return self._resolve_dessert_conflict(candidates, slot, screen)

    def _resolve_gastronome_conflict(
        self, candidates: list[Recipe], slot: int, screen
    ) -> Optional[Recipe]:
        """
        通过 cooker 图标区分共享首个食材的 gastronome 菜谱。

        订单卡片中，每个食材旁边显示对应的 cooker 图标。
        通过匹配第一个食材位置的 cooker 图标来确定正确的菜谱。

        Args:
            candidates: 首个食材相同的候选菜谱列表
            slot: 槽位索引
            screen: 屏幕截图

        Returns:
            区分后的正确菜谱，如果无法区分则返回 None
        """
        x1, y1, x2, y2 = self.config.screen.ingredients_regions[slot]
        cooker_roi = (x1, y1, x1 + (x2 - x1) // 4, y2)

        best_match = None
        best_confidence = 0.0

        for recipe in candidates:
            cooker_type = recipe.cookers[0]
            cooker_path = self.image_dir / f"cooker-{cooker_type}.jpg"
            if not cooker_path.exists():
                cooker_path = self.image_dir / f"icon-{cooker_type}.jpg"
                if not cooker_path.exists():
                    continue

            match = local_match(Template(str(cooker_path)), cooker_roi, screen)
            if match:
                confidence = float(match.get("confidence", 0))
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = recipe

        if best_match and best_confidence > 0.7:
            logger.debug(
                f"Resolved cooker conflict for {candidates[0].raw_ingredients[0]}: "
                f"{best_match.slug} (cooker confidence: {best_confidence:.2f})"
            )
            return best_match

        return None

    def _resolve_dessert_conflict(
        self, candidates: list[Recipe], slot: int, screen
    ) -> Optional[Recipe]:
        """
        通过第二食材区分共享首个食材的甜点菜谱。

        甜点无 cooker 图标，需匹配第二食材来区分。
        第二个食材的 ROI 是订单 ingredients region 的第二个 quarter。

        Args:
            candidates: 首个食材相同的候选菜谱列表
            slot: 槽位索引
            screen: 屏幕截图

        Returns:
            区分后的正确菜谱，如果无法区分则返回 None
        """
        x1, y1, x2, y2 = self.config.screen.ingredients_regions[slot]
        quarter_w = (x2 - x1) // 4
        ing2_roi = (x1 + quarter_w, y1, x1 + 2 * quarter_w, y2)

        best_match = None
        best_confidence = 0.0

        for recipe in candidates:
            if len(recipe.raw_ingredients) < 2:
                continue

            ing2_name = recipe.raw_ingredients[1]
            ing2_path = self.image_dir / f"ingredient-{ing2_name}.jpg"
            if not ing2_path.exists():
                ing2_path = self.image_dir / f"icon-{ing2_name}.jpg"
                if not ing2_path.exists():
                    continue

            match = local_match(Template(str(ing2_path)), ing2_roi, screen)
            if match:
                confidence = float(match.get("confidence", 0))
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = recipe

        if best_match and best_confidence > 0.6:
            logger.debug(
                f"Resolved conflict for {candidates[0].raw_ingredients[0]}: "
                f"{best_match.slug} (second ingredient confidence: {best_confidence:.2f})"
            )
            return best_match

        return None

    RUSH_DETECTION_POSITIONS = [
        (480, 195),
        (860, 195),
        (1230, 195),
        (1600, 195),
    ]
    RUSH_RED_THRESHOLD = 180

    def _detect_rush(self, slot: int, screen) -> bool:
        """
        检测是否 rush 订单

        Rush 订单背景为红色，通过检测特定像素点的红色值判断。

        Args:
            slot: 槽位索引
            screen: 屏幕截图

        Returns:
            是否 rush
        """
        # 使用配置中的值，如果没有则使用默认值
        positions = (
            self._rush_detection_positions
            if self._rush_detection_positions
            else self.RUSH_DETECTION_POSITIONS
        )
        threshold = (
            self._rush_red_threshold
            if self._rush_red_threshold
            else self.RUSH_RED_THRESHOLD
        )

        if slot >= len(positions):
            return False

        x, y = positions[slot]
        h, w = screen.shape[:2]

        if 0 <= y < h and 0 <= x < w:
            red_value = int(screen[y, x, 2])
            return red_value < threshold

        return False

    def get_recipe_by_slug(self, slug: str) -> Optional[Recipe]:
        """根据 slug 获取配方"""
        return self._recipe_by_slug.get(slug)

    def _save_screenshot(self, orders: list[DetectedOrder]) -> None:
        """保存调试截图"""
        if not self._save_screenshots:
            return

        try:
            screen = G.DEVICE.snapshot()
            if screen is not None:
                import cv2

                # 生成文件名: slotX-recipe-rush_time.jpg
                parts = []
                for order in orders:
                    rush_str = "rush" if order.is_rush else "normal"
                    parts.append(f"slot{order.slot_idx}_{order.recipe_slug}_{rush_str}")

                filename = "_".join(parts) + ".jpg"
                filepath = self._screenshot_dir / filename

                # 如果文件已存在，添加序号
                if filepath.exists():
                    counter = 1
                    while filepath.exists():
                        name_parts = filename.rsplit(".jpg", 1)
                        filepath = (
                            self._screenshot_dir / f"{name_parts[0]}_{counter}.jpg"
                        )
                        counter += 1

                cv2.imwrite(str(filepath), screen)
                logger.debug(f"Saved debug screenshot: {filepath}")
        except Exception as e:
            logger.warning(f"Failed to save debug screenshot: {e}")
