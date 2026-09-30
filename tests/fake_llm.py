"""A stand-in chat model so the graph can be tested without API keys or network calls."""

from typing import Callable

from code_review_agent.models import AggregatedReview, Finding, ReviewFindings, ReviewPlan


class FakeStructured:
    def __init__(self, llm: "FakeLLM", schema: type):
        self.llm, self.schema = llm, schema

    def with_retry(self, **_):
        return self

    async def ainvoke(self, messages, config=None):
        system, human = messages[0][1], messages[1][1]
        self.llm.calls.append((self.schema.__name__, system, human))
        return self.llm.responder(self.schema, system, human)


class FakeLLM:
    """Records every call and answers with ``responder(schema, system, human)``."""

    def __init__(self, responder: Callable | None = None):
        self.calls: list[tuple[str, str, str]] = []
        self.responder = responder or default_responder

    def with_structured_output(self, schema):
        return FakeStructured(self, schema)


def default_responder(schema, system: str, human: str):
    if schema is ReviewPlan:
        paths = [line.split(" (")[0] for line in human.split("FILES:\n")[1].splitlines() if " (" in line]
        return ReviewPlan(overview="An Express API backed by Postgres.", files=paths)
    if schema is ReviewFindings:
        if "security engineer" in system:
            return ReviewFindings(findings=[
                Finding(file="server.js", line=22, severity="critical", title="SQL injection in register",
                        explanation="User input is interpolated into SQL.", suggestion="Use parameters."),
                Finding(file="server.js", line=31, severity="high", title="Plain-text password comparison",
                        explanation="Passwords are stored unhashed.", suggestion="Use bcrypt."),
            ])
        if "correctness bugs" in system:
            return ReviewFindings(findings=[
                Finding(file="server.js", line=31, severity="high", title="Crash when user not found",
                        explanation="user is undefined for unknown emails.", suggestion="Check for a missing user."),
            ])
        return ReviewFindings(findings=[
            Finding(file="server.js", line=39, severity="medium", title="N+1 query loading owners",
                    explanation="One query per listing.", suggestion="Use a JOIN."),
        ])
    if schema is AggregatedReview:
        return AggregatedReview(summary="Serious security problems.", risk_level="critical",
                                keep_ids=[1, 2, 3, 4], top_priorities=["Fix SQL injection"])
    raise AssertionError(f"unexpected schema {schema}")
