from pathlib import Path

import pytest

from code_review_agent.graph import build_graph, fan_out
from code_review_agent.models import AggregatedReview, ReviewFindings
from code_review_agent.repo import SourceFile, load_source_files, make_batches, with_line_numbers
from fake_llm import FakeLLM, default_responder

FIXTURE = Path(__file__).parent / "fixtures" / "vulnerable_app"


async def run_review(llm: FakeLLM, **inputs) -> dict:
    graph = build_graph()
    state = {"target": str(FIXTURE), "max_files": 25, **inputs}
    return await graph.ainvoke(state, {"configurable": {"llm": llm}})


async def test_full_pipeline_produces_ranked_report():
    llm = FakeLLM()
    result = await run_review(llm)

    assert [f.path for f in result["selected"]] == ["db.js", "server.js"]
    assert len(result["findings"]) == 4
    assert [f.severity for f in result["kept"]] == ["critical", "high", "high", "medium"]
    assert result["review"].risk_level == "critical"
    md = result["report_markdown"]
    assert "SQL injection in register" in md
    assert "`server.js:22`" in md


async def test_each_specialist_runs_once_per_batch():
    llm = FakeLLM()
    await run_review(llm)
    reviewer_calls = [c for c in llm.calls if c[0] == "ReviewFindings"]
    assert len(reviewer_calls) == 3  # security, bugs, performance × 1 batch
    assert {"security engineer" in c[1] for c in reviewer_calls} == {True, False}


async def test_reviewers_see_numbered_code():
    llm = FakeLLM()
    await run_review(llm, specialists=["security"])
    human = next(c[2] for c in llm.calls if c[0] == "ReviewFindings")
    assert "### FILE: server.js" in human
    assert "  22 | " in human


async def test_specialist_subset():
    llm = FakeLLM()
    result = await run_review(llm, specialists=["performance"])
    assert {f.specialist for f in result["findings"]} == {"performance"}


async def test_lead_reviewer_can_drop_findings():
    def responder(schema, system, human):
        if schema is AggregatedReview:
            return AggregatedReview(summary="s", risk_level="critical", keep_ids=[1, 999], top_priorities=[])
        return default_responder(schema, system, human)

    result = await run_review(FakeLLM(responder))
    assert [f.id for f in result["kept"]] == [1]  # unknown id 999 is ignored


async def test_failed_reviewer_is_reported_not_fatal():
    def responder(schema, system, human):
        if schema is ReviewFindings and "performance engineer" in system:
            raise TimeoutError("model timed out")
        return default_responder(schema, system, human)

    result = await run_review(FakeLLM(responder))
    assert any("performance reviewer" in e for e in result["errors"])
    assert len(result["findings"]) == 3  # the other two specialists still reported
    assert "## Warnings" in result["report_markdown"]


async def test_empty_folder_skips_llm(tmp_path):
    llm = FakeLLM()
    result = await build_graph().ainvoke({"target": str(tmp_path)}, {"configurable": {"llm": llm}})
    assert llm.calls == []
    assert result["kept"] == []
    assert "No reviewable source files" in result["review"].summary


async def test_planner_failure_falls_back_to_all_files():
    def responder(schema, system, human):
        if schema.__name__ == "ReviewPlan":
            raise ValueError("bad JSON")
        return default_responder(schema, system, human)

    result = await run_review(FakeLLM(responder))
    assert len(result["selected"]) == 2


def test_fan_out_sends_one_task_per_specialist_and_batch():
    batches = [[SourceFile("a.py", "x")], [SourceFile("b.py", "y")]]
    sends = fan_out({"batches": batches, "specialists": ["security", "bugs"], "overview": ""})
    assert len(sends) == 4
    assert {(s.arg["specialist"], s.arg["batch_index"]) for s in sends} == {
        ("security", 1), ("bugs", 1), ("security", 2), ("bugs", 2)}


def test_loader_skips_dependencies_and_builds(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')")
    (tmp_path / "node_modules" / "lib").mkdir(parents=True)
    (tmp_path / "node_modules" / "lib" / "index.js").write_text("x")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "bundle.js").write_text("x")
    (tmp_path / "notes.txt").write_text("not code")
    assert [f.path for f in load_source_files(tmp_path)] == ["app.py"]


def test_batches_respect_size_limit():
    files = [SourceFile(f"f{i}.py", "x" * 1000) for i in range(10)]
    batches = make_batches(files, max_chars=3000)
    assert all(sum(len(f.content) for f in b) <= 3000 for b in batches)
    assert sum(len(b) for b in batches) == 10


def test_line_numbers():
    text = with_line_numbers(SourceFile("a.py", "one\ntwo"))
    assert text == "### FILE: a.py\n   1 | one\n   2 | two"


async def test_small_project_reviews_every_file_even_if_planner_skips_some():
    def responder(schema, system, human):
        if schema.__name__ == "ReviewPlan":
            from code_review_agent.models import ReviewPlan
            return ReviewPlan(overview="o", files=["server.js"])  # planner forgot db.js
        return default_responder(schema, system, human)

    result = await run_review(FakeLLM(responder))
    assert [f.path for f in result["selected"]] == ["server.js", "db.js"]
