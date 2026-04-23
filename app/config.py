from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # --- OpenAI ---
    openai_api_key: str
    openai_vision_model: str = "gpt-4o-mini"
    openai_escalation_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dims: int = 1536
    openai_timeout_seconds: float = 60.0
    openai_max_retries: int = 3
    openai_max_output_tokens: int = 2000

    # --- Supabase ---
    supabase_url: str
    supabase_service_key: str
    supabase_jwt_secret: str
    supabase_storage_bucket: str = "documents"
    supabase_signed_url_ttl: int = 300  # seconds

    # --- RAG ---
    rag_top_k: int = 5
    rag_similarity_threshold: float = 0.65
    embedding_max_chars: int = 20000  # text-embedding-3 accepts ~8k tokens (~32k chars)

    # --- OCR quality gate ---
    min_ocr_confidence: float = 0.3  # below this, skip call 2 and return early

    # --- App ---
    environment: str = "development"
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])
    log_level: str = "INFO"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v  # type: ignore[return-value]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
