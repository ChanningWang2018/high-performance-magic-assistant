"""
游戏环境模拟器 - 核心实现

地位：轻量级、确定性的状态机，模拟烹饪游戏
      作为游戏规则的"真理之源"

输入：游戏动作（烹饪、移动、调味等）、时间推进
输出：事件列表、状态更新、操作结果

真实游戏规则（2026-04-24 更新）：
- 订单刷新间隔：随机 3-5 秒，平均 4 秒（非固定间隔）
- 订单超时：与 recipe 食材耗时相关，普通订单 55-75 秒，rush 订单 30-45 秒
- 游戏时长：90-110 秒（根据玩家等级/进度，可配置）
- 动画窗口限制：因 UI 变化导致扫描不稳定，非游戏本身限制
- Visibility 加成：已完成订单的总 visibility 决定后续订单的得分倍率（详见 docs/game_rules.md）

⚠️ 一旦文件内容有更新，务必对开头注释进行相应的必要更新
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .env_simulator_types import (
    Event,
    EventType,
    Order,
    CookerState,
    AssemblyState,
    StockpileSlot,
    GameState,
    Recipe,
    IngredientRequirement,
    GameConfig,
)
from hawarma.core.reward import RecipeRewardLookup, RecipeTimeoutLookup
from hawarma.core.scoring import finalize_score


# ============================================================================
# ActionResult 和异常
# ============================================================================

@dataclass(frozen=True)
class ActionResult:
    """
    动作执行结果
    
    使用 frozen=True 确保结果不可变，避免意外修改
    """
    success: bool
    events: tuple[Event, ...] = field(default_factory=tuple)
    error_message: str | None = None
    score_earned: float = 0.0  # 如果是 serve_order，记录得分
    
    def __bool__(self) -> bool:
        """允许使用 `if result:` 语法"""
        return self.success
    
    @property
    def is_success(self) -> bool:
        """显式检查是否成功"""
        return self.success
    
    def get_error(self) -> str:
        """获取错误信息，如果没有则返回默认值"""
        return self.error_message or "Unknown error"
    
    @classmethod
    def success_result(cls, events: list[Event] = None, score: float = 0.0) -> ActionResult:
        """快速创建成功结果"""
        return cls(
            success=True,
            events=tuple(events) if events else (),
            score_earned=score
        )
    
    @classmethod
    def failure_result(cls, error_message: str, events: list[Event] = None) -> ActionResult:
        """快速创建失败结果"""
        return cls(
            success=False,
            events=tuple(events) if events else (),
            error_message=error_message
        )


# ============================================================================
# 游戏模拟器主类
# ============================================================================

class GameSimulator:
    """
    游戏环境模拟器
    
    轻量级、确定性的状态机，作为游戏规则的"真理之源"。
    
    核心特性：
    - 不可变状态：每个动作返回新的状态副本，便于调试和重放
    - 完整验证：所有操作都严格验证游戏规则
    - 事件驱动：所有状态变化都通过事件记录
    - 时间推进：通过 tick() 方法推进时间，触发自动事件
    
    使用示例：
        sim = GameSimulator()
        sim.load_recipes("data/recipes.json")
        sim.setup_cookers(['grill', 'oven', 'skillet', 'pot'])
        sim.setup_stockpile(['slot0', 'slot1', 'slot2'])
        
        # 注入订单
        sim.inject_order(0, recipe, is_rush=False)
        
        # 执行动作
        result = sim.start_cooking('beef', 'grill')
        if result.success:
            print(f"Cooking started! Events: {result.events}")
        
        # 推进时间
        events = sim.tick(5.0)  # 推进5秒
        for event in events:
            print(f"Event: {event}")
    """
    
    # 类常量
    MAX_SLOTS = 4
    MAX_STOCKPILE = 5
    COOKER_RETENTION = 5.0  # 灶台食材保留时间（秒）
    ANIMATION_DURATION = 1.5  # slot 位移动画时间（秒）
    ORDER_INTERVAL_MIN = 3.0   # 订单生成间隔最小值（秒）
    ORDER_INTERVAL_MAX = 5.0   # 订单生成间隔最大值（秒）
    RUSH_TIMEOUT_MIN = 30.0    # Rush 订单超时最小值（秒）
    RUSH_TIMEOUT_MAX = 45.0    # Rush 订单超时最大值（秒）
    NORMAL_TIMEOUT_MIN = 55.0  # 普通订单超时最小值（秒）
    NORMAL_TIMEOUT_MAX = 75.0  # 普通订单超时最大值（秒）
    MAX_CONDIMENTS = 3          # 每道菜最多调料数
    GAME_DURATION_MIN = 90.0   # 游戏最短时长（秒）
    GAME_DURATION_MAX = 110.0  # 游戏最长时长（秒）
    DEFAULT_GAME_DURATION = 90.0  # 默认游戏时长
    
    def __init__(self, game_duration: float | None = None):
        """初始化模拟器
        
        Args:
            game_duration: 游戏时长（秒），范围 90-110。None 表示使用默认值
        """
        # 当前游戏状态（使用深拷贝确保不可变性）
        self._state = GameState()
        
        # 配方数据
        self._recipes: dict[str, Recipe] = {}
        
        # 事件历史（完整记录，用于调试和重放）
        self._event_history: list[Event] = []
        
        # 动画窗口结束时间
        self._animation_until: float = 0.0
        
        # 待处理订单（用于延迟生成）
        self._pending_orders: list[tuple[Order, float]] = []  # (order, appear_at)
        
        # 订单ID计数器
        self._next_order_id: int = 1
        
        # 调试模式
        self._debug: bool = False
        
        # 标记是否需要立即刷新订单（订单完成后所有槽位为空）
        self._needs_immediate_refresh: bool = False
        
        # 上次订单生成时间
        self._last_order_time: float = 0.0
        
        # 游戏时长（可配置）
        self._game_duration: float = game_duration or self.DEFAULT_GAME_DURATION
        if self._game_duration < self.GAME_DURATION_MIN or self._game_duration > self.GAME_DURATION_MAX:
            raise ValueError(f"game_duration must be between {self.GAME_DURATION_MIN} and {self.GAME_DURATION_MAX}")
        
        # 下一次订单刷新时间（随机间隔）
        # 初始为随机 3-5 秒（游戏开始后第一个订单）
        self._next_order_refresh_time: float = self._random_order_interval()
        
        # 游戏配置（选菜单后的配置）
        self._game_config: GameConfig = GameConfig()
        
        # 订单超时查表
        self._timeout_lookup: RecipeTimeoutLookup | None = None
        
        # 订单得分查表
        self._reward_lookup: RecipeRewardLookup | None = None
        
        # 模拟器专有数据（不属于共享 Order 模型）
        self._order_recipes: dict[int, Recipe] = {}
        self._order_condiments: dict[int, dict[str, int]] = {}
        self._order_visibility: dict[int, float] = {}
        """快照旁路字典（兼容保留）：与 Order.spawned_at_visibility 同值，
        计分以 Order 域字段为准，字典仅作回退."""

        # 终局计分（S1）：逐单得分和 + 总 visibility，幂等结算。
        # visibility 真源是 _state.total_visibility（向 agent 暴露的状态）；
        # 终局算术与真实端共用 hawarma.core.scoring.finalize_score。
        self._total_score: float = 0.0
        self._orders_served: int = 0
        self._finalized: bool = False
        self._final_score: float = 0.0
    
    def _get_recipe(self, order: Order) -> Recipe:
        """获取订单对应的配方对象（模拟器内部数据）"""
        return self._order_recipes[order.order_id]

    def _snapshot_visibility(self, order: Order) -> float:
        """订单生成时可见度快照.

        新订单创建时域字段与旁路字典同值双写；老订单（域字段缺省 0.0）
        回退读字典，保证两端同语义.
        """
        return self._order_visibility.get(order.order_id, order.spawned_at_visibility)

    def finalize(self) -> float:
        """终局结算（幂等）：逐单得分和 + 总 visibility."""
        if not self._finalized:
            self._final_score = finalize_score(
                self._total_score, self._state.total_visibility
            )
            self._finalized = True
        return self._final_score

    def get_final_score(self) -> float:
        """终局分数（finalize 缓存或实时计算，查询无副作用叠加）."""
        if self._finalized:
            return self._final_score
        return finalize_score(self._total_score, self._state.total_visibility)
    
    # ------------------------------------------------------------------
    # 配置和初始化
    # ------------------------------------------------------------------
    
    @property
    def game_config(self) -> GameConfig:
        """获取当前游戏配置"""
        return self._game_config
    
    def select_recipes(self, count: int = 4, random_seed: int | None = None) -> list[str]:
        """
        从所有菜谱中随机选择指定数量的菜谱
        
        Args:
            count: 选择的菜谱数量（默认4，最多4）
            random_seed: 随机种子（用于可复现的测试）
        
        Returns:
            选中的菜谱slug列表
        """
        if not self._recipes:
            raise ValueError("No recipes loaded. Call load_recipes() first.")
        
        # 最多选择4个菜谱
        count = min(count, 4)
        
        # 获取所有菜谱的slug
        all_slugs = list(self._recipes.keys())
        
        # 设置随机种子（如果提供）
        if random_seed is not None:
            random.seed(random_seed)
        
        # 随机选择指定数量的菜谱
        selected = random.sample(all_slugs, min(count, len(all_slugs)))
        
        # 恢复随机状态（如果没有提供种子）
        if random_seed is None:
            random.seed()
        
        if self._debug:
            print(f"Selected {len(selected)} recipes: {selected}")
        
        return selected
    
    def setup_from_recipes(self, recipe_slugs: list[str]) -> GameConfig:
        """
        根据选中的菜谱自动配置游戏
        
        自动设置：
        - 灶台（从菜谱的cookers字段收集）
        - 食材区（从菜谱的raw_ingredients字段收集）
        - 调料区（从菜谱的condiments字段收集）
        
        Args:
            recipe_slugs: 选中的菜谱slug列表
            
        Returns:
            GameConfig对象
        """
        if not recipe_slugs:
            raise ValueError("Must select at least one recipe")
        
        if len(recipe_slugs) > 4:
            raise ValueError("Cannot select more than 4 recipes")
        
        # 收集所有需要的资源
        cookers_set = set()
        ingredients_set = set()
        condiments_set = set()
        
        for slug in recipe_slugs:
            if slug not in self._recipes:
                raise ValueError(f"Unknown recipe: {slug}")
            
            recipe = self._recipes[slug]
            
            # 收集灶台
            for ing in recipe.ingredients:
                cookers_set.add(ing.cooker_type)
            
            # 收集食材
            for ing in recipe.ingredients:
                ingredients_set.add(ing.name)
            
            # 收集调料
            for condiment in recipe.condiments.keys():
                condiments_set.add(condiment)
        
        # 创建游戏配置
        self._game_config = GameConfig(
            selected_recipes=recipe_slugs,
            available_cookers=list(cookers_set),
            available_ingredients=list(ingredients_set),
            available_condiments=list(condiments_set)
        )
        
        # 自动设置灶台
        self.setup_cookers(self._game_config.available_cookers)
        
        # 库存区保持为空（由Agent自己决定存什么）
        self.setup_stockpile(['slot0', 'slot1', 'slot2'])
        
        if self._debug:
            print(f"Game configured with {len(recipe_slugs)} recipes")
            print(f"  Cookers: {self._game_config.available_cookers}")
            print(f"  Ingredients: {self._game_config.available_ingredients}")
            print(f"  Condiments: {self._game_config.available_condiments}")
        
        return self._game_config
    
    def _random_order_interval(self) -> float:
        """生成随机订单间隔（3-5秒）"""
        return random.uniform(self.ORDER_INTERVAL_MIN, self.ORDER_INTERVAL_MAX)
    
    def _calculate_timeout(self, recipe: Recipe, is_rush: bool) -> float:
        """根据 recipe 计算订单超时时间
        
        从 recipe_timeout.csv 查表获取真实超时时间。
        如果找不到数据，则使用原来的公式计算作为后备。
        
        Args:
            recipe: 订单的配方
            is_rush: 是否为 rush 订单
            
        Returns:
            超时时间（秒）
        """
        if self._timeout_lookup is None:
            self._timeout_lookup = RecipeTimeoutLookup()
        
        timeout = self._timeout_lookup.get_timeout(recipe.slug, is_rush)
        
        if timeout is not None:
            return timeout
        
        total_cook_time = sum(ing.duration for ing in recipe.ingredients)
        
        if is_rush:
            base = self.RUSH_TIMEOUT_MIN
            max_timeout = self.RUSH_TIMEOUT_MAX
        else:
            base = self.NORMAL_TIMEOUT_MIN
            max_timeout = self.NORMAL_TIMEOUT_MAX
        
        cook_factor = max(0.0, min(1.0, (total_cook_time - 2.0) / 4.0))
        timeout = base + (max_timeout - base) * (1.0 - cook_factor * 0.3)
        
        return round(timeout, 1)
    
    def load_recipes(self, filepath: str | Path) -> None:
        """
        从 JSON 文件加载配方数据
        
        直接使用统一的 hawarma.recipe.Recipe (Pydantic 模型)。
        JSON 格式与真实环境相同（raw_ingredients, cookers, cook_durations, condiments）。
        
        Args:
            filepath: 配方文件路径
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Recipe file not found: {filepath}")
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self._recipes = {}
        recipes_list = data.get('recipes', data) if isinstance(data, dict) else data
        for recipe_data in recipes_list:
            recipe = Recipe(**recipe_data)
            self._recipes[recipe.slug] = recipe
        
        if self._debug:
            print(f"Loaded {len(self._recipes)} recipes")
    
    def setup_cookers(self, names: list[str]) -> None:
        """
        初始化灶台
        
        Args:
            names: 灶台名称列表（如 ['grill', 'oven', 'skillet', 'pot']）
        """
        self._state.cookers = {name: CookerState() for name in names}
        
        if self._debug:
            print(f"Setup {len(names)} cookers: {names}")
    
    def setup_stockpile(self, slots: list[str]) -> None:
        """
        初始化库存区
        
        Args:
            slots: 库存槽位名称列表（如 ['slot0', 'slot1', 'slot2']）
        """
        self._state.stockpile = {slot_name: StockpileSlot() for slot_name in slots}
        
        if self._debug:
            print(f"Setup {len(slots)} stockpile slots: {slots}")
    
    def enable_debug(self, enabled: bool = True) -> None:
        """启用或禁用调试模式"""
        self._debug = enabled
    
    # ------------------------------------------------------------------
    # 属性和查询
    # ------------------------------------------------------------------
    
    @property
    def state(self) -> GameState:
        """获取当前游戏状态（只读）"""
        return copy.deepcopy(self._state)
    
    @property
    def time(self) -> float:
        """获取当前模拟时间"""
        return self._state.time
    
    @property
    def recipes(self) -> dict[str, Recipe]:
        """获取所有配方"""
        return dict(self._recipes)  # 返回副本
    
    @property
    def events(self) -> list[Event]:
        """获取当前步骤的事件列表"""
        return list(self._event_history)
    
    def is_in_animation_window(self) -> bool:
        """检查是否在动画窗口期"""
        return self._state.time < self._animation_until
    
    def is_game_over(self) -> bool:
        """检查游戏是否已结束"""
        return self._state.time >= self._game_duration
    
    def get_order(self, slot_idx: int) -> Order | None:
        """
        获取指定槽位的订单
        
        注意：如果订单还在动画期间（1秒），返回None
        动画期间订单已存在但agent还无法"看到"
        """
        if 0 <= slot_idx < self.MAX_SLOTS:
            order = self._state.orders[slot_idx]
            # 检查订单是否在动画期间（创建后1秒内）
            if order is not None and self._state.time < order.created_at + 1.0:
                return None
            return order
        return None
    
    def get_cooker_state(self, cooker_name: str) -> CookerState | None:
        """获取指定灶台的状态"""
        return self._state.cookers.get(cooker_name)
    
    def get_stockpile_slot(self, slot_name: str) -> StockpileSlot | None:
        """获取指定库存槽位的状态"""
        return self._state.stockpile.get(slot_name)
    
    # ------------------------------------------------------------------
    # 订单管理
    # ------------------------------------------------------------------
    
    def inject_order(
        self,
        slot_idx: int,
        recipe: Recipe,
        is_rush: bool = False,
        condiments: dict[str, int] | None = None
    ) -> ActionResult:
        """
        将订单注入指定槽位
        
        Args:
            slot_idx: 槽位索引 (0-3)
            recipe: 配方
            is_rush: 是否为紧急订单
            condiments: 自定义调料需求（可选）
            
        Returns:
            ActionResult: 操作结果
        """
        # 验证槽位索引
        if slot_idx < 0 or slot_idx >= self.MAX_SLOTS:
            return ActionResult.failure_result(
                f"Invalid slot index: {slot_idx}. Must be 0-{self.MAX_SLOTS-1}"
            )
        
        # 检查槽位是否被占用
        if self._state.orders[slot_idx] is not None:
            return ActionResult.failure_result(
                f"Slot {slot_idx} is already occupied"
            )
        
        # 创建订单（生成瞬间锁定当前总 visibility，与真实端同语义）
        timeout = self._calculate_timeout(recipe, is_rush)
        order = Order(
            order_id=self._next_order_id,
            recipe_slug=recipe.slug,
            is_rush=is_rush,
            created_at=self._state.time,
            timeout_at=self._state.time + timeout,
            spawned_at_visibility=self._state.total_visibility,
        )
        
        # 存储模拟器专有数据
        self._order_recipes[order.order_id] = recipe
        self._order_condiments[order.order_id] = condiments if condiments else {}
        self._order_visibility[order.order_id] = self._state.total_visibility
        
        self._next_order_id += 1
        
        # 放置订单
        self._state.orders[slot_idx] = order
        
        # 创建事件
        event = Event(
            timestamp=self._state.time,
            event_type=EventType.ORDER_APPEARED,
            details={
                'order_id': order.order_id,
                'recipe': recipe.slug,
                'slot': slot_idx,
                'rush': is_rush,
                'timeout_at': order.timeout_at
            }
        )
        
        self._event_history.append(event)
        
        if self._debug:
            print(f"Order {order.order_id} injected to slot {slot_idx}")
        
        return ActionResult.success_result([event])
    
    # ============================================================================
    # 核心操作方法
    # ============================================================================
    
    def start_cooking(self, ingredient: str, cooker: str) -> ActionResult:
        """
        开始在指定灶台烹饪食材
        
        验证：
        - 游戏必须已配置（已选菜谱）
        - ingredient 必须在 available_ingredients 中
        - cooker 必须在 available_cookers 中
        
        Args:
            ingredient: 食材名称
            cooker: 灶台名称
            
        Returns:
            ActionResult: 操作结果
        """
        # 检查游戏是否已配置
        if not self._game_config.is_configured:
            return ActionResult.failure_result("Game not configured. Call setup_from_recipes() first.")
        
        # 检查食材是否在本局可用
        if ingredient not in self._game_config.available_ingredients:
            return ActionResult.failure_result(
                f"Ingredient '{ingredient}' not available in this game. "
                f"Available: {self._game_config.available_ingredients}"
            )
        
        # 检查灶台是否在本局可用
        if cooker not in self._game_config.available_cookers:
            return ActionResult.failure_result(
                f"Cooker '{cooker}' not available in this game. "
                f"Available: {self._game_config.available_cookers}"
            )
        
        # 检查灶台是否存在
        if cooker not in self._state.cookers:
            return ActionResult.failure_result(f"Cooker '{cooker}' does not exist")
        
        cooker_state = self._state.cookers[cooker]
        
        # 检查灶台是否繁忙
        if cooker_state.busy:
            return ActionResult.failure_result(f"Cooker '{cooker}' is busy")
        
        # 查找配方中的烹饪时间
        duration = None
        for recipe in self._recipes.values():
            for ing in recipe.ingredients:
                if ing.name == ingredient and ing.cooker_type == cooker:
                    duration = ing.duration
                    break
            if duration is not None:
                break
        
        # 如果没有找到配方，使用默认时间（或拒绝）
        if duration is None:
            return ActionResult.failure_result(
                f"No recipe found for ingredient '{ingredient}' on cooker '{cooker}'"
            )
        
        # 设置灶台状态
        cooker_state.busy = True
        cooker_state.item_name = ingredient
        cooker_state.cooker_type = cooker
        cooker_state.started_at = self._state.time
        cooker_state.done_at = self._state.time + duration
        cooker_state.expired_at = cooker_state.done_at + self.COOKER_RETENTION
        
        # 创建事件
        event = Event(
            timestamp=self._state.time,
            event_type=EventType.COOKING_STARTED,
            details={
                'ingredient': ingredient,
                'cooker': cooker,
                'duration': duration,
                'done_at': cooker_state.done_at
            }
        )
        
        self._event_history.append(event)
        
        if self._debug:
            print(f"Started cooking {ingredient} on {cooker} (done at {cooker_state.done_at:.1f}s)")
        
        return ActionResult.success_result([event])
    
    def move_to_assembly(self, cooker: str) -> ActionResult:
        """
        将烹饪完成的食材从灶台移动到组装站
        
        Args:
            cooker: 灶台名称
            
        Returns:
            ActionResult: 操作结果
        """
        # 检查灶台是否存在
        if cooker not in self._state.cookers:
            return ActionResult.failure_result(f"Cooker '{cooker}' does not exist")
        
        cooker_state = self._state.cookers[cooker]
        
        # 检查灶台是否有食材
        if not cooker_state.busy or cooker_state.item_name is None:
            return ActionResult.failure_result(f"Cooker '{cooker}' has no ingredient")
        
        # 检查烹饪是否完成
        if cooker_state.done_at is None or self._state.time < cooker_state.done_at:
            return ActionResult.failure_result(
                f"Ingredient on '{cooker}' is not cooked yet"
            )
        
        # 检查食材是否过期
        if cooker_state.expired_at and self._state.time >= cooker_state.expired_at:
            return ActionResult.failure_result(
                f"Ingredient on '{cooker}' has expired, must clear to trash"
            )
        
        # 获取食材信息
        ingredient_name = cooker_state.item_name
        cooker_type = cooker_state.cooker_type
        
        # 检查组装站兼容性
        if not self._state.assembly.can_add_ingredient(ingredient_name, cooker_type):
            return ActionResult.failure_result(
                f"Ingredient {ingredient_name} is not compatible with current assembly"
            )
        
        # 移动到组装站
        self._state.assembly.ingredients.append((ingredient_name, cooker_type, self._state.time))
        
        # 设置目标配方 - use current order if exists, otherwise search for matching recipe
        if self._state.assembly.target_recipe_slug is None:
            # First, try to find the recipe from current orders that need this ingredient
            for order in self._state.orders:
                if order and not order.is_completed:
                    # Check if this ingredient is part of the order's recipe
                    recipe = self._get_recipe(order)
                    for ing in recipe.ingredients:
                        if ing.name == ingredient_name and ing.cooker_type == cooker_type:
                            self._state.assembly.target_recipe_slug = recipe.slug
                            break
                    if self._state.assembly.target_recipe_slug:
                        break

            # Fallback: search for any recipe that has this ingredient (original behavior)
            if self._state.assembly.target_recipe_slug is None:
                for recipe in self._recipes.values():
                    for ing in recipe.ingredients:
                        if ing.name == ingredient_name and ing.cooker_type == cooker_type:
                            self._state.assembly.target_recipe_slug = recipe.slug
                            break
                    if self._state.assembly.target_recipe_slug:
                        break
        
        # 清空灶台
        cooker_state.clear()
        
        # 检查组装是否完成
        if self._state.assembly.is_complete:
            complete_event = Event(
                timestamp=self._state.time,
                event_type=EventType.ASSEMBLY_COMPLETED,
                details={
                    'recipe': self._state.assembly.target_recipe_slug,
                    'ingredients': [ing[0] for ing in self._state.assembly.ingredients]
                }
            )
            self._event_history.append(complete_event)
            if self._debug:
                slug = self._state.assembly.target_recipe_slug or 'Unknown'
                print(f"Assembly completed for recipe: {slug}")
        
        # 创建事件
        event = Event(
            timestamp=self._state.time,
            event_type=EventType.INGREDIENT_ADDED_TO_ASSEMBLY,
            details={
                'ingredient': ingredient_name,
                'cooker': cooker,
                'assembly_ingredients': [ing[0] for ing in self._state.assembly.ingredients]
            }
        )
        
        self._event_history.append(event)
        
        if self._debug:
            print(f"Moved {ingredient_name} from {cooker} to assembly")
        
        return ActionResult.success_result([event])
    
    def add_condiment(self, condiment_name: str) -> ActionResult:
        """
        向组装站添加调料
        
        规则：
        - 组装站必须非空且所有食材已到齐
        - 调料必须在目标配方的调料列表中
        - 每种调料有数量上限（recipe.condiments中指定）
        - 总调料数不超过 MAX_CONDIMENTS (3)
        - 无效调料（非recipe要求）会被忽略，返回success但不实际添加
        
        Args:
            condiment_name: 调料名称
            
        Returns:
            ActionResult: 操作结果
        """
        # 检查组装站是否有食材
        if not self._state.assembly.ingredients:
            return ActionResult.failure_result("Assembly is empty, cannot add condiment")
        
        # 检查组装站是否已完成（所有食材已到齐）
        if not self._state.assembly.is_complete:
            return ActionResult.failure_result(
                "Assembly is not complete, add all ingredients first"
            )
        
        # 获取目标配方
        recipe_slug = self._state.assembly.target_recipe_slug
        if recipe_slug is None:
            return ActionResult.failure_result("No target recipe for assembly")
        recipe = self._recipes.get(recipe_slug)
        if recipe is None:
            return ActionResult.failure_result(f"Unknown recipe slug: {recipe_slug}")
        
        # 检查是否为无效调料（非recipe要求）- 忽略但不报错
        if condiment_name not in recipe.condiments:
            if self._debug:
                print(f"Condiment '{condiment_name}' ignored (not in recipe)")
            return ActionResult.success_result([])
        
        # 检查总调料数是否已达上限
        total_condiments = sum(self._state.assembly.condiments.values())
        if total_condiments >= self.MAX_CONDIMENTS:
            return ActionResult.failure_result(
                f"Maximum condiments ({self.MAX_CONDIMENTS}) reached"
            )
        
        # 检查该调料是否已达上限
        current_count = self._state.assembly.condiments.get(condiment_name, 0)
        max_count = recipe.condiments[condiment_name]
        if current_count >= max_count:
            return ActionResult.failure_result(
                f"Condiment '{condiment_name}' already at maximum ({max_count})"
            )
        
        # 添加调料
        self._state.assembly.condiments[condiment_name] = current_count + 1
        
        # 创建事件
        event = Event(
            timestamp=self._state.time,
            event_type=EventType.CONDIMENT_ADDED,
            details={
                'condiment': condiment_name,
                'count': current_count + 1,
                'total_condiments': total_condiments + 1
            }
        )
        
        self._event_history.append(event)
        
        if self._debug:
            print(f"Added condiment {condiment_name} ({current_count + 1}/{max_count})")
        
        return ActionResult.success_result([event])
    
    def move_to_stockpile(self, cooker: str, slot_name: str) -> ActionResult:
        """
        将烹饪完成的食材从灶台移到库存
        
        规则：
        - 灶台必须存在且有食材
        - 烹饪必须完成
        - 食材不能已过期
        - 库存槽位必须兼容（同食材+同厨具）
        - 库存槽位不能已满（最多5份）
        
        Args:
            cooker: 灶台名称
            slot_name: 库存槽位名称
            
        Returns:
            ActionResult: 操作结果
        """
        # 检查灶台是否存在
        if cooker not in self._state.cookers:
            return ActionResult.failure_result(f"Cooker '{cooker}' does not exist")
        
        cooker_state = self._state.cookers[cooker]
        
        # 检查灶台是否有食材
        if not cooker_state.busy or cooker_state.item_name is None:
            return ActionResult.failure_result(f"Cooker '{cooker}' has no ingredient")
        
        # 检查烹饪是否完成
        if cooker_state.done_at is None or self._state.time < cooker_state.done_at:
            return ActionResult.failure_result(
                f"Ingredient on '{cooker}' is not cooked yet"
            )
        
        # 检查食材是否过期
        if cooker_state.expired_at and self._state.time >= cooker_state.expired_at:
            return ActionResult.failure_result(
                f"Ingredient on '{cooker}' has expired, must clear to trash"
            )
        
        # 检查库存槽位是否存在
        if slot_name not in self._state.stockpile:
            return ActionResult.failure_result(f"Stockpile slot '{slot_name}' does not exist")
        
        stockpile_slot = self._state.stockpile[slot_name]
        ingredient_name = cooker_state.item_name
        cooker_type = cooker_state.cooker_type
        
        # 检查库存槽位兼容性
        if not stockpile_slot.can_add(ingredient_name, cooker_type):
            return ActionResult.failure_result(
                f"Cannot add {ingredient_name} ({cooker_type}) to slot '{slot_name}' "
                f"(incompatible with existing: {stockpile_slot.item_name})"
            )
        
        # 检查库存槽位是否已满
        if stockpile_slot.count >= self.MAX_STOCKPILE:
            return ActionResult.failure_result(
                f"Stockpile slot '{slot_name}' is full ({self.MAX_STOCKPILE} items)"
            )
        
        # 添加到库存
        stockpile_slot.add(ingredient_name, cooker_type)
        
        # 清空灶台
        cooker_state.clear()
        
        # 创建事件
        event = Event(
            timestamp=self._state.time,
            event_type=EventType.INGREDIENT_MOVED_TO_STOCKPILE,
            details={
                'ingredient': ingredient_name,
                'cooker': cooker,
                'stockpile_slot': slot_name,
                'count': stockpile_slot.count
            }
        )
        
        self._event_history.append(event)
        
        if self._debug:
            print(f"Moved {ingredient_name} from {cooker} to stockpile {slot_name}")
        
        return ActionResult.success_result([event])
    
    def pull_from_stockpile(self, slot_name: str) -> ActionResult:
        """
        从库存取出食材放到组装站
        
        规则：
        - 库存槽位必须存在且有食材
        - 食材必须与当前组装站兼容
        
        Args:
            slot_name: 库存槽位名称
            
        Returns:
            ActionResult: 操作结果
        """
        # 检查库存槽位是否存在
        if slot_name not in self._state.stockpile:
            return ActionResult.failure_result(f"Stockpile slot '{slot_name}' does not exist")
        
        stockpile_slot = self._state.stockpile[slot_name]
        
        # 检查库存是否有食材
        if stockpile_slot.count <= 0:
            return ActionResult.failure_result(f"Stockpile slot '{slot_name}' is empty")
        
        ingredient_name = stockpile_slot.item_name
        cooker_type = stockpile_slot.cooker_type
        
        # 检查组装站兼容性
        if not self._state.assembly.can_add_ingredient(ingredient_name, cooker_type):
            return ActionResult.failure_result(
                f"Ingredient {ingredient_name} is not compatible with current assembly"
            )
        
        # 添加到组装站
        self._state.assembly.ingredients.append((ingredient_name, cooker_type, self._state.time))
        
        # 设置目标配方（如果是第一个食材）
        if self._state.assembly.target_recipe_slug is None:
            for recipe in self._recipes.values():
                for ing in recipe.ingredients:
                    if ing.name == ingredient_name and ing.cooker_type == cooker_type:
                        self._state.assembly.target_recipe_slug = recipe.slug
                        break
                if self._state.assembly.target_recipe_slug:
                    break
        
        # 从库存移除
        stockpile_slot.remove_one()
        
        # 创建事件
        event = Event(
            timestamp=self._state.time,
            event_type=EventType.INGREDIENT_ADDED_TO_ASSEMBLY,
            details={
                'ingredient': ingredient_name,
                'cooker': cooker_type,
                'source': f'stockpile:{slot_name}',
                'assembly_ingredients': [ing[0] for ing in self._state.assembly.ingredients]
            }
        )
        
        self._event_history.append(event)
        
        if self._debug:
            print(f"Pulled {ingredient_name} from stockpile {slot_name} to assembly")
        
        return ActionResult.success_result([event])
    
    def move_to_trash(self, from_location: str) -> ActionResult:
        """
        将食材丢弃到垃圾桶
        
        支持的来源位置：
        - cooker名称（如 'grill'）
        - stockpile名称（如 'slot0'）
        - 'assembly'（清空整个组装站）
        
        Args:
            from_location: 来源位置
            
        Returns:
            ActionResult: 操作结果
        """
        # 检查是否为组装站
        if from_location == 'assembly':
            if not self._state.assembly.ingredients:
                return ActionResult.failure_result("Assembly is already empty")
            
            # 记录被丢弃的食材
            discarded = [ing[0] for ing in self._state.assembly.ingredients]
            
            # 清空组装站
            self._state.assembly.clear()
            
            event = Event(
                timestamp=self._state.time,
                event_type=EventType.INGREDIENT_MOVED_TO_TRASH,
                details={
                    'source': 'assembly',
                    'discarded': discarded
                }
            )
            
            self._event_history.append(event)
            
            if self._debug:
                print(f"Trashed assembly: {discarded}")
            
            return ActionResult.success_result([event])
        
        # 检查是否为灶台
        if from_location in self._state.cookers:
            cooker_state = self._state.cookers[from_location]
            
            if not cooker_state.busy or cooker_state.item_name is None:
                return ActionResult.failure_result(
                    f"Cooker '{from_location}' has no ingredient to trash"
                )
            
            ingredient_name = cooker_state.item_name
            
            # 清空灶台
            cooker_state.clear()
            
            event = Event(
                timestamp=self._state.time,
                event_type=EventType.INGREDIENT_MOVED_TO_TRASH,
                details={
                    'source': f'cooker:{from_location}',
                    'ingredient': ingredient_name
                }
            )
            
            self._event_history.append(event)
            
            if self._debug:
                print(f"Trashed {ingredient_name} from cooker {from_location}")
            
            return ActionResult.success_result([event])
        
        # 检查是否为库存
        if from_location in self._state.stockpile:
            stockpile_slot = self._state.stockpile[from_location]
            
            if stockpile_slot.count <= 0:
                return ActionResult.failure_result(
                    f"Stockpile slot '{from_location}' is empty"
                )
            
            ingredient_name = stockpile_slot.item_name
            
            # 清空库存槽位
            stockpile_slot.clear()
            
            event = Event(
                timestamp=self._state.time,
                event_type=EventType.INGREDIENT_MOVED_TO_TRASH,
                details={
                    'source': f'stockpile:{from_location}',
                    'ingredient': ingredient_name
                }
            )
            
            self._event_history.append(event)
            
            if self._debug:
                print(f"Trashed stockpile {from_location}")
            
            return ActionResult.success_result([event])
        
        return ActionResult.failure_result(f"Unknown location: '{from_location}'")
    
    def clear_cooker(self, cooker: str) -> ActionResult:
        """
        清理灶台上的过期食材
        
        规则：
        - 灶台必须存在
        - 灶台必须有食材
        - 食材必须已过期
        
        Args:
            cooker: 灶台名称
            
        Returns:
            ActionResult: 操作结果
        """
        # 检查灶台是否存在
        if cooker not in self._state.cookers:
            return ActionResult.failure_result(f"Cooker '{cooker}' does not exist")
        
        cooker_state = self._state.cookers[cooker]
        
        # 检查灶台是否有食材
        if not cooker_state.busy or cooker_state.item_name is None:
            return ActionResult.failure_result(f"Cooker '{cooker}' has no ingredient")
        
        # 检查食材是否已过期
        if cooker_state.expired_at is None or self._state.time < cooker_state.expired_at:
            return ActionResult.failure_result(
                f"Ingredient on '{cooker}' has not expired yet "
                f"(expires at {cooker_state.expired_at:.1f}s, now {self._state.time:.1f}s)"
            )
        
        ingredient_name = cooker_state.item_name
        
        # 清空灶台
        cooker_state.clear()
        
        # 创建事件
        event = Event(
            timestamp=self._state.time,
            event_type=EventType.INGREDIENT_MOVED_TO_TRASH,
            details={
                'source': f'cooker:{cooker}',
                'ingredient': ingredient_name,
                'reason': 'expired'
            }
        )
        
        self._event_history.append(event)
        
        if self._debug:
            print(f"Cleared expired {ingredient_name} from cooker {cooker}")
        
        return ActionResult.success_result([event])
    
    def serve_order(self, slot_idx: int) -> ActionResult:
        """
        提交订单（将组装好的菜品送到取餐台）
        
        Args:
            slot_idx: 订单槽位索引
            
        Returns:
            ActionResult: 操作结果
        """
        # 检查动画窗口
        if self.is_in_animation_window():
            return ActionResult.failure_result(
                f"Cannot serve during animation window (until {self._animation_until:.1f}s)"
            )
        
        # 检查槽位索引
        if slot_idx < 0 or slot_idx >= self.MAX_SLOTS:
            return ActionResult.failure_result(f"Invalid slot index: {slot_idx}")
        
        # 检查槽位是否有订单
        order = self._state.orders[slot_idx]
        if order is None:
            return ActionResult.failure_result(f"No order at slot {slot_idx}")
        
        # 检查组装站是否有完成的菜品
        if not self._state.assembly.is_complete:
            return ActionResult.failure_result("Assembly is not complete")
        
        # 检查组装站的配方是否匹配订单的配方
        recipe = self._get_recipe(order)
        if self._state.assembly.target_recipe_slug != recipe.slug:
            return ActionResult.failure_result(
                f"Assembly recipe does not match order recipe"
            )
        
        # 检查调料是否满足要求
        assembly_condiments = self._state.assembly.condiments
        
        # 验证每种调料数量
        for condiment_name, required_count in recipe.condiments.items():
            actual_count = assembly_condiments.get(condiment_name, 0)
            if actual_count < required_count:
                return ActionResult.failure_result(
                    f"Missing condiment: {condiment_name} "
                    f"(need {required_count}, have {actual_count})"
                )
        
        # 计算得分（使用订单生成时锁定的 spawned_at_visibility）
        score = self._calculate_score(order, assembly_condiments)
        
        # 累加本订单的 visibility 到总 visibility（不影响本次得分）
        if self._reward_lookup is None:
            self._reward_lookup = RecipeRewardLookup()
        has_condiments = bool(assembly_condiments)
        visibility = self._reward_lookup.get_visibility(recipe.slug, has_condiments)
        self._state.total_visibility += visibility
        # 终局计分：累加逐单得分和（finalize 时 +总 visibility）
        self._total_score += score
        self._orders_served += 1
        
        # 标记订单完成
        order.served_at = self._state.time
        
        # 清空组装站
        self._state.assembly.clear()
        
        # 清除订单槽位
        self._state.orders[slot_idx] = None
        
        # 触发槽位前移
        self._advance_slots(self._state.time)
        
        # 创建事件
        event = Event(
            timestamp=self._state.time,
            event_type=EventType.ORDER_SERVED,
            details={
                'order_id': order.order_id,
                'slot': slot_idx,
                'recipe': recipe.slug,
                'score': score,
                'served_at': order.served_at,
                'visibility': visibility,
                'spawned_at_visibility': self._snapshot_visibility(order),
            }
        )
        
        self._event_history.append(event)
        
        if self._debug:
            print(f"Order {order.order_id} served from slot {slot_idx}, score: {score}, total_visibility: {self._state.total_visibility}")
        
        return ActionResult.success_result([event], score=score)
    
    def clear_assembly(self) -> ActionResult:
        """
        清空组装站（订单超时或配方错误时丢弃食材）
        
        Returns:
            ActionResult: 操作结果
        """
        if not self._state.assembly.ingredients:
            return ActionResult.failure_result("Assembly is already empty")
        
        discarded = [ing[0] for ing in self._state.assembly.ingredients]
        self._state.assembly.ingredients.clear()
        self._state.assembly.condiments.clear()
        self._state.assembly.target_recipe_slug = None
        
        return ActionResult.success_result()
    
    def _calculate_score(self, order: Order, assembly_condiments: dict[str, int]) -> float:
        """
        计算订单得分
        
        基于 reward.csv 查表，并应用订单生成时锁定的 visibility 区间加成。
        
        Args:
            order: 订单
            assembly_condiments: 组装站上的调料
            
        Returns:
            得分
        """
        if self._reward_lookup is None:
            self._reward_lookup = RecipeRewardLookup()
        
        has_condiments = bool(assembly_condiments)
        recipe_slug = order.recipe_slug
        return self._reward_lookup.get_score(
            recipe_slug,
            has_condiments=has_condiments,
            is_rush=order.is_rush,
            total_visibility=self._snapshot_visibility(order),
        )
    
    def _advance_slots(self, current_time: float) -> None:
        """触发槽位前移"""
        # 移除None值（已完成或超时的订单）
        new_orders = [o for o in self._state.orders if o is not None]
        # 填充None到右侧
        while len(new_orders) < self.MAX_SLOTS:
            new_orders.append(None)
        
        self._state.orders = new_orders
        
        # 设置动画窗口（1.5秒）
        self._animation_until = current_time + self.ANIMATION_DURATION
        
        # 检查剩余订单数量，设置刷新计时
        active_orders = sum(1 for o in self._state.orders if o is not None)
        if active_orders == 0:
            # 所有槽位为空，需要立即刷新
            self._needs_immediate_refresh = True
        else:
            # 有剩余订单，从当前时刻开始计时随机3-5秒后刷新
            # 每次订单过期都重置计时器
            self._next_order_refresh_time = current_time + self._random_order_interval()
        
        # 创建槽位前移事件
        event = Event(
            timestamp=current_time,
            event_type=EventType.SLOTS_ADVANCED,
            details={
                'animation_until': self._animation_until
            }
        )
        
        self._event_history.append(event)
        
        if self._debug:
            print(f"Slots advanced, animation until {self._animation_until}")
    
    # 其余操作将在后续实现...
    # start_cooking, move_to_assembly, serve_order, tick, etc.


