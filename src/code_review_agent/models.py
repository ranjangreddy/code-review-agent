"""Structured outputs the agents must return.

Every LLM call goes through ``with_structured_output(<one of these>)``, so each agent's answer is
validated by Pydantic instead of being parsed out of free text. That works the same way on every
LangChain chat model that supports tool calling or JSON mode.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low"]
Specialist = Literal["security", "bugs", "performance"]

SEVERITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class ReviewPlan(BaseModel):
    """Planner output: what the project is and which files deserve a review."""

    overview: str = Field(description="2-4 sentences: what this project does and its tech stack.")
    files: list[str] = Field(
        description="Paths (exactly as listed) of the files most worth reviewing, most important first."
    )


class Finding(BaseModel):
    """One issue raised by a specialist reviewer."""

    file: str = Field(description="Path of the file, exactly as shown in the code header.")
    line: Optional[int] = Field(default=None, description="Line number from the numbered code, if known.")
    severity: Severity
    title: str = Field(description="Short headline, under 12 words.")
    explanation: str = Field(description="What is wrong and what could happen because of it.")
    suggestion: str = Field(description="Concrete fix, ideally with a short code snippet.")


class ReviewFindings(BaseModel):
    """Specialist output."""

    findings: list[Finding] = Field(default_factory=list)


class TaggedFinding(Finding):
    """A finding plus bookkeeping added by our code (never produced by the LLM)."""

    id: int = 0
    specialist: Specialist = "bugs"


class AggregatedReview(BaseModel):
    """Lead reviewer output: dedupe, drop false positives, summarize."""

    summary: str = Field(description="3-5 sentence executive summary of the codebase's health.")
    risk_level: Literal["critical", "high", "medium", "low"] = Field(
        description="Overall risk if this code shipped as-is."
    )
    keep_ids: list[int] = Field(
        description="IDs of findings to keep: real issues, one per duplicate group."
    )
    top_priorities: list[str] = Field(
        description="The 3 most important things to fix first, one short sentence each."
    )
