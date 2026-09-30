"""Application settings, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    environment: str = "development"
    jwt_secret: str = "dev-only-insecure-secret-change-me-please-0123456789"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24
    database_url: str = "sqlite:///./storage/app.db"
    frontend_origin: str = "http://localhost:5173"
    frontend_dist: str = "../frontend/dist"  # production: built React app served by app.server
    cookie_name: str = "access_token"

    # OTP
    otp_expire_minutes: int = 10
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60
    otp_max_per_hour: int = 5

    # Email
    email_mode: str = "console"  # console | smtp
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""

    # History
    history_visible_days: int = 3

    # Groq / KeyPool (comma-separated strings, parsed by the properties below)
    groq_api_keys: str = ""
    groq_models: str = "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b"
    llm_default_cooldown_seconds: int = 60
    llm_max_wait_seconds: int = 20
    llm_request_timeout_seconds: int = 60
    # Groq counts prompt + max_tokens against the per-minute token limit (8000 on the free tier),
    # so a single request larger than this is rejected with 413.
    llm_max_request_tokens: int = 8000
    # Hidden reasoning tokens also count against that limit. "low" = gpt-oss low, qwen3 none; "" = model default.
    llm_reasoning_effort: str = "low"

    # TypeSafe Jev: fast typed judgments (labels, scores, yes/no). Optional; Groq is used when unset.
    typesafe_api_key: str = ""
    typesafe_model: str = "jev-1.13.0"  # pinned: thresholds are tuned against a specific version
    typesafe_base_url: str = "https://api.typesafe.ai"
    typesafe_timeout_seconds: float = 15

    # Admin
    admin_emails: str = ""

    # Resume upload + RAG
    max_upload_bytes: int = 5 * 1024 * 1024
    max_resume_chars: int = 50_000
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chroma_path: str = "./storage/chroma"

    @staticmethod
    def _split(value: str) -> list[str]:
        return [part.strip() for part in value.split(",") if part.strip()]

    @property
    def groq_key_list(self) -> list[str]:
        return self._split(self.groq_api_keys)

    @property
    def groq_model_list(self) -> list[str]:
        return self._split(self.groq_models)

    @property
    def admin_email_list(self) -> list[str]:
        return [e.lower() for e in self._split(self.admin_emails)]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def _check_production_secret(self):
        # Refuse to start in production with the default/weak secret.
        if self.is_production and (self.jwt_secret.startswith("dev-only") or len(self.jwt_secret) < 32):
            raise ValueError("JWT_SECRET must be set to a strong value (32+ chars) in production")
        if self.is_production and self.email_mode != "smtp":
            raise ValueError("EMAIL_MODE must be 'smtp' in production (console mode logs OTP codes)")
        if self.email_mode not in ("console", "smtp"):
            raise ValueError("EMAIL_MODE must be 'console' or 'smtp'")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
