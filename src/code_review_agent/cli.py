"""Command line entry point: `code-review <path-or-github-url>`."""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .graph import ALL_SPECIALISTS, build_graph
from .llm import default_model

console = Console()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="code-review",
        description="Multi-agent code reviewer: a planner, parallel security/bug/performance "
        "specialists, and a lead reviewer that merges their findings.",
    )
    p.add_argument("target", help="Local folder or public GitHub URL (https://github.com/owner/repo)")
    p.add_argument("--model", default=None,
                   help=f"LangChain 'provider:model' string (default: $REVIEW_MODEL or {default_model()})")
    p.add_argument("--out", default="code-review-report.md", help="Where to write the Markdown report")
    p.add_argument("--max-files", type=int, default=25, help="Max files the planner may select")
    p.add_argument("--specialists", default=",".join(ALL_SPECIALISTS),
                   help=f"Comma-separated subset of: {', '.join(ALL_SPECIALISTS)}")
    p.add_argument("--concurrency", type=int, default=6, help="Max LLM calls in flight at once")
    return p.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    specialists = [s.strip() for s in args.specialists.split(",") if s.strip()]
    unknown = set(specialists) - set(ALL_SPECIALISTS)
    if unknown:
        console.print(f"[red]Unknown specialist(s): {', '.join(sorted(unknown))}[/red]")
        return 2

    model = args.model or default_model()
    console.print(f"[bold]Reviewing[/bold] {args.target}  [dim]model: {model}[/dim]\n")

    graph = build_graph()
    config = {"configurable": {"model": model}, "max_concurrency": args.concurrency}
    inputs = {"target": args.target, "max_files": args.max_files, "specialists": specialists}

    final: dict = {}
    async for mode, chunk in graph.astream(inputs, config, stream_mode=["updates", "values"]):
        if mode == "values":
            final = chunk
            continue
        for node, update in chunk.items():
            _print_progress(node, update or {})

    Path(args.out).write_text(final.get("report_markdown", ""), encoding="utf-8")
    _print_summary(final)
    console.print(f"\n[bold green]Report written to[/bold green] {Path(args.out).resolve()}")
    return 0


def _print_progress(node: str, update: dict) -> None:
    if node == "load_repo":
        console.print(f"📂 Loaded {len(update.get('all_files', []))} source files")
    elif node == "plan":
        n, b = len(update.get("selected", [])), len(update.get("batches", []))
        console.print(f"🧭 Planner picked {n} files → {b} batch(es)")
        if update.get("overview"):
            console.print(f"   [dim]{update['overview']}[/dim]")
    elif node == "review":
        for err in update.get("errors", []):
            console.print(f"   [red]✗ {err}[/red]")
        found = update.get("findings", [])
        if found:
            console.print(f"   ✓ {found[0].specialist} reviewer: {len(found)} finding(s)")
        elif not update.get("errors"):
            console.print("   ✓ a reviewer finished: no findings")
    elif node == "aggregate":
        console.print(f"🧑‍⚖️ Lead reviewer kept {len(update.get('kept', []))} finding(s)")


def _print_summary(state: dict) -> None:
    review, kept = state.get("review"), state.get("kept", [])
    if review:
        console.print(f"\n[bold]Risk: {review.risk_level.upper()}[/bold]  {review.summary}")
    if not kept:
        return
    table = Table(show_lines=False)
    for col in ("Severity", "Where", "Issue", "Found by"):
        table.add_column(col)
    for f in kept[:15]:
        table.add_row(f.severity, f"{f.file}:{f.line or '?'}", f.title, f.specialist)
    console.print(table)
    if len(kept) > 15:
        console.print(f"[dim]…and {len(kept) - 15} more in the report[/dim]")


def main() -> None:
    load_dotenv()
    sys.exit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()
