"""Application settings via pydantic-settings.

Loads configuration from .env at the project root. All LLM provider,
context window, and server settings are centralised here.

Dependents:
    Imported by every service and router module in the backend.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the project root (two levels above this file)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = str(_PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    """All application settings — sourced from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM provider ─────────────────────────────────────────────────────
    # "openai" | "agent" | "echo" | "mock"
    llm_provider: str = "echo"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1"

    # ── Azure AI Foundry Agent ───────────────────────────────────────────
    azure_ai_project_endpoint: str = ""
    azure_ai_agent_id: str = ""

    # ── Context window ───────────────────────────────────────────────────
    max_context_tokens: int = 120_000
    max_response_tokens: int = 4_096
    system_prompt: str = "You are a helpful assistant."

    # ── Server ───────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    debug: bool = False


# Singleton — import this everywhere
settings = Settings()
