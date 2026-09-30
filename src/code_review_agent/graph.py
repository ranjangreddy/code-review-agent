"""The multi-agent review workflow as a LangGraph StateGraph.

    START
      │
    load_repo            clone / read the source files               (plain code)
      │
    plan                 planner agent picks the files to review     (LLM)
      │
      ├──Send──► review(security, batch 1) ─┐
      ├──Send──► review(bugs, batch 1)     ─┤   one parallel task per specialist × batch   (LLM)
      ├──Send──► review(performance, …)    ─┤   findings merged by a list reducer
      │                                     ▼
      └───────────────────────────────► aggregate    lead reviewer dedupes and ranks   (LLM)
                                            │
                                          report     render markdown                    (plain code)
                                            │
                                           END
"""

import json
import operator
from pathlib import Path
from typing import Annotated, Optional, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from . import prompts
from .llm import get_llm
from .models import (
    SEVERITY_ORDER,
    AggregatedReview,
    ReviewFindings,
    ReviewPlan,
    TaggedFinding,
)
from .repo import SourceFile, load_source_files, make_batches, resolve_target, with_line_numbers
from .report import render_markdown

ALL_SPECIALISTS = list(prompts.SPECIALISTS)
BATCH_CHARS = 60_000
README_CHARS = 3_000


class ReviewState(TypedDict, total=False):
    # inputs
    target: str
    max_files: int
    specialists: list[str]
    # produced along the way
    root: str
    all_files: list[SourceFile]
    overview: str
    selected: list[SourceFile]
    batches: list[list[SourceFile]]
    findings: Annotated[list[TaggedFinding], operator.add]  # parallel reviewers append here
    errors: Annotated[list[str], operator.add]
    review: Optional[AggregatedReview]
    kept: list[TaggedFinding]
    report_markdown: str


class ReviewTask(TypedDict):
    """Payload for one parallel reviewer, sent with Send()."""

    specialist: str
    batch: list[SourceFile]
    batch_index: int
    batch_count: int
    overview: str


async def _structured(schema, system: str, human: str, config: RunnableConfig):
    llm = get_llm(config).with_structured_output(schema).with_retry(stop_after_attempt=3)
    return await llm.ainvoke([("system", system), ("human", human)], config)


# ------------------------------------------------------------------ nodes


def load_repo(state: ReviewState) -> dict:
    root = resolve_target(state["target"])
    return {"root": str(root), "all_files": load_source_files(root)}


async def plan(state: ReviewState, config: RunnableConfig) -> dict:
    files = state.get("all_files", [])
    max_files = state.get("max_files", 25)
    if not files:
        return {"overview": "", "selected": [], "batches": []}

    listing = "\n".join(f"{f.path} ({f.lines} lines)" for f in files)
    readme = _read_readme(Path(state["root"]))
    human = f"FILES:\n{listing}"
    if readme:
        human += f"\n\nREADME (truncated):\n{readme}"

    by_path = {f.path: f for f in files}
    try:
        result: ReviewPlan = await _structured(
            ReviewPlan, prompts.PLANNER.format(max_files=max_files), human, config
        )
        overview = result.overview
        chosen = [by_path[p] for p in dict.fromkeys(result.files) if p in by_path]
    except Exception:  # a planner failure shouldn't sink the review
        overview, chosen = "", []

    if not chosen:  # planner returned nothing usable: fall back to the first files
        chosen = files
    # Small project that fits one batch: review everything rather than trusting the planner's cut.
    # Skipped files are where secrets and config bugs hide (e.g. a hardcoded DB password).
    if len(files) <= max_files and sum(len(f.content) for f in files) <= BATCH_CHARS:
        chosen = chosen + [f for f in files if f not in chosen]
    chosen = chosen[:max_files]
    return {"overview": overview, "selected": chosen, "batches": make_batches(chosen, BATCH_CHARS)}


