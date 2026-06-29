"""Application configuration.

All settings come from environment variables (or a local .env file).
Path helpers derive the working-directory layout from DATA_DIR so the rest of
the app never hard-codes folder locations — this is what lets us swap the
local filesystem for Azure Blob storage later without touching the pipeline.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM provider ────────────────────────────────────────────
    llm_provider: str = "google"           # "google" | "azure_openai"

    # ── Google AI ───────────────────────────────────────────────
    google_api_key: str = ""
    model_extract: str = "gemini-2.5-flash"
    model_summary: str = "gemini-2.5-flash"
    model_query: str = "gemini-2.5-flash"

    # ── Azure OpenAI (used when llm_provider = "azure_openai") ──
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""        # https://my-resource.openai.azure.com/
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-02-15-preview"

    # ── Pipeline tuning ─────────────────────────────────────────
    max_llm_concurrency: int = 5
    chunk_size: int = 400
    chunk_overlap: int = 50

    # ── Storage ─────────────────────────────────────────────────
    data_dir: Path = Path("data")

    # ── Derived path helpers ────────────────────────────────────
    @property
    def extracted_text_dir(self) -> Path:
        return self.data_dir / "extracted_text"

    @property
    def chunks_dir(self) -> Path:
        return self.data_dir / "chunks"

    @property
    def graph_dir(self) -> Path:
        return self.data_dir / "graph"

    @property
    def communities_dir(self) -> Path:
        return self.graph_dir / "communities"

    @property
    def entities_file(self) -> Path:
        return self.graph_dir / "entities.json"

    @property
    def relationships_file(self) -> Path:
        return self.graph_dir / "relationships.json"

    @property
    def community_map_file(self) -> Path:
        return self.graph_dir / "community_map.json"

    @property
    def graph_stats_file(self) -> Path:
        return self.graph_dir / "graph_stats.json"

    @property
    def graph_html_file(self) -> Path:
        return self.graph_dir / "knowledge_graph.html"

    @property
    def api_key_set(self) -> bool:
        if self.llm_provider == "azure_openai":
            return bool(self.azure_openai_api_key and self.azure_openai_endpoint)
        return bool(self.google_api_key)

    @property
    def active_model_label(self) -> str:
        if self.llm_provider == "azure_openai":
            return f"azure · {self.azure_openai_deployment}"
        return self.model_extract

    def entities_count_on_disk(self) -> int:
        """Return how many entities are in entities.json (0 if file missing)."""
        import json
        if not self.entities_file.exists():
            return 0
        try:
            return len(json.loads(self.entities_file.read_text(encoding="utf-8")))
        except Exception:
            return 0

    def ensure_dirs(self) -> None:
        """Create the working-directory tree if it does not exist."""
        for d in (
            self.extracted_text_dir,
            self.chunks_dir,
            self.graph_dir,
            self.communities_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
