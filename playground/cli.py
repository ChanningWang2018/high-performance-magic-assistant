"""
Playground CLI

统一入口：python -m playground ...

命令：
    run     运行单局游戏
    bench   运行基准测试
    replay  回放游戏记录

策略通过 hawarma.agent.registry 注册表加载，
与真实游戏共享同一套策略配置。
"""

from __future__ import annotations

import argparse
import sys

from hawarma.agent.registry import list_strategies, get_strategy


def _parse_recipes(recipes_arg: str | None) -> list[str] | None:
    """将逗号分隔的 recipe slugs 解析为列表"""
    if not recipes_arg:
        return None
    return [s.strip() for s in recipes_arg.split(",")]


def _load_strategy(name: str):
    """通过注册表加载策略"""
    try:
        return get_strategy(name)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_run(args):
    """运行单局游戏"""
    from playground.env.sim import SimEnv
    from playground.agents.base import Agent
    from playground.core.episode import run_episode
    from playground.replay.recorder import save_replay

    strategy = _load_strategy(args.strategy)

    env = SimEnv()
    agent = Agent(strategy)

    result = run_episode(
        env,
        agent,
        seed=args.seed,
        record_history=args.record,
        recipe_slugs=_parse_recipes(args.recipes),
    )

    print(f"\nGame completed!")
    print(f"  Seed: {result.seed}")
    print(f"  Strategy: {result.strategy_name}")
    print(f"  Total Reward: {result.total_reward:.0f}")
    print(f"  Total Visibility: {result.total_visibility:.0f}")
    print(f"  Final Score: {result.final_score:.0f} [{result.scoring_version}]")
    print(f"  Steps: {result.steps}")
    print(f"  Actions: {result.actions_taken}")

    if args.record and result.history:
        save_replay(result, args.output)
        print(f"  Replay saved to {args.output}")


def cmd_bench(args):
    """运行基准测试"""
    from playground.env.sim import SimEnv
    from playground.bench.bench import run_benchmark
    from playground.bench.compare import print_comparison, export_csv, export_json

    all_strategies = list_strategies()

    if args.strategies:
        names = [s.strip() for s in args.strategies.split(",")]
    else:
        # 默认只比较 Tier 1 推荐策略
        names = ["gastronome"]

    strategies = {}
    for name in names:
        if name not in all_strategies:
            print(f"Unknown strategy: {name}")
            print(f"Available: {', '.join(all_strategies)}")
            sys.exit(1)
        strategies[name] = _load_strategy(name)

    def env_factory():
        return SimEnv()

    results = run_benchmark(
        env_factory,
        strategies,
        num_games=args.games,
        recipe_slugs=_parse_recipes(args.recipes),
    )

    print_comparison(results)

    if args.csv:
        export_csv(results, args.csv)
    if args.json:
        export_json(results, args.json)


def cmd_replay(args):
    """回放游戏记录"""
    from playground.replay.recorder import replay_cli
    replay_cli(args.file)


def _format_strategy_list() -> str:
    """格式化策略列表用于 help 文本"""
    return ", ".join(list_strategies())


def main():
    parser = argparse.ArgumentParser(
        prog="playground",
        description="Hawarma Playground - RL-style game simulation and strategy benchmark",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    available_strategies = _format_strategy_list()

    # run
    run_parser = subparsers.add_parser("run", help="Run a single game")
    run_parser.add_argument("--seed", type=int, default=42, help="Random seed")
    run_parser.add_argument(
        "--strategy",
        type=str,
        default="default",
        help=f"Strategy name ({available_strategies})",
    )
    run_parser.add_argument("--record", action="store_true", help="Record replay")
    run_parser.add_argument("--output", type=str, default="playground_replay.json", help="Replay output file")
    run_parser.add_argument("--recipes", type=str, default=None, help="Comma-separated recipe slugs (e.g. beef_wrap,chicken_wrap)")
    run_parser.set_defaults(func=cmd_run)

    # bench
    bench_parser = subparsers.add_parser("bench", help="Run benchmark")
    bench_parser.add_argument("--games", type=int, default=50, help="Number of games per strategy")
    bench_parser.add_argument(
        "--strategies",
        type=str,
        default=None,
        help=f"Comma-separated strategy names ({available_strategies})",
    )
    bench_parser.add_argument("--recipes", type=str, default=None, help="Comma-separated recipe slugs (e.g. beef_wrap,chicken_wrap)")
    bench_parser.add_argument("--csv", type=str, default=None, help="Export to CSV")
    bench_parser.add_argument("--json", type=str, default=None, help="Export to JSON")
    bench_parser.set_defaults(func=cmd_bench)

    # replay
    replay_parser = subparsers.add_parser("replay", help="Replay a recorded game")
    replay_parser.add_argument("file", type=str, help="Replay JSON file")
    replay_parser.set_defaults(func=cmd_replay)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