def fan_out(state: ReviewState):
    """Conditional edge: one Send per (specialist, batch), or straight to aggregate if nothing to do."""
    batches = state.get("batches", [])
    if not batches:
        return "aggregate"
    specialists = state.get("specialists") or ALL_SPECIALISTS
    return [
        Send(
            "review",
            ReviewTask(
                specialist=s,
                batch=batch,
                batch_index=i,
                batch_count=len(batches),
                overview=state.get("overview", ""),
            ),
        )
        for i, batch in enumerate(batches, 1)
        for s in specialists
    ]


async def review(task: ReviewTask, config: RunnableConfig) -> dict:
    specialist = task["specialist"]
    code = "\n\n".join(with_line_numbers(f) for f in task["batch"])
    system = prompts.SPECIALISTS[specialist].format(overview=task["overview"] or "(no overview)")
    try:
        result: ReviewFindings = await _structured(ReviewFindings, system, code, config)
    except Exception as e:
        label = f"{specialist} reviewer, batch {task['batch_index']}/{task['batch_count']}"
        return {"errors": [f"{label} failed: {type(e).__name__}: {e}"]}

    paths = {f.path for f in task["batch"]}
    tagged = [
        TaggedFinding(**f.model_dump(), specialist=specialist)
        for f in result.findings
        if f.title.strip()
    ]
    for f in tagged:  # snap near-miss paths ("routes/auth.js" vs "api/routes/auth.js")
        if f.file not in paths:
            f.file = next((p for p in paths if p.endswith(f.file) or f.file.endswith(p)), f.file)
    return {"findings": tagged}


async def aggregate(state: ReviewState, config: RunnableConfig) -> dict:
    findings = sorted(
        state.get("findings", []),
        key=lambda f: (SEVERITY_ORDER[f.severity], f.file, f.line or 0),
    )
    for i, f in enumerate(findings, 1):
        f.id = i

    if not findings:
        reviewed = bool(state.get("selected"))
        return {
            "kept": [],
            "review": AggregatedReview(
                summary="No issues were found in the reviewed files."
                if reviewed
                else "No reviewable source files were found.",
                risk_level="low",
                keep_ids=[],
                top_priorities=[],
            ),
        }

    compact = [
        {k: v for k, v in f.model_dump().items() if k != "suggestion"} for f in findings
    ]
    human = (
        f"PROJECT:\n{state.get('overview') or '(no overview)'}\n\n"
        f"FINDINGS:\n{json.dumps(compact, indent=1)}"
    )
    try:
        result: AggregatedReview = await _structured(AggregatedReview, prompts.AGGREGATOR, human, config)
        valid = {f.id for f in findings}
        keep = [i for i in dict.fromkeys(result.keep_ids) if i in valid]
        kept = [f for f in findings if f.id in set(keep)]
    except Exception as e:
        result = AggregatedReview(
            summary=f"Lead reviewer unavailable ({type(e).__name__}); showing all raw findings.",
            risk_level=findings[0].severity,
            keep_ids=[f.id for f in findings],
            top_priorities=[f.title for f in findings[:3]],
        )
        kept = findings
    return {"review": result, "kept": kept}


def report(state: ReviewState) -> dict:
    return {"report_markdown": render_markdown(state)}


# ------------------------------------------------------------------ wiring


def _read_readme(root: Path) -> str:
    for name in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
        p = root / name
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="ignore")[:README_CHARS]
    return ""


def build_graph():
    g = StateGraph(ReviewState)
    g.add_node("load_repo", load_repo)
    g.add_node("plan", plan)
    g.add_node("review", review)
    g.add_node("aggregate", aggregate)
    g.add_node("report", report)

    g.add_edge(START, "load_repo")
    g.add_edge("load_repo", "plan")
    g.add_conditional_edges("plan", fan_out, ["review", "aggregate"])
    g.add_edge("review", "aggregate")  # waits for every parallel reviewer to finish
    g.add_edge("aggregate", "report")
    g.add_edge("report", END)
    return g.compile()


# Module-level graph for LangGraph Studio / `langgraph dev` (see langgraph.json).
graph = build_graph()
