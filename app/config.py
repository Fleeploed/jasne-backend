import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        env_parse_none_str="",
    )

    # =====================================================================
    # OpenAI — model selection per call type
    # =====================================================================
    openai_api_key: str

    # Vision/classifier (call 1): MUST be full GPT-4o, never mini.
    # GPT-4o mini tokenizes images at ~2833 tokens/tile vs 85 for gpt-4o,
    # which makes it ~33× more expensive per image and worse on Czech
    # diacritics. Skill explicitly forbids mini for vision.
    openai_classifier_model: str = "gpt-4o"

    # Default analyzer (call 2) for routine categories
    # (smlouvy, ucty, socialni, zdravotni, urad_prace, skolstvi, ostatni).
    openai_default_analyzer_model: str = "gpt-4o-mini"

    # Escalation analyzer for high-stakes categories
    # (danove, cizinecka, soudni). Skill targets "gpt-5.4-mini" — using
    # gpt-4o until that model id is publicly available.
    openai_escalation_analyzer_model: str = "gpt-4o"

    # Chat questions about a scan (Pro feature). Cheap model — full history
    # is replayed every turn so cost grows with conversation depth.
    openai_chat_model: str = "gpt-4o-mini"

    # Embeddings for RAG.
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dims: int = 1536

    # Classifier must return full OCR text for every page. The shared 2k
    # output cap is too small for normal 3+ page documents and can truncate
    # JSON, which then looks like a false "unreadable photo" error.
    openai_classifier_max_output_tokens: int = 8000

    # Shared OpenAI client tuning.
    openai_timeout_seconds: float = 60.0
    openai_max_retries: int = 3
    openai_max_output_tokens: int = 2000

    # The analyzer emits a JSON object with ~10 fields plus several arrays
    # (deadlines, actions_required, documents_needed). A dense 10-page document
    # can blow past the shared 2k cap mid-object, which truncates the JSON and
    # surfaces as a bogus "LLM returned invalid JSON" 500. Give it more room.
    openai_analyzer_max_output_tokens: int = 4000

    # =====================================================================
    # Azure Document Intelligence — OCR (prebuilt-read)
    # =====================================================================
    # Replaces GPT-4o Vision for OCR. Both endpoint and key are required;
    # app fails to start if missing. Endpoint format:
    # https://<resource>.cognitiveservices.azure.com/
    azure_document_intelligence_endpoint: str
    azure_document_intelligence_key: str
    # Azure DI per-call timeout. Multi-page docs can take a while; the
    # prebuilt-read poller normally returns within ~10s but we leave room.
    azure_document_intelligence_timeout_seconds: float = 60.0

    # =====================================================================
    # Supabase
    # =====================================================================
    supabase_url: str
    supabase_service_key: str
    supabase_jwt_secret: str
    supabase_storage_bucket: str = "documents"
    supabase_signed_url_ttl: int = 300  # seconds — Vision must fetch within this

    # =====================================================================
    # RevenueCat
    # =====================================================================
    # Value of the Authorization header configured in RevenueCat Webhooks.
    # Example: "Bearer rc_webhook_...". If unset, webhooks are rejected.
    revenuecat_webhook_auth: str | None = None
    # RevenueCat REST API key used to fetch canonical subscriber status.
    revenuecat_api_key: str | None = None
    revenuecat_entitlement_id: str = "pro"
    revenuecat_api_base_url: str = "https://api.revenuecat.com/v1"

    # =====================================================================
    # Push notifications
    # =====================================================================
    # Shared secret required by Railway/GitHub cron when calling internal
    # notification jobs. In local/test environments an unset value allows
    # manual calls; production must configure it.
    internal_cron_secret: str | None = None
    expo_push_api_url: str = "https://exp.host/--/api/v2/push/send"

    # =====================================================================
    # OAMP status tracking
    # =====================================================================
    oamp_base_url: str = "https://ipc.gov.cz"
    oamp_status_path: str = "/informace-o-stavu-rizeni/"
    oamp_status_path_en: str = "/en/status-of-your-application/"
    oamp_timeout_seconds: float = 60.0
    oamp_user_agents: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        ]
    )

    # =====================================================================
    # RAG
    # =====================================================================
    rag_top_k: int = 5
    rag_similarity_threshold: float = 0.50
    embedding_max_chars: int = 20000  # text-embedding-3 accepts ~8k tokens (~32k chars)

    # =====================================================================
    # Quality gates
    # =====================================================================
    # Below this OCR confidence we skip call 2 entirely and return early
    # — the photo is unreadable, no point burning the analyzer call.
    min_ocr_confidence: float = 0.3

    # =====================================================================
    # App
    # =====================================================================
    environment: str = "development"
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    log_level: str = "INFO"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v: object) -> list[str]:
        if v is None:
            return ["*"]
        if isinstance(v, str):
            if not v.strip():
                return ["*"]
            if v.strip().startswith("["):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed if str(x).strip()]
                except (json.JSONDecodeError, ValueError):
                    pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v  # type: ignore[return-value]

    @field_validator("oamp_user_agents", mode="before")
    @classmethod
    def parse_oamp_user_agents(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            if not v.strip():
                return []
            if v.strip().startswith("["):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed if str(x).strip()]
                except (json.JSONDecodeError, ValueError):
                    pass
            return [item.strip() for item in v.split(",") if item.strip()]
        return v  # type: ignore[return-value]

    @field_validator("allowed_origins")
    @classmethod
    def reject_wildcard_origins_in_production(cls, v: list[str], info) -> list[str]:
        environment = (info.data.get("environment") or "").lower()
        if environment == "production" and "*" in v:
            raise ValueError("ALLOWED_ORIGINS must be explicit in production")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