# ============================================================================
# 辅助函数
# ============================================================================
    # 时间管理和订单生成
    # ============================================================================
    
    def tick(self, dt: float) -> list[Event]:
        """
        推进游戏时间并触发自动事件
        
        处理的事件包括：
        - 订单超时
        - 烹饪完成
        - 食材过期
        - 订单生成（基于4秒间隔）
        
        Args:
            dt: 推进的时间（秒）
            
        Returns:
            本次tick触发的事件列表
        """
        # 如果游戏已结束，不再推进时间
        if self.is_game_over():
            return []
        
        events: list[Event] = []
        
        # 计算新时间，但不能超过游戏结束时间
        new_time = min(self._state.time + dt, self._game_duration)
        
        # 先生成新订单（在超时检查之前）
        # 这样在订单超时重置计时器之前，可以先生成之前的订单
        order_events = self._generate_orders(new_time)
        events.extend(order_events)
        
        # 检查订单超时
        timeout_events = self._check_order_timeouts(new_time)
        events.extend(timeout_events)
        
        # 检查烹饪完成和食材过期
        cooking_events = self._check_cooking_progress(new_time)
        events.extend(cooking_events)
        
        # 更新时间
        self._state.time = new_time
        
        # 记录事件
        self._event_history.extend(events)
        
        return events
    
    def _check_order_timeouts(self, current_time: float) -> list[Event]:
        """检查并处理订单超时"""
        events = []
        
        for i, order in enumerate(self._state.orders):
            if order is None or order.is_completed:
                continue
            
            if current_time >= order.timeout_at:
                events.append(Event(
                    timestamp=current_time,
                    event_type=EventType.ORDER_TIMEOUT,
                    details={
                        'order_id': order.order_id,
                        'slot': i,
                        'recipe': order.recipe_slug
                    }
                ))
                
                # 清除订单并触发槽位前移
                self._state.orders[i] = None
                self._advance_slots(current_time)
        
        return events
    
    def _check_cooking_progress(self, current_time: float) -> list[Event]:
        """检查烹饪完成和食材过期"""
        events = []
        
        for cooker_name, cooker in self._state.cookers.items():
            if not cooker.busy:
                continue
            
            # 检查烹饪完成
            if cooker.done_at and current_time >= cooker.done_at:
                if not any(e.event_type == EventType.COOKING_COMPLETED 
                          for e in self._event_history[-10:]):
                    events.append(Event(
                        timestamp=current_time,
                        event_type=EventType.COOKING_COMPLETED,
                        details={
                            'cooker': cooker_name,
                            'ingredient': cooker.item_name
                        }
                    ))
            
            # 检查食材过期
            if cooker.expired_at and current_time >= cooker.expired_at:
                events.append(Event(
                    timestamp=current_time,
                    event_type=EventType.INGREDIENT_EXPIRED,
                    details={
                        'cooker': cooker_name,
                        'ingredient': cooker.item_name
                    }
                ))
                
                # 清理过期食材
                cooker.clear()
        
        return events
    
    def _generate_orders(self, current_time: float) -> list[Event]:
        """生成新订单
        
        刷新规则：
        1. 立即刷新：如果所有槽位为空（提交或超时后），在动画窗口结束后立即生成
        2. 定时刷新：达到 _next_order_refresh_time 时生成新订单
           - 游戏开始后第 4 秒生成第一个订单
           - 订单提交/超时后有剩余订单时，从该时刻开始计时 4 秒
        
        注意：当 tick 时间跳过刷新点时，订单应在刷新时刻创建（而非当前时刻）
        注意：使用容差处理浮点精度问题（如 7.9 + 0.1 可能 ≠ 8.0）
        """
        events = []
        
        # 浮点比较容差
        EPSILON = 1e-9
        
        # 检查是否需要立即刷新（提交或超时后所有槽位为空）
        if self._needs_immediate_refresh:
            if current_time >= self._animation_until:
                event = self._create_new_order(current_time)
                if event:
                    events.append(event)
                    self._needs_immediate_refresh = False
                    # 更新下一次刷新时间（随机3-5秒）
                    self._next_order_refresh_time = current_time + self._random_order_interval()
            return events
        
        # 检查是否到达刷新时间（使用容差处理浮点精度）
        if current_time >= self._next_order_refresh_time - EPSILON:
            # 检查是否有空槽位
            active_orders = sum(1 for o in self._state.orders if o is not None)
            if active_orders < self.MAX_SLOTS:
                # 使用刷新时刻作为订单创建时间（而非当前时间）
                order_time = self._next_order_refresh_time
                event = self._create_new_order(order_time)
                if event:
                    events.append(event)
                    # 更新下一次刷新时间（随机3-5秒）
                    self._next_order_refresh_time = order_time + self._random_order_interval()
                    self._last_order_time = order_time
        
        return events
    
    def _create_new_order(self, created_at: float) -> Event | None:
        """
        创建一个新订单
        
        Args:
            created_at: 订单创建时间（用于计算动画和超时）
            
        Returns:
            生成的事件，如果没有生成则返回None
        """
        # 查找空槽位
        empty_slot = None
        for i, order in enumerate(self._state.orders):
            if order is None:
                empty_slot = i
                break
        
        if empty_slot is None:
            return None
        
        # 获取可用配方（从选中的配方中随机选择）
        if not self._recipes:
            return None
        
        # 使用选中的配方，如果没有则使用所有配方
        if self._game_config.selected_recipes:
            available_slugs = [s for s in self._game_config.selected_recipes if s in self._recipes]
            if available_slugs:
                recipe_slug = random.choice(available_slugs)
                recipe = self._recipes[recipe_slug]
            else:
                recipe = list(self._recipes.values())[0]
        else:
            recipe = list(self._recipes.values())[0]
        
        # 随机决定是否rush（10%概率）
        is_rush = random.random() < 0.10
        
        # 计算超时时间（基于recipe）
        timeout = self._calculate_timeout(recipe, is_rush)
        
