"""无界面命令行入口（工单 11：python -m reco_cli）。"""

from __future__ import annotations

import argparse
import io
import sys

__all__ = ["main"]

__version__ = "0.2.0"

_SUBCOMMANDS = ("report", "scan", "extract", "recommend", "feedback", "explain", "replay", "eval")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reco_cli", description="reco-cli 无界面命令行入口（工单 11）")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("report", help="画像诊断报告（样本量/轴覆盖/权重/CV）")
    p.add_argument("--db", required=True)
    p.add_argument("--write", action="store_true", help="写 reports/reco-<date>.json")
    p.add_argument("--reports-dir", default=None)

    p = sub.add_parser("scan", help="扫描专区并输出快照")
    p.add_argument("root")
    p.add_argument("--db", default=None)

    p = sub.add_parser("extract", help="批量特征提取 → 向量（事件流投影）")
    p.add_argument("--db", required=True)
    p.add_argument("--limit", type=int, default=None, help="最多处理的书籍数（默认全部）")

    p = sub.add_parser("recommend", help="推荐列表（rank 唯一入口）")
    p.add_argument("--db", required=True)
    p.add_argument("--json", action="store_true", default=True)

    p = sub.add_parser("feedback", help="反馈入口（dislike/like/add_to_pool）")
    p.add_argument("kind", choices=["dislike", "like", "add_to_pool"])
    p.add_argument("--db", required=True)
    p.add_argument("--book-id", required=True)
    p.add_argument("--axis", default=None)
    p.add_argument("--value", type=float, default=1.0)

    p = sub.add_parser("explain", help="单书推荐解释")
    p.add_argument("--db", required=True)
    p.add_argument("--book-id", required=True)

    p = sub.add_parser("replay", help="事件流重放校验（ADR-0003）")
    p.add_argument("--db", required=True)

    p = sub.add_parser("eval", help="评估（小样本诚实降级）")
    p.add_argument("--db", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """argparse 路由 + UTF-8 输出修复（T0 占位乱码回归点）。

    西文 locale 控制台/管道下 stdout 强制 UTF-8：Windows 控制台走
    io.TextIOWrapper 重配；PYTHONIOENCODING=cp1252 管道场景由子进程
    回归测试守护（test_C2）。未知子命令 exit 2（argparse 语义）。
    """
    # UTF-8 输出修复（工单 11：西文 locale 控制台/管道不乱码）。
    # reconfigure 原地改编码，不替换 stdout 对象——替换会让旧 TextIOWrapper
    # 被 GC 时关闭底层缓冲，级联炸掉 pytest capsys/tempfile（首轮 25 ERROR 根因）。
    try:
        if sys.platform == "win32" and getattr(sys.stdout, "encoding", "") not in ("utf-8", "utf8"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, ValueError, OSError):
        pass  # 非 TextIOWrapper（如测试捕获对象）跳过
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    parser = _build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help(sys.stdout)  # 无参 = 主动看帮助 → stdout（T0 契约：exit 0）
        return 0  # 未知子命令才走 stderr + 非 0

    from reco_cli import app

    if ns.command == "report":
        return app.cmd_report(ns.db, write=ns.write, reports_dir=ns.reports_dir)
    if ns.command == "scan":
        return app.cmd_scan(ns.root, db=ns.db)
    if ns.command == "extract":
        return app.cmd_extract(ns.db, limit=ns.limit)
    if ns.command == "recommend":
        return app.cmd_recommend(ns.db, as_json=ns.json)
    if ns.command == "feedback":
        return app.cmd_feedback(ns.kind, db=ns.db, book_id=ns.book_id,
                                axis=ns.axis, value=ns.value)
    if ns.command == "explain":
        return app.cmd_explain(ns.db, book_id=ns.book_id)
    if ns.command == "replay":
        return app.cmd_replay(ns.db)
    if ns.command == "eval":
        return app.cmd_eval(ns.db)
    parser.print_help(sys.stderr)
    return 2
