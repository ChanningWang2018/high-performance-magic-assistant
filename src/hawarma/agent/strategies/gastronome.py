"""
GastronomeStrategy: 美食站点策略（贪心瀑布 + CPM + visibility 跨越 + 单食材 + 延迟感知）

10 级贪心瀑布决策框架，融合了以下历史策略的最佳特性：
  - GreedyCascadeStrategy       10 级瀑布 + 基础辅助方法
  - CPMCascadeStrategy          关键路径计算 + 多灶台优化 + assembly 抢占
  - VisibilityAwareCascadeStrategy visibility 阈值跨越感知
  - CPMEnhancedCascadeStrategy  单食材订单优先（-0.3s CP）
  - DelayAwareCascadeStrategy   智能预烹饪阈值 + 紧急度排序的存储

性能基准（100 局配对，相对 GreedyCascadeStrategy 基线 3736）：
  GastronomeStrategy           ~3934  (+5.3%)

输入: UnifiedState
输出: Action | None
"""

from __future__ import annotations

from loguru import logger

from hawarma.core.actions import (
    Action,
    CookAction,
    MoveToAssemblyAction,
    MoveToStockpileAction,
    PullFromStockpileAction,
    AddCondimentAction,
    ServeOrderAction,
    ClearCookerAction,
    ClearAssemblyAction,
)
from hawarma.core.state import UnifiedState
from hawarma.agent.strategy import Strategy
from hawarma.recipe import Recipe


