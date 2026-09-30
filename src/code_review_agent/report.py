"""Render the final review as Markdown."""

from collections import Counter

from .models import SEVERITY_ORDER

_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}


def render_markdown(state: dict) -> str:
    review = state.get("review")
    kept = state.get("kept", [])
    raw = state.get("findings", [])
    selected = state.get("selected", [])
    out: list[str] = [f"# Code Review: `{state.get('target', '')}`", ""]

    if review:
        out += [
            f"**Overall risk:** {_ICON.get(review.risk_level, '')} {review.risk_level.upper()}",
            "",
            review.summary,
            "",
        ]
    if state.get("overview"):
        out += [f"> **Project:** {state['overview']}", ""]

    counts = Counter(f.severity for f in kept)
    out += [
        "| Critical | High | Medium | Low | Files reviewed | Raw → kept |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {counts['critical']} | {counts['high']} | {counts['medium']} | {counts['low']} "
        f"| {len(selected)} | {len(raw)} → {len(kept)} |",
        "",
    ]

    if review and review.top_priorities:
        out += ["## Fix these first", ""]
        out += [f"{i}. {p}" for i, p in enumerate(review.top_priorities, 1)]
        out.append("")

    if kept:
        out += ["## Findings", ""]
        for f in sorted(kept, key=lambda f: (SEVERITY_ORDER[f.severity], f.file, f.line or 0)):
            where = f"`{f.file}:{f.line}`" if f.line else f"`{f.file}`"
            out += [
                f"### {_ICON[f.severity]} {f.title}",
                f"{where} · **{f.severity}** · {f.specialist}",
                "",
                f.explanation,
                "",
                f"**Fix:** {f.suggestion}",
                "",
            ]

    if selected:
        out += ["<details><summary>Files reviewed</summary>", ""]
        out += [f"- `{f.path}`" for f in selected]
        out += ["", "</details>", ""]

    if state.get("errors"):
        out += ["## Warnings", ""] + [f"- {e}" for e in state["errors"]] + [""]

    return "\n".join(out)
