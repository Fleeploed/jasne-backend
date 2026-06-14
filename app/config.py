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

    # Classifier (call 1): text-only GPT-4o, never mini. Mini is markedly
    # weaker at the Czech-bureaucracy category signals, and full 4o at temp 0
    # is cheap on the short classifier prompt. (Historically this also had to
    # be 4o because call 1 did Vision OCR; OCR now runs on Azure DI.)
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

    # Call 1 is text-only classification: it returns a tiny JSON object
    # ({category, doc_type, sender, confidence, error}), NOT the OCR text
    # (that comes from Azure DI now). A few hundred tokens is plenty; the old
    # 8000 cap was a leftover from the GPT-4o-Vision era when call 1 re-emitted
    # the whole document.
    openai_classifier_max_output_tokens: int = 500

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
    supabase_signed_url_ttl: int = 300  # seconds — Azure DI must fetch the signed URL within this

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
    expo_push_receipts_url: str = "https://exp.host/--/api/v2/push/getReceipts"

    # =====================================================================
    # Abuse protection
    # =====================================================================
    # Per-user cap on scan *attempts* (successful or not) per hour. Quota only
    # charges successful scans, so without this a user spamming garbage PDFs
    # generates unlimited paid Azure OCR calls. Pro's 100 scans/month is ~3/day,
    # so 20/hour is generous for honest burst usage.
    scan_attempts_per_hour: int = 20

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
    # Deliberately 0.50, not the 0.65 the original skill spec suggested:
    # migration 0004 loosened it because cosine similarity on the cs/en/ru/uk
    # OAMP corpus rarely clears 0.65 even for clearly relevant chunks. The
    # skill/project doc still says 0.65 and is stale — code is the source of
    # truth here.
    rag_similarity_threshold: float = 0.50
    embedding_max_chars: int = 20000  # text-embedding-3 accepts ~8k tokens (~32k chars)

    # =====================================================================
    # App
    # =====================================================================
    environment: str = "development"
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    log_level: str = "INFO"

    # Error tracking. When unset, Sentry is disabled (no-op). Crash/error
    # reporting only — no performance tracing, no PII.
    sentry_dsn: str | None = None

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