class GastronomeStrategy(Strategy):
    """美食站点唯一策略：贪心瀑布 + CPM + visibility + 单食材 + 延迟感知"""

    # 操作耗时估算（秒）
    MOVE_TIME = 0.3
    CONDIMENT_TIME = 0.3
    SERVE_TIME = 0.3

    # CPM 抢占阈值：只有当短订单 CP 比 assembly 订单 CP 短这么多时才抢占
    PREEMPT_THRESHOLD = 3.0

    # 库存与时间（delay_aware 调优）
    EXPIRED_THRESHOLD = 5.0
    WARN_THRESHOLD = 3.0
    PRECOOK_STORE_THRESHOLD = 1.0
    MAX_PRECOOK_STOCKPILE = 4
    PRECOOK_STOP_TIME = 15.0
    MIN_PRECOOK_DURATION = 1.0

    # 灶台动作总开销预算（dispatch + Maxtouch swipe + 落地动画 + 安全余量）
    # 把 5s 过期窗口提前收紧，避免决策时"未过期"但落地时已糊掉
    MOVE_SAFETY_MARGIN = 0.5

    # visibility 阈值跨越奖励
    VIS_THRESHOLDS = [40, 80, 160, 240, 360]
    CROSSING_BONUS = 5.0

    # 单食材订单 CP 减少
    SINGLE_INGREDIENT_BONUS = 0.3

    def __init__(self):
        """Initialize cached recipe/ingredient lookups (populated by on_game_start)."""
        self._recipe_by_slug: dict[str, Recipe] = {}
        self._recipe_condiments: dict[str, dict[str, int]] = {}
        self._ingredient_info: dict[str, tuple[str, float]] = {}
        self._recipe_ingredient_cooker: dict[str, list[tuple[str, str, float]]] = {}
        self._reward_lookup = None

    def on_game_start(self, recipes: dict[str, Recipe]) -> None:
        """Cache recipe, ingredient, and condiment data; build reward lookup."""
        self._recipe_by_slug = recipes
        self._recipe_condiments = {}
        self._ingredient_info = {}
        self._recipe_ingredient_cooker = {}

        for slug, recipe in recipes.items():
            self._recipe_condiments[slug] = dict(recipe.condiments)
            ics = []
            for ing in recipe.ingredients:
                self._ingredient_info[ing.name] = (ing.cooker_type, ing.duration)
                ics.append((ing.name, ing.cooker_type, ing.duration))
            self._recipe_ingredient_cooker[slug] = ics

        from hawarma.core.reward import RecipeRewardLookup
        self._reward_lookup = RecipeRewardLookup()

    # ====================================================================
    # 主决策：10 级贪心瀑布
    # ====================================================================

    def decide(self, state: UnifiedState) -> Action | None:
        """10-level greedy cascade: clear → serve → clear_expired → move → cook → condiment → stockpile → precook → store."""
        assembly_ings = [
            ing[0] if isinstance(ing, tuple) else ing
            for ing in state.assembly.ingredients_cookers
        ]

        if action := self._try_clear_assembly(state, assembly_ings):
            return action
        if action := self._try_serve(state, assembly_ings):
            return action
        if action := self._try_clear_expired(state):
            return action
        if action := self._try_move_to_assembly(state, assembly_ings):
            return action
        if action := self._try_parallel_cooking(state, assembly_ings):
            return action
        if action := self._try_add_condiment_urgent(state, assembly_ings):
            return action
        if action := self._try_pull_from_stockpile_urgent(state):
            return action
        if action := self._try_precook(state, assembly_ings):
            return action
        if action := self._try_store_to_stockpile(state):
            return action
        if action := self._try_pull_from_stockpile(state):
            return action

        return None

    # ====================================================================
    # 送餐
    # ====================================================================

    def _try_serve(self, state: UnifiedState, assembly_ings: list[str]) -> ServeOrderAction | None:
        """Serve if assembly matches a recipe ingredients + condiments, respecting target_slug."""
        if state.is_in_animation_window:
            return None
        assembly = state.assembly
        if not assembly.ingredients_cookers:
            return None
        target_slug = assembly.target_recipe_slug
        for slot_idx, order in self._prioritized_orders(state):
            if order is None or order.done:
                continue
            if target_slug and target_slug != order.recipe_slug:
                continue
            recipe = self._recipe_by_slug.get(order.recipe_slug)
            if recipe and self._ingredients_match(assembly.ingredients_cookers, recipe):
                condiments_needed = self._recipe_condiments.get(order.recipe_slug, {})
                if self._condiments_complete(assembly.condiments, condiments_needed):
                    return ServeOrderAction(slot_idx=slot_idx)
        return None

    # ====================================================================
    # 组装站清理（含 CPM 抢占）
    # ====================================================================

    def _try_clear_assembly(self, state: UnifiedState, assembly_ings: list[str]) -> ClearAssemblyAction | None:
        """Clear assembly if ingredients mismatch recipe, target order gone, or CPM preemption triggered."""
        assembly = state.assembly
        if not assembly.ingredients_cookers:
            return None
        target_slug = assembly.target_recipe_slug

        if target_slug:
            has_active = any(
                o and not o.done and o.recipe_slug == target_slug
                for o in state.orders
            )
            if not has_active:
                return ClearAssemblyAction()
            ics = self._recipe_ingredient_cooker.get(target_slug, [])
            recipe_pairs = {(n, c) for n, c, _ in ics}
            assembly_pairs = set()
            for ing in assembly.ingredients_cookers:
                if isinstance(ing, tuple):
                    assembly_pairs.add((ing[0], ing[1] if len(ing) > 1 else None))
                else:
                    assembly_pairs.add((ing, None))
            if not assembly_pairs.issubset(recipe_pairs):
                return ClearAssemblyAction()

            assembly_order = None
            for o in state.orders:
                if o and not o.done and o.recipe_slug == target_slug:
                    assembly_order = o
                    break
            if assembly_order is None:
                return None

            assembly_cp = self._get_critical_path(state, assembly_order)
            for _, order in self._prioritized_orders(state):
                if order.recipe_slug == target_slug:
                    continue
                if not self._order_is_ready(state, order):
                    continue
                order_cp = self._get_critical_path(state, order)
                if assembly_cp - order_cp > self.PREEMPT_THRESHOLD:
                    return ClearAssemblyAction()
            return None

        inferred = self._infer_recipe_from_assembly(state)
        if inferred and any(
            o and not o.done and o.recipe_slug == inferred for o in state.orders
        ):
            return None

        assembly_pairs = set()
        for ing in assembly.ingredients_cookers:
            if isinstance(ing, tuple):
                assembly_pairs.add((ing[0], ing[1] if len(ing) > 1 else None))
            else:
                assembly_pairs.add((ing, None))

        for order in state.orders:
            if not order or order.done:
                continue
            ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
            recipe_pairs = {(n, c) for n, c, _ in ics}
            if assembly_pairs.issubset(recipe_pairs):
                return None

        return ClearAssemblyAction()

    # ====================================================================
    # 清理过期灶台
    # ====================================================================

    def _try_clear_expired(self, state: UnifiedState) -> ClearCookerAction | None:
        """Clear first expired cooker to free the slot."""
        for cooker_name, cooker in state.cookers.items():
            if cooker.busy and cooker.is_expired(state.time):
                return ClearCookerAction(cooker=cooker_name)
        return None

    # ====================================================================
    # 移动完成食材到组装站（过期优先）
    # ====================================================================

    def _try_move_to_assembly(self, state: UnifiedState, assembly_ings: list[str]) -> MoveToAssemblyAction | None:
        """Move done ingredient to assembly, preferring oldest (most urgent)."""
        needed = self._get_needed_ingredients(state)
        all_needed_with_cooker: list[tuple[str, str]] = []
        for order in state.orders:
            if order and not order.done:
                ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
                for ing_name, cooker, _ in ics:
                    all_needed_with_cooker.append((ing_name, cooker))
        effective_needed: set[tuple[str, str]] = set(needed) | set(all_needed_with_cooker)
        if not effective_needed:
            return None

        done_items = []
        for cooker_name, cooker in state.cookers.items():
            if not cooker.busy or cooker.done_at is None:
                continue
            if state.time < cooker.done_at:
                continue
            if not self._is_arrival_safe(state.time, cooker.expired_at):
                logger.debug(
                    f"Skip move-to-assembly {cooker.item_name} on {cooker_name}: "
                    f"remaining={cooker.expired_at - state.time:.2f}s < safety_margin"
                )
                continue
            ing_key = (cooker.item_name, cooker.cooker_type)
            if ing_key not in effective_needed:
                continue
            if not self._can_add_to_assembly(state, cooker.item_name, cooker.cooker_type):
                continue
            time_since_done = state.time - cooker.done_at
            done_items.append((-time_since_done, cooker_name, cooker))

        if done_items:
            done_items.sort()
            _, best_cooker_name, best_cooker = done_items[0]
            order_id = self._get_order_id_for_ingredient_with_cooker(
                state, best_cooker.item_name, best_cooker.cooker_type)
            return MoveToAssemblyAction(cooker=best_cooker_name, order_id=order_id)

        return None

    # ====================================================================
    # 多灶台并行烹饪（CPM 评分）
    # ====================================================================

    def _try_parallel_cooking(self, state: UnifiedState, assembly_ings: list[str]) -> CookAction | None:
        """Cook missing ingredients on free cookers, prioritized by CPM score (-cp + duration)."""
        free_cookers = [name for name, c in state.cookers.items() if not c.busy]
        if not free_cookers:
            return None
        assembly = state.assembly

        if assembly.target_recipe_slug:
            has_active = any(
                o and not o.done and o.recipe_slug == assembly.target_recipe_slug
                for o in state.orders
            )
            if has_active:
                ics = self._recipe_ingredient_cooker.get(assembly.target_recipe_slug, [])
                for ing_name, cooker, duration in ics:
                    if ing_name in assembly_ings:
                        continue
                    if cooker not in free_cookers:
                        continue
                    if self._is_cooking(state, ing_name, cooker):
                        continue
                    if self._has_in_stockpile(state, ing_name, cooker):
                        continue
                    order_id = self._get_order_id_for_ingredient(state, ing_name)
                    return CookAction(ingredient=ing_name, cooker=cooker, duration=duration, order_id=order_id)

        candidates: list[tuple[str, str, float, float]] = []
        seen: set[tuple[str, str]] = set()
        for _, order in self._prioritized_orders(state):
            ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
            for ing_name, cooker, duration in ics:
                combo = (ing_name, cooker)
                if combo in seen:
                    continue
                seen.add(combo)
                if ing_name in assembly_ings:
                    continue
                if cooker not in free_cookers:
                    continue
                if self._is_cooking(state, ing_name, cooker):
                    continue
                if self._has_in_stockpile(state, ing_name, cooker):
                    continue
                cp = self._get_critical_path(state, order)
                score = -cp + duration
                candidates.append((ing_name, cooker, duration, score))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[3], reverse=True)
        ing_name, cooker, duration, _ = candidates[0]
        order_id = self._get_order_id_for_ingredient(state, ing_name)
        return CookAction(ingredient=ing_name, cooker=cooker, duration=duration, order_id=order_id)

    # ====================================================================
    # 调料添加
    # ====================================================================

    def _try_add_condiment_urgent(self, state: UnifiedState, assembly_ings: list[str]) -> AddCondimentAction | None:
        """Add missing condiment when assembly ingredients match a target recipe."""
        assembly = state.assembly
        if not assembly.ingredients_cookers:
            return None
        target_slug = assembly.target_recipe_slug
        if not target_slug:
            target_slug = self._infer_recipe_from_assembly(state)
            if not target_slug:
                return None
        recipe = self._recipe_by_slug.get(target_slug)
        if not recipe:
            return None
        if not self._ingredients_match(assembly.ingredients_cookers, recipe):
            return None
        condiments_needed = self._recipe_condiments.get(target_slug, {})
        if not condiments_needed:
            return None
        for condiment, required in condiments_needed.items():
            current = assembly.condiments.get(condiment, 0)
            if current < required:
                return AddCondimentAction(condiment=condiment)
        return None

    # ====================================================================
    # 库存取用
    # ====================================================================

    def _try_pull_from_stockpile_urgent(self, state: UnifiedState) -> PullFromStockpileAction | None:
        """Pull needed ingredient from stockpile when assembly already has a target slug."""
        assembly = state.assembly
        if not assembly.target_recipe_slug:
            if assembly.ingredients_cookers:
                return None
            return self._try_pull_from_stockpile(state)
        has_active = any(
            o and not o.done and o.recipe_slug == assembly.target_recipe_slug
            for o in state.orders
        )
        if not has_active:
            return None
        needed = self._get_needed_item_names(state)
        if not needed:
            return None
        for slot_name, slot in state.stockpile.items():
            if slot.item_name in needed and slot.count > 0:
                if self._can_add_to_assembly(state, slot.item_name, slot.cooker_type):
                    return PullFromStockpileAction(slot=slot_name, ingredient=slot.item_name)
        return None

    def _try_pull_from_stockpile(self, state: UnifiedState) -> PullFromStockpileAction | None:
        """Pull from stockpile when needed ingredient exists and can be added to assembly."""
        assembly = state.assembly
        if assembly.target_recipe_slug:
            has_active = any(
                o and not o.done and o.recipe_slug == assembly.target_recipe_slug
                for o in state.orders
            )
            if not has_active:
                return None
        needed_ings = self._get_needed_item_names(state)
        if not needed_ings:
            return None
        for slot_name, slot in state.stockpile.items():
            if slot.item_name in needed_ings and slot.count > 0:
                if self._can_add_to_assembly(state, slot.item_name, slot.cooker_type):
                    return PullFromStockpileAction(slot=slot_name, ingredient=slot.item_name)
        return None

    # ====================================================================
    # 智能预烹饪（delay_aware 调优：MIN_PRECOOK_DURATION, STOP_TIME, sort by duration desc）
    # ====================================================================

    def _try_precook(self, state: UnifiedState, assembly_ings: list[str]) -> CookAction | None:
        """Pre-cook ingredients for active (then fallback) orders when cookers are free and stockpile not full."""
        remaining_time = state.game_duration - state.time
        if remaining_time < self.PRECOOK_STOP_TIME:
            return None

        free_cookers = [name for name, c in state.cookers.items() if not c.busy]
        if not free_cookers:
            return None

        total_in_stockpile = sum(slot.count for slot in state.stockpile.values())
        if total_in_stockpile >= self.MAX_PRECOOK_STOCKPILE:
            return None

        cooking_combos: set[tuple[str, str]] = set()
        for cooker in state.cookers.values():
            if cooker.busy:
                cooking_combos.add((cooker.item_name, cooker.cooker_type))

        active_order_slugs = {o.recipe_slug for o in state.orders if o and not o.done}

        def _try_precook_for_slugs(slugs: set[str]) -> CookAction | None:
            candidates: list[tuple[str, str, float, float]] = []
            for slug in slugs:
                ics = self._recipe_ingredient_cooker.get(slug, [])
                for ing_name, cooker, duration in ics:
                    combo = (ing_name, cooker)
                    if combo in cooking_combos:
                        continue
                    if ing_name in assembly_ings:
                        continue
                    if self._has_in_stockpile(state, ing_name, cooker):
                        continue
                    if cooker not in free_cookers:
                        continue
                    if duration < self.MIN_PRECOOK_DURATION:
                        continue
                    score = duration
                    candidates.append((ing_name, cooker, duration, score))
            if candidates:
                candidates.sort(key=lambda x: x[3], reverse=True)
                ing_name, cooker, duration, _ = candidates[0]
                order_id = self._get_order_id_for_ingredient(state, ing_name)
                return CookAction(ingredient=ing_name, cooker=cooker, duration=duration, order_id=order_id)
            return None

        result = _try_precook_for_slugs(active_order_slugs)
        if result:
            return result

        all_slugs = set(self._recipe_ingredient_cooker.keys())
        return _try_precook_for_slugs(all_slugs - active_order_slugs)

    # ====================================================================
    # 紧急度排序的存储（delay_aware 调优：needed > near-expired > normal）
    # ====================================================================

    def _try_store_to_stockpile(self, state: UnifiedState) -> MoveToStockpileAction | None:
        """Store done ingredients to stockpile, prioritized by needed > near-expired > normal."""
        assembly = state.assembly
        needed = self._get_needed_item_names(state)

        candidates: list[tuple[str, str, str, float]] = []
        for cooker_name, cooker in state.cookers.items():
            if not cooker.busy or cooker.done_at is None:
                continue
            if state.time < cooker.done_at:
                continue
            ing_name = cooker.item_name
            cooker_type = cooker.cooker_type
            time_since_done = state.time - cooker.done_at

            if not self._is_arrival_safe(state.time, cooker.expired_at):
                logger.debug(
                    f"Skip store-to-stockpile {ing_name} on {cooker_name}: "
                    f"remaining={cooker.expired_at - state.time:.2f}s < safety_margin"
                )
                continue

            if ing_name in needed:
                priority = 100 + time_since_done
            elif time_since_done > self.EXPIRED_THRESHOLD - 1.0:
                priority = 50 + time_since_done
            else:
                priority = time_since_done

            candidates.append((cooker_name, ing_name, cooker_type, priority))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[3], reverse=True)
        cooker_name, ing_name, cooker_type, _ = candidates[0]

        slot = self._try_increment_stockpile(state, ing_name, cooker_type)
        if slot is None:
            slot = self._find_available_slot(state, ing_name, cooker_type)
        if slot:
            return MoveToStockpileAction(cooker=cooker_name, slot=slot)

        return None

    # ====================================================================
    # 订单优先级（CP + visibility 跨越 + 单食材 + Rush tiebreaker）
    # ====================================================================

    def _prioritized_orders(self, state: UnifiedState):
        """Yield (slot_idx, order) sorted by CP asc, then rush status; includes visibility crossing & single-ingredient bonus."""
        active = [(i, o) for i, o in enumerate(state.orders) if o and not o.done]
        scored = []
        for slot_idx, order in active:
            cp = self._get_critical_path(state, order)
            if self._will_cross_threshold(state, order):
                cp -= self.CROSSING_BONUS
            ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
            if len(ics) == 1:
                cp -= self.SINGLE_INGREDIENT_BONUS
            rush_priority = 0 if order.is_rush else 1
            scored.append((cp, rush_priority, slot_idx, order))
        scored.sort(key=lambda x: (x[0], x[1]))
        for _, _, slot_idx, order in scored:
            yield slot_idx, order

    # ====================================================================
    # 关键路径法（CPM）
    # ====================================================================

    def _get_critical_path(self, state: UnifiedState, order) -> float:
        """Estimate remaining time to complete order: max ingredient wait + condiments + serve."""
        ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
        if not ics:
            return float('inf')

        assembly_ing_names = set()
        for ing in state.assembly.ingredients_cookers:
            if isinstance(ing, tuple):
                assembly_ing_names.add(ing[0])
            else:
                assembly_ing_names.add(ing)

        stockpile_ings: dict[str, int] = {}
        for slot in state.stockpile.values():
            if slot.count > 0 and slot.item_name:
                stockpile_ings[slot.item_name] = stockpile_ings.get(slot.item_name, 0) + slot.count

        cooking: dict[str, float] = {}
        for c in state.cookers.values():
            if c.busy and c.item_name:
                cooking[c.item_name] = min(cooking.get(c.item_name, float('inf')), c.done_at or 0)

        max_ing_time = 0.0
        for ing_name, cooker_type, duration in ics:
            if ing_name in assembly_ing_names:
                continue

            t_get = 0.0
            if stockpile_ings.get(ing_name, 0) > 0:
                t_get = self.MOVE_TIME
            elif ing_name in cooking:
                done_at = cooking[ing_name]
                if state.time < done_at:
                    t_get = (done_at - state.time) + self.MOVE_TIME
                else:
                    t_get = self.MOVE_TIME
            else:
                cooker = state.cookers.get(cooker_type)
                if cooker is None or not cooker.busy:
                    t_get = duration + self.MOVE_TIME
                else:
                    wait = max(0, (cooker.done_at or state.time) - state.time)
                    t_get = wait + duration + self.MOVE_TIME

            max_ing_time = max(max_ing_time, t_get)

        condiments = self._recipe_condiments.get(order.recipe_slug, {})
        condiment_count = sum(condiments.values()) if condiments else 0
        condiment_time = condiment_count * self.CONDIMENT_TIME

        return max_ing_time + condiment_time + self.SERVE_TIME

    def _order_is_ready(self, state: UnifiedState, order) -> bool:
        """Check if all ingredients for an order are done (in assembly, stockpile, or completed cooker)."""
        ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
        assembly_ing_names = set()
        for ing in state.assembly.ingredients_cookers:
            if isinstance(ing, tuple):
                assembly_ing_names.add(ing[0])
            else:
                assembly_ing_names.add(ing)

        stockpile_ings: dict[str, int] = {}
        for slot in state.stockpile.values():
            if slot.count > 0 and slot.item_name:
                stockpile_ings[slot.item_name] = stockpile_ings.get(slot.item_name, 0) + slot.count

        cooking_done: dict[str, bool] = {}
        for c in state.cookers.values():
            if c.busy and c.item_name and c.done_at and state.time >= c.done_at:
                cooking_done[c.item_name] = True

        for ing_name, cooker_type, duration in ics:
            if ing_name in assembly_ing_names:
                continue
            if stockpile_ings.get(ing_name, 0) > 0:
                continue
            if cooking_done.get(ing_name, False):
                continue
            return False
        return True

    # ====================================================================
    # visibility 阈值跨越感知
    # ====================================================================

    def _get_order_visibility(self, order) -> int:
        """Return order's visibility reward based on recipe and condiment status."""
        if self._reward_lookup is None:
            return 30
        return self._reward_lookup.get_visibility(order.recipe_slug, has_condiments=True)

    def _next_threshold(self, current_vis: float) -> float | None:
        """Return next visibility threshold above current, or None if all crossed."""
        for t in self.VIS_THRESHOLDS:
            if current_vis < t:
                return t
        return None

    def _will_cross_threshold(self, state: UnifiedState, order) -> bool:
        """Check if completing this order will cross a visibility milestone."""
        current_vis = state.total_visibility
        order_vis = self._get_order_visibility(order)
        next_threshold = self._next_threshold(current_vis)
        if next_threshold is None:
            return False
        return current_vis + order_vis >= next_threshold

    # ====================================================================
    # 食材→订单映射
    # ====================================================================

    def _get_order_id_for_ingredient(self, state: UnifiedState, ingredient: str) -> int | None:
        """Find the best order_id for a given ingredient (lowest CP, considering bonuses)."""
        best_order = None
        best_cp = float('inf')
        for _, order in self._prioritized_orders(state):
            recipe = self._recipe_by_slug.get(order.recipe_slug)
            if recipe:
                if ingredient in recipe.raw_ingredients:
                    cp = self._get_critical_path(state, order)
                    if self._will_cross_threshold(state, order):
                        cp -= self.CROSSING_BONUS
                    ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
                    if len(ics) == 1:
                        cp -= self.SINGLE_INGREDIENT_BONUS
                    if cp < best_cp:
                        best_cp = cp
                        best_order = order
        return best_order.order_id if best_order else None

    def _get_order_id_for_ingredient_with_cooker(self, state: UnifiedState, ingredient: str, cooker_type: str) -> int | None:
        """Find order_id where ingredient + cooker_type matches (first match in priority order)."""
        for _, order in self._prioritized_orders(state):
            ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
            for ing_name, ck, _ in ics:
                if ing_name == ingredient and ck == cooker_type:
                    return order.order_id
        return None

    # ====================================================================
    # 辅助方法
    # ====================================================================

    def _get_needed_ingredients(self, state: UnifiedState) -> list[tuple[str, str]]:
        """Return list of (ingredient, cooker_type) still needed for the current target or best-guess order."""
        assembly = state.assembly
        target_slug = assembly.target_recipe_slug
        present = set(assembly.ingredients_cookers)

        if target_slug:
            ics = self._recipe_ingredient_cooker.get(target_slug, [])
            present_pairs = set()
            for ing in present:
                if isinstance(ing, tuple):
                    present_pairs.add((ing[0], ing[1] if len(ing) > 1 else None))
                else:
                    present_pairs.add((ing, None))
            result = []
            for ing_name, cooker, _ in ics:
                if (ing_name, cooker) not in present_pairs:
                    result.append((ing_name, cooker))
            return result

        present_ing_names = set()
        for ing in present:
            name = ing[0] if isinstance(ing, tuple) else ing
            present_ing_names.add(name)

        if not present_ing_names:
            for _, order in self._prioritized_orders(state):
                ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
                return [(n, c) for n, c, _ in ics]
            return []

        for _, order in self._prioritized_orders(state):
            recipe = self._recipe_by_slug.get(order.recipe_slug)
            if not recipe:
                continue
            raw_ings = set(recipe.raw_ingredients)
            if present_ing_names.issubset(raw_ings):
                ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
                return [(n, c) for n, c, _ in ics if n not in present_ing_names]

        return []

    def _get_needed_item_names(self, state: UnifiedState) -> set[str]:
        """Return set of ingredient names still needed (cooker-agnostic)."""
        return {ing for ing, _ in self._get_needed_ingredients(state)}

    def _can_add_to_assembly(self, state: UnifiedState, ingredient: str, cooker_type: str | None = None) -> bool:
        """Check if ingredient+cooker_type is compatible with current assembly (respects target_slug or inferred recipe)."""
        assembly = state.assembly
        present_with_cooker = assembly.ingredients_cookers
        target_slug = assembly.target_recipe_slug

        if not present_with_cooker and not target_slug:
            return True

        if not target_slug:
            present_ing_combos = {(t[0], t[1]) if isinstance(t, tuple) else (t, None) for t in present_with_cooker}
            compatible_slugs = []
            for order in state.orders:
                if not order or order.done:
                    continue
                recipe = self._recipe_by_slug.get(order.recipe_slug)
                if not recipe:
                    continue
                all_match = True
                for ing_name, ck_type in present_ing_combos:
                    ing_matched = False
                    for r_ing in recipe.ingredients:
                        if r_ing.name == ing_name and r_ing.cooker_type == ck_type:
                            ing_matched = True
                            break
                    if not ing_matched:
                        all_match = False
                        break
                if all_match:
                    compatible_slugs.append(order.recipe_slug)

            if not compatible_slugs:
                return False

            new_key = (ingredient, cooker_type)
            if new_key in present_ing_combos:
                return False

            for slug in compatible_slugs:
                ics = self._recipe_ingredient_cooker.get(slug, [])
                for ing_name, ck, _ in ics:
                    if ing_name == ingredient and ck == cooker_type:
                        return True
            return False

        ics = self._recipe_ingredient_cooker.get(target_slug, [])
        found = False
        for ing_name, ck, _ in ics:
            if ing_name == ingredient:
                if ck != cooker_type:
                    return False
                found = True
        if not found:
            return False

        present_combinations = {
            (ing[0], ing[1]) if isinstance(ing, tuple) else (ing, None)
            for ing in present_with_cooker
        }
        return (ingredient, cooker_type) not in present_combinations

    def _is_cooking(self, state: UnifiedState, ingredient: str, cooker_type: str | None = None) -> bool:
        """Check if ingredient is currently being cooked (optionally filtered by cooker_type)."""
        for cooker in state.cookers.values():
            if cooker.busy and cooker.item_name == ingredient:
                if cooker_type is None or cooker.cooker_type == cooker_type:
                    return True
        return False

    def _has_in_stockpile(self, state: UnifiedState, ingredient: str, cooker_type: str | None = None) -> bool:
        """Check if ingredient is available in stockpile (optionally filtered by cooker_type)."""
        for slot in state.stockpile.values():
            if slot.item_name == ingredient and slot.count > 0:
                if cooker_type is None or slot.cooker_type == cooker_type:
                    return True
        return False

    def _find_available_slot(self, state: UnifiedState, ingredient: str, cooker_type: str) -> str | None:
        """Find a stockpile slot that can accept the ingredient (empty or matching, under capacity)."""
        for slot_name, slot in state.stockpile.items():
            if slot.item_name is None or (slot.item_name == ingredient and slot.cooker_type == cooker_type):
                if slot.count < 5:
                    return slot_name
        return None

    def _try_increment_stockpile(self, state: UnifiedState, ingredient: str, cooker_type: str) -> str | None:
        """Find an existing slot with the same ingredient+cooker_type that still has room."""
        for slot_name, slot in state.stockpile.items():
            if slot.item_name == ingredient and slot.cooker_type == cooker_type:
                if slot.count < 5:
                    return slot_name
        return None

    def _infer_recipe_from_assembly(self, state: UnifiedState) -> str | None:
        """Guess which recipe the current assembly belongs to, based on matching ingredient pairs."""
        assembly = state.assembly
        if not assembly.ingredients_cookers:
            return None
        assembly_pairs = set()
        for ing in assembly.ingredients_cookers:
            if isinstance(ing, tuple):
                assembly_pairs.add((ing[0], ing[1] if len(ing) > 1 else None))
            else:
                assembly_pairs.add((ing, None))

        for _, order in self._prioritized_orders(state):
            ics = self._recipe_ingredient_cooker.get(order.recipe_slug, [])
            recipe_pairs = {(n, c) for n, c, _ in ics}
            if assembly_pairs == recipe_pairs:
                return order.recipe_slug
        return None

    def _ingredients_match(self, actual: list, recipe) -> bool:
        """Check if assembly ingredients (name, cooker_type) match exactly with recipe ingredients."""
        if len(actual) != len(recipe.ingredients):
            return False
        expected_pairs = set()
        for ing in recipe.ingredients:
            expected_pairs.add((ing.name, ing.cooker_type))
        actual_pairs = set()
        for ing in actual:
            if isinstance(ing, tuple):
                actual_pairs.add((ing[0], ing[1] if len(ing) > 1 else None))
            else:
                actual_pairs.add((ing, None))
        return actual_pairs == expected_pairs

    def _condiments_complete(self, applied: dict[str, int], needed: dict[str, int]) -> bool:
        """Check if all required condiments have been applied in sufficient quantity."""
        for condiment, count in needed.items():
            if applied.get(condiment, 0) < count:
                return False
        return True

    def _is_arrival_safe(self, state_time: float, expired_at: float | None) -> bool:
        """预测 move/stockpile 能否在游戏内过期前落地

        游戏的 5s 窗口在食材"落地"瞬间判定，而模型只能在决策瞬间检查。
        两者之间存在 dispatch + Maxtouch swipe + 落地动画的尾段延迟，
        用 MOVE_SAFETY_MARGIN 把判定窗口提前收紧。
        """
        if expired_at is None:
            return False
        return state_time + self.MOVE_SAFETY_MARGIN < expired_at
