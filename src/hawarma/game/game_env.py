"""
游戏环境（真实游戏）

GameEnv 追踪真实游戏的状态，通过程序逻辑维护灶台、组装站、库存状态。
不继承 ABC — 真实环境和模拟环境本质不同（同步 vs 异步），通过 UnifiedState + Action 共享数据契约。

输入：UI操作结果、订单检测结果
输出：游戏状态供Agent决策

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新，同时更新所属目录的md
"""

from __future__ import annotations

import time

from loguru import logger

from hawarma.core.models import (
    AssemblyState,
    CookerState,
    MixingBowlState,
    Order,
    StockpileSlot,
)
from hawarma.core.reward import RecipeRewardLookup
from hawarma.core.scoring import ScoringContext
from hawarma.recipe import Recipe, Station


class GameEnv:
    """
    真实游戏环境

    同时实现 GastronomeEnv 和 DessertEnv。
    根据当前 station 模式，只有对应接口的方法会被调用。
    """

    def __init__(
        self,
        cooker_names: list[str],
        stockpile_slots: int = 3,
        game_duration: float = 90.0,
        recipes: dict[str, Recipe] | None = None,
        cooker_retention: float = 5.0,
    ):
        self._cooker_retention = cooker_retention
        self._cookers: dict[str, CookerState] = {
            name: CookerState(cooker_type=name) for name in cooker_names
        }

        self._assembly = AssemblyState()
        self._mixing_bowl = MixingBowlState()

        self._stockpile: dict[str, StockpileSlot] = {
            f"slot{i}": StockpileSlot() for i in range(stockpile_slots)
        }

        self._orders: list[Order | None] = [None] * 4

        self._game_start_time: float | None = None
        self._game_duration = game_duration
        self._animation_until: float = 0.0
        self._next_order_id = 1

        # 配方数据（用于校验组装站操作）
        self._recipes = recipes or {}

        # 度量上下文（统计查询、总分、终局结算唯一持有者；S1 抽离）
        self._scoring = ScoringContext()

        # 得分查表（lazy init 在 on_order_served 中需要时再创建，
        # 避免 GameEnv 在 unit test 中加载 CSV）
        self._reward_lookup: RecipeRewardLookup | None = None

        logger.info(
            f"GameEnv initialized: {len(cooker_names)} cookers, {stockpile_slots} stockpile slots"
        )

    # ========================================================================
    # 度量兼容访问（S1 抽离过渡期：读写转发到 ScoringContext）
    # ========================================================================

    @property
    def _orders_served(self) -> int:
        return self._scoring.orders_served

    @_orders_served.setter
    def _orders_served(self, value: int) -> None:
        self._scoring.orders_served = value
        self._scoring.invalidate()

    @property
    def _total_score(self) -> float:
        return self._scoring.total_score

    @_total_score.setter
    def _total_score(self, value: float) -> None:
        self._scoring.total_score = value
        self._scoring.invalidate()

    @property
    def _total_visibility(self) -> float:
        return self._scoring.total_visibility

    @_total_visibility.setter
    def _total_visibility(self, value: float) -> None:
        self._scoring.total_visibility = value
        self._scoring.invalidate()

    @property
    def _orders_timeout(self) -> int:
        return self._scoring.orders_timeout

    @_orders_timeout.setter
    def _orders_timeout(self, value: int) -> None:
        self._scoring.orders_timeout = value

    @property
    def _actions_taken(self) -> int:
        return self._scoring.actions_taken

    @_actions_taken.setter
    def _actions_taken(self, value: int) -> None:
        self._scoring.actions_taken = value

    # ========================================================================
    # Env 接口实现
    # ========================================================================

    @property
    def time(self) -> float:
        """当前游戏时间（秒）"""
        if self._game_start_time is None:
            return 0.0
        return time.time() - self._game_start_time

    @property
    def orders(self) -> list[Order | None]:
        """当前订单列表"""
        return self._orders

    @property
    def cookers(self) -> dict[str, CookerState]:
        """灶台状态"""
        return self._cookers

    @property
    def assembly(self) -> AssemblyState:
        """组装站状态"""
        return self._assembly

    @property
    def stockpile(self) -> dict[str, StockpileSlot]:
        """库存状态"""
        return self._stockpile

    @property
    def mixing_bowl(self) -> MixingBowlState:
        """搅拌盆状态（甜点专用）"""
        return self._mixing_bowl

    def is_in_animation_window(self) -> bool:
        """是否在动画窗口期间"""
        return time.time() < self._animation_until

    def animation_window_remaining(self) -> float:
        """动画窗口剩余时长（秒）。

        只观测不改行为：供扫描循环/日志判断「新单发现滞后里动画占几成」。
        不在动画窗口内返回 0.0。
        """
        return max(0.0, self._animation_until - time.time())

    def start_cooking(self, ingredient: str, cooker: str, duration: float) -> bool:
        """开始烹饪"""
        if cooker not in self._cookers:
            logger.error(f"Unknown cooker: {cooker}")
            return False

        cooker_state = self._cookers[cooker]
        if cooker_state.busy:
            logger.warning(f"Cooker {cooker} is busy")
            return False

        cooker_state.busy = True
        cooker_state.item_name = ingredient
        cooker_state.started_at = self.time
        cooker_state.done_at = self.time + duration
        cooker_state.expired_at = self.time + duration + self._cooker_retention
        return True

    def move_to_assembly(self, cooker: str) -> bool:
        """将灶台完成的食材移动到组装站"""
        if cooker not in self._cookers:
            return False

        cooker_state = self._cookers[cooker]
        if not cooker_state.busy or cooker_state.done_at is None:
            return False

        ingredient = cooker_state.item_name
        cooker_type = cooker_state.cooker_type

        # 食材已过期，拒绝移动
        if cooker_state.is_expired(self.time):
            logger.warning(
                f"[t={self.time:.1f}s] Ingredient {ingredient} on {cooker} expired, cannot move to assembly"
            )
            return False

        # 如果组装站为空，根据(ingredient, cooker)推断目标配方
        if self._assembly.is_free:
            inferred_slug = self.get_recipe_for_ingredient_cooker(ingredient, cooker_type)
            if inferred_slug:
                self._assembly.target_recipe_slug = inferred_slug

        self._assembly.ingredients.append((ingredient, cooker_type, 0.0))
        cooker_state.reset()
        return True

    def move_to_stockpile(self, cooker: str, slot: str) -> bool:
        """将灶台食材移动到库存"""
        if cooker not in self._cookers:
            return False

        cooker_state = self._cookers[cooker]
        if not cooker_state.busy:
            return False

        if slot not in self._stockpile:
            return False

        stockpile_slot = self._stockpile[slot]
        if not stockpile_slot.add(
            cooker_state.item_name, cooker_state.cooker_type
        ):
            return False

        self._cookers[cooker].reset()
        return True

    def pull_from_stockpile(self, slot: str) -> bool:
        """从库存取用食材到组装站"""
        if slot not in self._stockpile:
            return False

        stockpile_slot = self._stockpile[slot]
        if stockpile_slot.count <= 0:
            return False

        ingredient = stockpile_slot.item_name
        cooker_type = stockpile_slot.cooker_type

        # 如果组装站为空，根据(ingredient, cooker)推断目标配方
        if self._assembly.is_free:
            inferred_slug = self.get_recipe_for_ingredient_cooker(ingredient, cooker_type)
            if inferred_slug:
                self._assembly.target_recipe_slug = inferred_slug

        self._assembly.ingredients.append((ingredient, cooker_type, 0.0))
        stockpile_slot.remove()
        return True

    def add_condiment(self, condiment: str) -> bool:
        """添加调料到组装站（校验目标配方）"""
        if self._assembly.target_recipe_slug:
            recipe = self._recipes.get(self._assembly.target_recipe_slug)
            if recipe is not None:
                condiments = recipe.condiments
                max_count = condiments.get(condiment, 0)
                valid = max_count > 0

                if not valid:
                    logger.warning(
                        f"[t={self.time:.1f}s] Condiment {condiment} not in recipe {self._assembly.target_recipe_slug}"
                    )
                    return False
                current = self._assembly.condiments.get(condiment, 0)
                if current >= max_count:
                    logger.warning(
                        f"[t={self.time:.1f}s] Condiment {condiment} already at max ({max_count}) for recipe {self._assembly.target_recipe_slug}"
                    )
                    return False

        current = self._assembly.condiments.get(condiment, 0)
        self._assembly.condiments[condiment] = current + 1
        return True

    def serve_order(self, slot_idx: int) -> bool:
        """送餐"""
        if slot_idx < 0 or slot_idx >= len(self._orders):
            return False

        order = self._orders[slot_idx]
        if order is None:
            return False

        order.done = True
        self._assembly.reset()
        self.set_animation_window(1.5)

        # 送餐后移除该订单
        self._orders[slot_idx] = None
        # 左移槽位
        self._shift_orders_left()

        logger.info(
            f"[t={self.time:.1f}s] Served order {order.order_id} ({order.recipe_slug}) slot {slot_idx} {'RUSH' if order.is_rush else 'normal'}"
        )
        return True

    def clear_cooker(self, cooker: str) -> bool:
        """清理灶台"""
        if cooker not in self._cookers:
            return False
        self._cookers[cooker].reset()
        return True

    def clear_assembly(self) -> bool:
        """清空组装站"""
        if not self._assembly.ingredients:
            return False
        self._assembly.reset()
        return True

    # ========================================================================
    # Dessert 方法
    # ========================================================================

    def add_to_mixing_bowl(self, ingredient: str, recipe_slug: str | None = None) -> bool:
        """食材 → 搅拌盆"""
        if len(self._mixing_bowl.ingredients) >= 2:
            logger.warning(f"[t={self.time:.1f}s] Mixing bowl full, cannot add {ingredient}")
            return False

        if self._mixing_bowl.is_empty:
            if recipe_slug:
                self._mixing_bowl.target_recipe_slug = recipe_slug
            else:
                inferred = self._infer_dessert_recipe(ingredient)
                if inferred:
                    self._mixing_bowl.target_recipe_slug = inferred

        if self._mixing_bowl.target_recipe_slug:
            recipe = self._recipes.get(self._mixing_bowl.target_recipe_slug)
            if recipe:
                raw_ings = recipe.raw_ingredients
                if ingredient not in raw_ings:
                    logger.warning(
                        f"[t={self.time:.1f}s] Ingredient {ingredient} not in dessert recipe {self._mixing_bowl.target_recipe_slug}"
                    )
                    return False

        if ingredient in self._mixing_bowl.ingredients:
            logger.warning(f"[t={self.time:.1f}s] Ingredient {ingredient} already in mixing bowl")
            return False

        self._mixing_bowl.ingredients.append(ingredient)
        logger.info(f"[t={self.time:.1f}s] Added {ingredient} to mixing bowl")
        return True

    def add_condiment_to_mixing_bowl(self, condiment: str) -> bool:
        """调料 → 搅拌盆"""
        if self._mixing_bowl.is_empty:
            logger.warning(f"[t={self.time:.1f}s] Mixing bowl is empty, cannot add condiment")
            return False

        if self._mixing_bowl.target_recipe_slug:
            recipe = self._recipes.get(self._mixing_bowl.target_recipe_slug)
            if recipe is not None:
                condiments = recipe.condiments
                max_count = condiments.get(condiment, 0)
                valid = max_count > 0

                if not valid:
                    logger.warning(
                        f"[t={self.time:.1f}s] Condiment {condiment} not in recipe {self._mixing_bowl.target_recipe_slug}"
                    )
                    return False
                current = self._mixing_bowl.condiments.get(condiment, 0)
                if current >= max_count:
                    logger.warning(
                        f"[t={self.time:.1f}s] Condiment {condiment} already at max ({max_count})"
                    )
                    return False

        self._mixing_bowl.condiments[condiment] = self._mixing_bowl.condiments.get(condiment, 0) + 1
        logger.info(f"[t={self.time:.1f}s] Added condiment {condiment} to mixing bowl")
        return True

    def stir_mixing_bowl(self) -> bool:
        """搅拌操作"""
        if self._mixing_bowl.is_empty:
            logger.warning(f"[t={self.time:.1f}s] Mixing bowl is empty, cannot stir")
            return False
        if self._mixing_bowl.is_stirred:
            logger.warning(f"[t={self.time:.1f}s] Mixing bowl already stirred")
            return False
        self._mixing_bowl.is_stirred = True
        logger.info(f"[t={self.time:.1f}s] Stirred mixing bowl")
        return True

    def move_mixing_bowl_to_cooker(self, cooker: str) -> bool:
        """搅拌盆 → 灶台"""
        if not self._mixing_bowl.is_stirred:
            logger.warning(f"[t={self.time:.1f}s] Mixing bowl not stirred")
            return False
        if cooker not in self._cookers:
            logger.error(f"Unknown cooker: {cooker}")
            return False
        cooker_state = self._cookers[cooker]
        if cooker_state.busy:
            logger.warning(f"Cooker {cooker} is busy")
            return False

        recipe = self._recipes.get(self._mixing_bowl.target_recipe_slug)
        if not recipe:
            logger.error(f"Recipe not found: {self._mixing_bowl.target_recipe_slug}")
            return False

        cookers_list = recipe.cookers
        durations = recipe.cook_durations
        if not cookers_list or not durations:
            logger.error(f"Recipe {self._mixing_bowl.target_recipe_slug} has no cookers/durations")
            return False
        if cookers_list[0] != cooker:
            logger.warning(f"Recipe requires {cookers_list[0]}, not {cooker}")
            return False

        duration = durations[0]
        cooker_state.busy = True
        cooker_state.item_name = self._mixing_bowl.target_recipe_slug
        cooker_state.cooker_type = cooker
        cooker_state.started_at = self.time
        cooker_state.done_at = self.time + duration
        cooker_state.expired_at = self.time + duration + self._cooker_retention

        self._mixing_bowl.reset()
        logger.info(f"[t={self.time:.1f}s] Moved mixing bowl to {cooker} ({duration}s)")
        return True

    def serve_from_cooker(self, cooker: str, slot_idx: int) -> bool:
        """灶台 → 取餐台"""
        if cooker not in self._cookers:
            return False
        cooker_state = self._cookers[cooker]
        if not cooker_state.busy or cooker_state.done_at is None:
            return False
        if self.time < cooker_state.done_at:
            logger.warning(f"[t={self.time:.1f}s] Cooker {cooker} not done yet")
            return False
        if cooker_state.is_expired(self.time):
            logger.warning(f"[t={self.time:.1f}s] Cooker {cooker} expired")
            return False
        if slot_idx < 0 or slot_idx >= len(self._orders):
            return False
        order = self._orders[slot_idx]
        if order is None:
            return False
        recipe_slug = cooker_state.item_name
        if order.recipe_slug != recipe_slug:
            logger.warning(
                f"[t={self.time:.1f}s] Order {order.order_id} expects {order.recipe_slug}, not {recipe_slug}"
            )
            return False

        order.done = True
        cooker_state.reset()
        self.set_animation_window(1.5)
        self._orders[slot_idx] = None
        self._shift_orders_left()
        logger.info(f"[t={self.time:.1f}s] Served dessert {recipe_slug} from {cooker} slot {slot_idx}")
        return True

    def clear_mixing_bowl(self) -> bool:
        """清空搅拌盆"""
        if self._mixing_bowl.is_empty:
            return False
        discarded = self._mixing_bowl.ingredients.copy()
        self._mixing_bowl.reset()
        logger.info(f"[t={self.time:.1f}s] Cleared mixing bowl (discarded: {discarded})")
        return True

    def _infer_dessert_recipe(self, ingredient: str) -> str | None:
        """根据单个食材推断甜点配方"""
        from hawarma.recipe import Station
        for order in self._orders:
            if order and not order.done:
                recipe = self._recipes.get(order.recipe_slug)
                if recipe:
                    station = recipe.station
                    if station == Station.DESSERT:
                        raw_ings = recipe.raw_ingredients
                        if ingredient in raw_ings:
                            return order.recipe_slug
        return None

    # ========================================================================
    # 统一状态接口
    # ========================================================================

    def get_unified_state(self) -> "UnifiedState":
        """构建 UnifiedState 快照"""
        from hawarma.core.state import UnifiedState

        assembly = self._assembly
        orders = []
        for order in self._orders:
            if order is None:
                orders.append(None)
            else:
                orders.append(order)

        return UnifiedState(
            time=self.time,
            orders=tuple(orders),
            cookers=dict(self._cookers),
            assembly=assembly,
            stockpile=dict(self._stockpile),
            recipes=dict(self._recipes),
            game_duration=self._game_duration,
            is_in_animation_window=self.is_in_animation_window(),
            mixing_bowl=self._mixing_bowl,
        )

    def get_stats(self) -> dict:
        stats = self._scoring.get_stats()
        return {
            "time": self.time,
            **stats,
        }

    def finalize(self) -> float:
        """终局结算（幂等）：总分 = 订单得分和 + 总 visibility."""
        return self._scoring.finalize()

    def on_order_served(self, order: Order, has_condiments: bool) -> None:
        """
        记录订单完成事件，按游戏规则计算实际得分。

        与模拟器对齐（env_simulator.py:1170-1206）：
            1. 按 serve 瞬间的 recipe + has_condiments + 订单生成时锁定的
               spawned_at_visibility 计算该订单得分
            2. 把该订单的 visibility 加到局内总 visibility
               （影响后续订单的 spawned_at_visibility 与最终总分）

        调用方（runner）需在调用本方法前：
            - 从 env.orders[slot_idx] 拿到 order 引用
            - 在 env.serve_order / serve_from_cooker 清空 assembly 之前
              用 env.assembly.condiments 判断 has_condiments
        """
        if self._reward_lookup is None:
            self._reward_lookup = RecipeRewardLookup()

        score = self._reward_lookup.get_score(
            order.recipe_slug,
            has_condiments=has_condiments,
            is_rush=order.is_rush,
            total_visibility=order.spawned_at_visibility,
        )
        visibility = self._reward_lookup.get_visibility(
            order.recipe_slug, has_condiments
        )

        self._scoring.record_serve(score, visibility)

        logger.info(
            f"[t={self.time:.1f}s] Scored order {order.order_id} ({order.recipe_slug}, "
            f"{'RUSH' if order.is_rush else 'normal'}, cond={has_condiments}): "
            f"+{score:.0f} score, +{visibility} visibility "
            f"(spawned_at={order.spawned_at_visibility:.0f}, "
            f"total_vis={self._total_visibility:.0f})"
        )

    def on_order_timeout(self, order_id: int) -> None:
        self._scoring.record_timeout()

    def on_action_taken(self) -> None:
        """每次执行动作时调用，更新统计"""
        self._scoring.record_action()

    # ========================================================================
    # 扩展方法（GameEnv 特有）
    # ========================================================================

    def start_game(self) -> None:
        """开始游戏计时"""
        self._game_start_time = time.time()

    def is_game_over(self) -> bool:
        """游戏是否结束"""
        if self._game_start_time is None:
            return False
        return self.time >= self._game_duration

    def set_animation_window(self, duration: float = 1.5) -> None:
        """设置动画窗口"""
        self._animation_until = time.time() + duration

    def add_to_assembly(
        self,
        ingredient: str,
        cooker: str,
        order_id: int | None = None,
        recipe_slug: str | None = None,
    ) -> bool:
        """添加食材到组装站（带配方关联，校验食材合法性）"""
        if self._assembly.is_free:
            self._assembly.owner_order_id = order_id
            if recipe_slug:
                self._assembly.target_recipe_slug = recipe_slug
            elif order_id:
                order = self.get_order_by_id(order_id)
                if order:
                    self._assembly.target_recipe_slug = order.recipe_slug
                else:
                    self._assembly.target_recipe_slug = (
                        self.get_recipe_for_ingredient_cooker(ingredient, cooker)
                    )
            else:
                self._assembly.target_recipe_slug = (
                    self.get_recipe_for_ingredient_cooker(ingredient, cooker)
                )

        # 如果组装站有食材但没有目标配方，尝试推断
        if not self._assembly.target_recipe_slug and self._assembly.ingredients:
            all_ings = [t[0] for t in self._assembly.ingredients] + [ingredient]
            inferred = self._infer_recipe_slug_from_ingredients(all_ings)
            if inferred:
                self._assembly.target_recipe_slug = inferred

        # 校验食材是否属于目标配方
        raw_ings = []
        if self._assembly.target_recipe_slug:
            recipe = self._recipes.get(self._assembly.target_recipe_slug)
            if recipe is not None:
                raw_ings = recipe.raw_ingredients
                if ingredient not in raw_ings:
                    logger.warning(
                        f"[t={self.time:.1f}s] Ingredient {ingredient} not in recipe {self._assembly.target_recipe_slug}"
                    )
                    return False

        # 检查是否重复添加（按 ingredient-cooker 组合统计）
        already_added = sum(
            1 for t in self._assembly.ingredients if t[0] == ingredient
        )
        needed = raw_ings.count(ingredient) if raw_ings else 1
        if already_added >= needed:
            logger.warning(
                f"[t={self.time:.1f}s] Ingredient {ingredient} already at max ({needed}) for recipe {self._assembly.target_recipe_slug}"
            )
            return False

        self._assembly.ingredients.append((ingredient, cooker, 0.0))
        return True

    def get_condiment_count(self, condiment: str) -> int:
        """获取已添加的调料数量"""
        return self._assembly.condiments.get(condiment, 0)

    def _shift_orders_left(self) -> None:
        """压缩订单槽位，将 None 移到末尾"""
        non_null = [o for o in self._orders if o is not None]
        self._orders = non_null + [None] * (4 - len(non_null))
        self._log_orders_state("_shift")

    def _log_orders_state(self, reason: str = "") -> None:
        """输出完整的订单状态列表"""
        slots = []
        for i, order in enumerate(self._orders):
            if order is None:
                slots.append(f"slot{i}=None")
            else:
                rush_mark = "R" if order.is_rush else ""
                slots.append(f"slot{i}={order.recipe_slug}({rush_mark})")
        logger.info(
            f"[t={self.time:.1f}s] Orders state [{reason}]: [{', '.join(slots)}]"
        )

    # ========================================================================
    # 订单操作
    # ========================================================================

    def add_order(self, recipe_slug: str, is_rush: bool) -> int | None:
        """添加新订单到最左边的空槽位"""
        # 找到最左边的空槽位
        target_slot = None
        for i, order in enumerate(self._orders):
            if order is None:
                target_slot = i
                break

        if target_slot is None:
            return None

        order_id = self._next_order_id
        self._next_order_id += 1

        now = time.time()
        timeout = 40.0 if is_rush else 70.0

        self._orders[target_slot] = Order(
            order_id=order_id,
            recipe_slug=recipe_slug,
            is_rush=is_rush,
            created_at=now,
            timeout_at=now + timeout,
            spawned_at_visibility=self._total_visibility,
        )

        logger.info(
            f"[t={self.time:.1f}s] New order {order_id}: {recipe_slug} ({'RUSH' if is_rush else 'normal'}) slot {target_slot}"
        )
        self._log_orders_state("add")
        return order_id

    def check_and_remove_timed_out_orders(self) -> list[int]:
        """检查并移除超时订单，返回超时订单ID列表"""
        timed_out = []
        now = time.time()
        for i, order in enumerate(self._orders):
            if order is not None and not order.done and now >= order.timeout_at:
                timed_out.append(order.order_id)
                logger.warning(
                    f"[t={self.time:.1f}s] Order {order.order_id} ({order.recipe_slug}) timed out in slot {i}"
                )
                self._orders[i] = None

        if timed_out:
            self._shift_orders_left()
        else:
            self._log_orders_state("timeout_check")

        return timed_out

    def get_order_by_id(self, order_id: int) -> Order | None:
        """根据ID获取订单"""
        for order in self._orders:
            if order and order.order_id == order_id:
                return order
        return None

    def get_order_slot(self, order_id: int) -> int | None:
        """获取订单所在的槽位索引"""
        for i, order in enumerate(self._orders):
            if order and order.order_id == order_id:
                return i
        return None

    # ========================================================================
    # 状态查询
    # ========================================================================

    def get_free_cookers(self) -> list[str]:
        """获取空闲灶台列表"""
        return [name for name, cooker in self._cookers.items() if not cooker.busy]

    def get_done_cookers(self) -> list[str]:
        """获取烹饪完成的灶台列表"""
        return [
            name
            for name, cooker in self._cookers.items()
            if cooker.busy and cooker.done_at and self.time >= cooker.done_at
        ]

    def get_stockpile_count(self, ingredient: str) -> int:
        """获取指定食材的库存数量"""
        for slot in self._stockpile.values():
            if slot.item_name == ingredient:
                return slot.count
        return 0

    def find_stockpile_slot(self, ingredient: str) -> str | None:
        """找到存储指定食材的库存槽位"""
        for slot_name, slot in self._stockpile.items():
            if slot.item_name == ingredient and slot.count > 0:
                return slot_name
        return None

    def find_empty_stockpile_slot(self) -> str | None:
        """找到空的库存槽位"""
        for slot_name, slot in self._stockpile.items():
            if slot.item_name is None or slot.count == 0:
                return slot_name
        return None

    def _infer_recipe_slug_from_ingredient(self, ingredient: str) -> str | None:
        """根据单个食材和活跃订单推断目标配方"""
        for order in self._orders:
            if order and not order.done:
                recipe = self._recipes.get(order.recipe_slug)
                if recipe:
                    raw_ings = recipe.raw_ingredients
                    if ingredient in raw_ings:
                        return order.recipe_slug
        return None

    def get_recipe_for_ingredient_cooker(
        self, ingredient: str, cooker_type: str
    ) -> str | None:
        """
        根据 (ingredient, cooker) 组合确定目标recipe。
        
        逻辑：
        1. 遍历活跃订单
        2. 检查 ingredient+cooker 是否匹配 recipe 的 (raw_ingredients[0], cookers[0])
        3. 返回匹配的recipe_slug，不匹配则返回None
        
        场景分析：
        - gildedShoreRisotto: raw=[clearwater_fish, creamfield_rice], cooks=[oven, pot]
        - braisedNewYearFish: raw=[clearwater_fish], cooks=[skillet]
        - clearwater_fish(oven) → 只能匹配 gildedShoreRisotto
        - clearwater_fish(skillet) → 只能匹配 braisedNewYearFish
        """
        for order in self._orders:
            if order and not order.done:
                recipe = self._recipes.get(order.recipe_slug)
                if recipe:
                    raw_ings = recipe.raw_ingredients
                    cookers = recipe.cookers
                    if raw_ings and cookers:
                        if raw_ings[0] == ingredient and cookers[0] == cooker_type:
                            return order.recipe_slug
        return None

    def _infer_recipe_slug_from_ingredients(self, ingredients: list[str]) -> str | None:
        """根据多个食材和活跃订单推断目标配方"""
        for order in self._orders:
            if order and not order.done:
                recipe = self._recipes.get(order.recipe_slug)
                if recipe:
                    raw_ings = set(recipe.raw_ingredients)
                    if all(ing in raw_ings for ing in ingredients):
                        return order.recipe_slug
        return None