# 创建订单（生成瞬间锁定当前总 visibility，与真实端同语义）
        order = Order(
            order_id=self._next_order_id,
            recipe_slug=recipe.slug,
            is_rush=is_rush,
            created_at=created_at,
            timeout_at=created_at + timeout,
            spawned_at_visibility=self._state.total_visibility,
        )
        
        # 存储模拟器专有数据
        self._order_recipes[order.order_id] = recipe
        self._order_condiments[order.order_id] = dict(recipe.condiments)
        self._order_visibility[order.order_id] = self._state.total_visibility
        
        self._next_order_id += 1
        
        # 放置订单
        self._state.orders[empty_slot] = order
        
        # 设置动画窗口（1秒）
        self._animation_until = created_at + 1.0
        
        # 创建事件
        event = Event(
            timestamp=created_at,
            event_type=EventType.ORDER_APPEARED,
            details={
                'order_id': order.order_id,
                'recipe': recipe.slug,
                'slot': empty_slot,
                'rush': is_rush,
                'timeout_at': order.timeout_at,
                'animation_until': self._animation_until
            }
        )
        
        self._event_history.append(event)
        
        if self._debug:
            print(f"Order {order.order_id} appeared at slot {empty_slot} "
                  f"(rush={is_rush}, animation until {self._animation_until})")
        
        return event
    

# 模块导出
__all__ = [
    'GameSimulator',
    'ActionResult',
]
