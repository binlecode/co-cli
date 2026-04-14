"""Pydantic output models for delegation tool agents."""

from pydantic import BaseModel, Field


class CoderOutput(BaseModel):
    """Structured output from the coder delegation agent."""

    summary: str
    diff_preview: str
    files_touched: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


class ResearchOutput(BaseModel):
    """Structured output from the research delegation agent."""

    summary: str
    sources: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


class AnalysisOutput(BaseModel):
    """Structured output from the analysis delegation agent."""

    conclusion: str
    evidence: list[str]
    reasoning: str


class ThinkingOutput(BaseModel):
    """Structured output from the reasoning delegation agent."""

    plan: str
    steps: list[str]
    conclusion: str
