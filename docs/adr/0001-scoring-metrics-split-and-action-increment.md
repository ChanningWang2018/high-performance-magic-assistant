# ADR-0001：度量抽离小步先行（D+B）与 Action 三增量、S1→S2→S3 顺序

状态：已接受（Ticket #8 落地 S1 部分）

## 背景

- 终局计分缺 visibility 加成，历史基准绝对值不可比（`docs/game_rules.md` 终局公式）。
- 环境对象身兼状态、订单、计时、统计、推断数职；模拟器与真实 Runner 执行语义分叉风险。
- Action 缺少类型与优先级表达，但现有 14 个 Action 子类字段名不能 break。

## 决策

1. **小步 D+B 先行**：先抽离度量上下文（Metrics/ScoringContext：统计查询、总分、终局结算）
   与执行上下文（S2 才做：滑动串行封装），观察世界与规划上下文保持不动。
   本次只做度量抽离（D），执行封装留给 Ticket #9。
2. **Action 三增量**：现状字段冻结，仅增类型、优先级、取消标记（Ticket #11 做）。
   本次不碰 Action。
3. **顺序 S1→S2→S3**：终局计分与度量抽离（#8）→ 执行封装与耗时基线（#9）
   → Action 调度表达与双端对齐（#11）→ 三项回归落袋（#12）。
   #11 的回放一致性 oracle 由 #8 的终局分数定义；#12 的三项回归依赖 #8/#9/#11。

## 后果

- `hawarma.core.scoring.ScoringContext` 为改计分的唯一入口；
  `GameEnv` / `SimEnv` / `GameSimulator` 只转发、不自算。
- 终局口径 `v2 = 逐单和 + 总 visibility`，`finalize()` 幂等；
  Episode 与基准同时保留 `total_reward`（单步累积）与 `final_score`（终局）两个数字，
  按 `scoring_version` 隔离展示。
- 快照语义：两端统一以 `Order.spawned_at_visibility` 域字段为准，
  模拟器旁路字典仅作兼容回退；修复真实 Runner 扫描同步漏传快照。
- Gastronome / Dessert 行为不变（本次只加度量，不改决策与执行）。
