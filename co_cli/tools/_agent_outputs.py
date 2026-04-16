"""Pydantic output models for delegation tool agents."""

from pydantic import BaseModel


class AgentOutput(BaseModel):
    """Output from a delegation agent."""

    result: str
