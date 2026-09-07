"""
UI 操作执行器

地位：封装所有 swipe 操作，提供坐标映射和异步执行
      从 config.yaml 读取坐标配置
      根据菜谱选择顺序动态确定元素坐标

输入：符号化操作（食材名、灶台名等）
输出：swipe 操作执行结果

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新，同时更新所属目录的md
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from loguru import logger

from hawarma.config import AppConfig
from hawarma.recipe import Recipe, Station

from hawarma.game.patch_maxtouch import apply_patch
from hawarma.game.timing import ExecutionTiming

apply_patch()


class Operator:
    """
    UI 操作执行器
    
    将符号化操作转换为屏幕坐标，并执行 swipe 操作。
    """

    def __init__(self, config: AppConfig, recipes: list[Recipe], station: Station = Station.GASTRONOME):
        """
        初始化 Operator
        
        Args:
            config: 应用配置
            recipes: 选中的配方列表（用于确定食材位置）
            station: 站点类型，默认 GASTRONOME
        """
        self.config = config
        self.recipes = recipes
        self._station = station

        # 坐标映射
        self._ingredient_positions: dict[str, tuple[int, int]] = {}
        self._cooker_positions: dict[str, tuple[int, int]] = {}
        self._condiment_positions: dict[str, tuple[int, int]] = {}
        self._stockpile_positions: dict[str, tuple[int, int]] = {}
        self._assembly_position: tuple[int, int] = tuple(config.screen.assembly_station_position)
        self._trash_position: tuple[int, int] = tuple(config.screen.trash_position)
        self._pickup_positions: list[tuple[int, int]] = [
            tuple(pos) for pos in config.screen.pickup_stations_positions
        ]
        self._mixing_bowl_position: tuple[int, int] = tuple(config.stations.dessert.mixing_bowl_position)
        
        # 异步锁
        self._lock = asyncio.Lock()

        # 滑动往返耗时观测（只观测不改行为）
        self.timing = ExecutionTiming()

        # 构建坐标映射
        self._build_mappings()
        
        logger.info(f"Operator initialized with {len(self._ingredient_positions)} ingredients")
    
    @property
    def cooker_positions(self) -> dict[str, tuple[int, int]]:
        """灶台名称 → 坐标映射（公共接口）"""
        return self._cooker_positions

    def _build_mappings(self) -> None:
        """
        构建坐标映射
        
        根据菜谱选择顺序动态确定各元素位置：
        1. Cookers: 按cookers_layout去重，根据种类数量选择槽位
        2. Ingredients: 按raw_ingredients去重，反转顺序后分配索引
        3. Condiments: 按condiments去重，顺序分配索引
        """
        # 收集所有食材（按选中配方的顺序）
        all_ingredients = []
        for recipe in self.recipes:
            for ing in recipe.raw_ingredients:
                if ing not in all_ingredients:
                    all_ingredients.append(ing)
        
        # 食材位置：反转顺序后分配索引（从下到上、从左到右）
        positions = self.config.screen.raw_ingredients_positions
        reversed_ingredients = list(reversed(all_ingredients))
        for i, ing in enumerate(reversed_ingredients):
            if i < len(positions):
                self._ingredient_positions[ing] = tuple(positions[i])
        
        # 收集所有cooker（按选中配方的cookers_layout顺序）
        all_cookers = []
        for recipe in self.recipes:
            for cooker in recipe.cookers_layout:
                if cooker not in all_cookers:
                    all_cookers.append(cooker)
        
        if self._station == Station.DESSERT:
            # ── Dessert：固定坐标 ──
            cooker_positions_config = self.config.stations.dessert.cookers_positions
            for cooker_name, pos in cooker_positions_config.items():
                self._cooker_positions[cooker_name] = tuple(pos)
        else:
            # ── Gastronome：槽位分配 ──
            # 灶台位置：根据种类数量选择槽位
            # 1种→[1]，2种→[1,2]，3种→[0,1,2]，4种→[0,1,2,3]
            cooker_positions = self.config.screen.cookers_positions
            num_cookers = len(all_cookers)

            if num_cookers == 1:
                slot_indices = [1]
            elif num_cookers == 2:
                slot_indices = [1, 2]
            elif num_cookers == 3:
                slot_indices = [0, 1, 2]
            else:  # 4 or more
                slot_indices = [0, 1, 2, 3]

            for i, cooker in enumerate(all_cookers):
                if i < len(slot_indices):
                    slot_idx = slot_indices[i]
                    if slot_idx < len(cooker_positions):
                        self._cooker_positions[cooker] = tuple(cooker_positions[slot_idx])
        
        # 收集所有condiment（按选中配方的顺序）
        all_condiments = []
        for recipe in self.recipes:
            for cond in recipe.condiments:
                if cond not in all_condiments:
                    all_condiments.append(cond)
        
        # 调料位置：顺序分配索引（从下到上、从左到右）
        condiment_positions = self.config.screen.condiments_positions
        for i, cond in enumerate(all_condiments):
            if i < len(condiment_positions):
                self._condiment_positions[cond] = tuple(condiment_positions[i])
        
        # 库存位置
        stockpile_positions = self.config.screen.stockpile_positions
        for i, pos in enumerate(stockpile_positions):
            self._stockpile_positions[f"slot{i}"] = tuple(pos)
    
    # ========================================================================
    # 核心操作
    # ========================================================================
    
    async def swipe(self, start: tuple[int, int], end: tuple[int, int], duration: float = 0.1, steps: int = 5) -> None:
        """
        执行 swipe 操作
        
        直接使用 Airtest 的 swipe API。
        注意：设备使用的是 Maxtouch（Android 10+），实际 duration 会比设置值略长。
        
        Args:
            start: 起始坐标
            end: 结束坐标
            duration: 持续时间
            steps: 滑动步数（建议5）
        """
        async with self._lock:
            from airtest.core.api import swipe
            t0 = time.perf_counter()
            swipe(start, end, duration=duration, steps=steps)
            await asyncio.sleep(0.01)
            dt = time.perf_counter() - t0
            self.timing.swipe.record(dt)
            logger.debug(f"Swipe {start}->{end} round-trip: {dt:.4f}s")
    
    async def cook(self, ingredient: str, cooker: str) -> None:
        """
        烹饪食材
        
        Args:
            ingredient: 食材名称
            cooker: 灶台名称
        """
        ing_pos = self._get_ingredient_position(ingredient)
        cooker_pos = self._get_cooker_position(cooker)
        
        await self.swipe(ing_pos, cooker_pos, duration=0.1)
        logger.debug(f"Cooking {ingredient} on {cooker}")
    
    async def move_to_assembly(self, cooker: str) -> None:
        """
        将灶台食材移动到组装站
        
        Args:
            cooker: 灶台名称
        """
        cooker_pos = self._get_cooker_position(cooker)
        await self.swipe(cooker_pos, self._assembly_position, duration=0.1)
        logger.debug(f"Moved from {cooker} to assembly")
    
    async def move_to_stockpile(self, cooker: str, slot: str) -> None:
        """
        将灶台食材移动到库存
        
        Args:
            cooker: 灶台名称
            slot: 库存槽位名称
        """
        cooker_pos = self._get_cooker_position(cooker)
        stockpile_pos = self._get_stockpile_position(slot)
        await self.swipe(cooker_pos, stockpile_pos)
        logger.debug(f"Moved from {cooker} to {slot}")
    
    async def pull_from_stockpile(self, slot: str) -> None:
        """
        从库存取用食材到组装站
        
        Args:
            slot: 库存槽位名称
        """
        stockpile_pos = self._get_stockpile_position(slot)
        await self.swipe(stockpile_pos, self._assembly_position)
        logger.debug(f"Pulled from {slot} to assembly")
    
    async def add_condiment(self, condiment: str) -> None:
        """
        添加调料到组装站
        
        Args:
            condiment: 调料名称
        """
        condiment_pos = self._get_condiment_position(condiment)
        await self.swipe(condiment_pos, self._assembly_position, duration=0.1)
        logger.debug(f"Added condiment {condiment}")
    
    async def serve_order(self, slot_idx: int) -> None:
        """
        送餐
        
        Args:
            slot_idx: 订单槽位索引
        """
        pickup_pos = self._pickup_positions[slot_idx]
        distance = self._calculate_distance(self._assembly_position, pickup_pos)
        duration, steps = self._calculate_swipe_params(distance)
        await self.swipe(self._assembly_position, pickup_pos, duration=duration, steps=steps)
        logger.debug(f"Served order to slot {slot_idx}")
    
    def _calculate_distance(self, pos1: tuple[int, int], pos2: tuple[int, int]) -> float:
        """计算两点之间的欧氏距离"""
        dx = pos2[0] - pos1[0]
        dy = pos2[1] - pos1[1]
        return (dx ** 2 + dy ** 2) ** 0.5
    
    def _calculate_swipe_params(self, distance: float) -> tuple[float, int]:
        """
        根据距离计算swipe参数
        距离越远，duration和steps越大

        注：swipe方法内部已有0.01s等待让UI事件生效，可适当降低参数
        """
        for threshold, (duration, steps) in self.config.game.swipe_params.items():
            if distance < threshold:
                return duration, steps
        last = list(self.config.game.swipe_params.values())[-1]
        return last
    
    async def clear_cooker(self, cooker: str) -> None:
        """
        清理灶台（丢弃过期食材）
        
        Args:
            cooker: 灶台名称
        """
        cooker_pos = self._get_cooker_position(cooker)
        await self.swipe(cooker_pos, self._trash_position, duration=0.4, steps=16)
        logger.debug(f"Cleared cooker {cooker}")
    
    async def clear_assembly(self) -> None:
        """
        清空组装站（丢弃食材到垃圾桶）
        """
        await self.swipe(self._assembly_position, self._trash_position, duration=0.4, steps=16)
        logger.debug(f"Cleared assembly station")

    # ========================================================================
    # Dessert 操作
    # ========================================================================

    async def move_to_mixing_bowl(self, ingredient: str) -> None:
        """食材 → 搅拌盆"""
        ing_pos = self._get_ingredient_position(ingredient)
        await self.swipe(ing_pos, self._mixing_bowl_position, duration=0.1)
        logger.debug(f"Moved {ingredient} to mixing bowl")

    async def add_condiment_to_mixing_bowl(self, condiment: str) -> None:
        """调料 → 搅拌盆"""
        cond_pos = self._get_condiment_position(condiment)
        await self.swipe(cond_pos, self._mixing_bowl_position, duration=0.1)
        logger.debug(f"Added condiment {condiment} to mixing bowl")

    async def stir(self, distance: float = 400.0, duration: float = 1.5, steps: int = 10) -> None:
        """搅拌：从搅拌盆坐标向左水平滑动"""
        x, y = self._mixing_bowl_position
        end_x = x - int(distance)
        await self.swipe((x, y), (end_x, y), duration=duration, steps=steps)
        logger.debug(f"Stirred mixing bowl ({distance}px, {duration}s, {steps} steps)")

    async def move_mixing_bowl_to_cooker(self, cooker: str) -> None:
        """搅拌盆 → 灶台"""
        cooker_pos = self._get_cooker_position(cooker)
        await self.swipe(self._mixing_bowl_position, cooker_pos, duration=0.1)
        logger.debug(f"Moved mixing bowl to {cooker}")

    async def serve_from_cooker(self, cooker: str, slot_idx: int) -> None:
        """灶台 → 取餐台"""
        cooker_pos = self._get_cooker_position(cooker)
        pickup_pos = self._pickup_positions[slot_idx]
        distance = self._calculate_distance(cooker_pos, pickup_pos)
        duration, steps = self._calculate_swipe_params(distance)
        await self.swipe(cooker_pos, pickup_pos, duration=duration, steps=steps)
        logger.debug(f"Served from {cooker} to slot {slot_idx}")

    async def clear_mixing_bowl(self) -> None:
        """清空搅拌盆"""
        await self.swipe(self._mixing_bowl_position, self._trash_position, duration=0.4, steps=16)
        logger.debug(f"Cleared mixing bowl")

    # ========================================================================
    # 坐标查询
    # ========================================================================
    
    def _get_ingredient_position(self, ingredient: str) -> tuple[int, int]:
        """获取食材位置"""
        if ingredient in self._ingredient_positions:
            return self._ingredient_positions[ingredient]
        raise ValueError(f"Unknown ingredient: {ingredient}")
    
    def _get_cooker_position(self, cooker: str) -> tuple[int, int]:
        """获取灶台位置"""
        if cooker in self._cooker_positions:
            return self._cooker_positions[cooker]
        raise ValueError(f"Unknown cooker: {cooker}")
    
    def _get_condiment_position(self, condiment: str) -> tuple[int, int]:
        """获取调料位置"""
        if condiment in self._condiment_positions:
            return self._condiment_positions[condiment]
        # fallback: deterministic index from codepoint sum
        positions = self.config.screen.condiments_positions
        idx = sum(ord(c) for c in condiment) % len(positions)
        return tuple(positions[idx])
    
    def _get_stockpile_position(self, slot: str) -> tuple[int, int]:
        """获取库存位置"""
        if slot in self._stockpile_positions:
            return self._stockpile_positions[slot]
        raise ValueError(f"Unknown stockpile slot: {slot}")
    
    # ========================================================================
    # 调试
    # ========================================================================
    
    def get_mappings(self) -> dict:
        """获取坐标映射（用于调试）"""
        return {
            "ingredients": self._ingredient_positions,
            "cookers": self._cooker_positions,
            "condiments": self._condiment_positions,
            "stockpile": self._stockpile_positions,
            "assembly": self._assembly_position,
            "pickups": self._pickup_positions,
        }
