"""Environment-based configuration. No secrets are hardcoded; dev defaults are rejected in production."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self.env = os.getenv("APP_ENV", "development")
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./aegis_demo.db")
        self.jwt_secret = os.getenv("JWT_SECRET", "")
        self.jwt_algorithm = "HS256"
        self.token_minutes = int(os.getenv("ACCESS_TOKEN_MINUTES", "120"))
        self.upload_dir = Path(os.getenv("UPLOAD_DIR", "./uploads"))
        self.max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
        self.cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
        self.auto_create_schema = _bool("AUTO_CREATE_SCHEMA", True)
        self.allow_registration = _bool("ALLOW_REGISTRATION", True)
        self.rate_limit_per_minute = int(os.getenv("RATE_LIMIT_PER_MINUTE", "240"))
        self.auth_rate_limit_per_minute = int(os.getenv("AUTH_RATE_LIMIT_PER_MINUTE", "10"))
        # AI configuration. Providers are chosen by operators via env only (never by request input -> no SSRF).
        self.ai_provider = os.getenv("AI_PROVIDER", "none")  # none | openai_compatible | anthropic
        self.ai_base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
        self.ai_api_key = os.getenv("AI_API_KEY", "")
        self.ai_model = os.getenv("AI_MODEL", "")
        self.embedding_provider = os.getenv("EMBEDDING_PROVIDER", "local")  # local | openai_compatible
        self.embedding_base_url = os.getenv("EMBEDDING_BASE_URL", self.ai_base_url)
        self.embedding_api_key = os.getenv("EMBEDDING_API_KEY", self.ai_api_key)
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

        if not self.jwt_secret:
            if self.env == "production":
                raise RuntimeError("JWT_SECRET must be set in production")
            # Ephemeral per-process secret: tokens are invalidated on restart, nothing insecure is committed.
            self.jwt_secret = secrets.token_urlsafe(48)
        if self.env == "production" and len(self.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET must be at least 32 characters in production")
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    @property
    def ai_enabled(self) -> bool:
        return self.ai_provider != "none" and bool(self.ai_api_key) and bool(self.ai_model)


settings = Settings()
