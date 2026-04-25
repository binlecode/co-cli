"""Web fetch domain policy and HTTP retry settings."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_WEB_HTTP_MAX_RETRIES = 2
DEFAULT_WEB_HTTP_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_WEB_HTTP_BACKOFF_MAX_SECONDS = 8.0
DEFAULT_WEB_HTTP_JITTER_RATIO = 0.2


class WebSettings(BaseModel):
    """Web fetch domain policy and HTTP retry settings."""

    model_config = ConfigDict(extra="forbid")

    fetch_allowed_domains: list[str] = Field(default=[])
    fetch_blocked_domains: list[str] = Field(default=[])
    http_max_retries: int = Field(default=DEFAULT_WEB_HTTP_MAX_RETRIES, ge=0, le=10)
    http_backoff_base_seconds: float = Field(
        default=DEFAULT_WEB_HTTP_BACKOFF_BASE_SECONDS, ge=0.0, le=30.0
    )
    http_backoff_max_seconds: float = Field(
        default=DEFAULT_WEB_HTTP_BACKOFF_MAX_SECONDS, ge=0.5, le=120.0
    )
    http_jitter_ratio: float = Field(default=DEFAULT_WEB_HTTP_JITTER_RATIO, ge=0.0, le=1.0)

    @field_validator("fetch_allowed_domains", "fetch_blocked_domains", mode="before")
    @classmethod
    def _parse_web_domains(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [s.strip().lower() for s in v.split(",") if s.strip()]
        return [s.lower() for s in v]

    @model_validator(mode="after")
    def _validate_web_retry_bounds(self) -> "WebSettings":
        if self.http_backoff_base_seconds > self.http_backoff_max_seconds:
            raise ValueError(
                "web.http_backoff_base_seconds must be <= web.http_backoff_max_seconds"
            )
        return self
