"""Externally configurable main-runtime behavioral limits."""

from pydantic import BaseModel, ConfigDict, Field


class MainRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_steps: int = Field(default=20, ge=1)
    max_llm_calls: int = Field(default=20, ge=0)
    max_tool_calls: int = Field(default=40, ge=0)
    max_failures: int = Field(default=3, ge=0)
    provider_retry_limit: int = Field(default=2, ge=0)
    structured_output_repair_limit: int = Field(default=1, ge=0)


__all__ = ["MainRuntimeConfig"]
