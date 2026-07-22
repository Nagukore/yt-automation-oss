"""Central application configuration via Pydantic Settings.

All runtime configuration is read from environment variables / `.env`.
Import the singleton `settings` everywhere; never read os.environ directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    # --- Security ---
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    first_admin_email: str = "admin@example.com"
    first_admin_password: str = "change-me"

    # --- Database ---
    # Full SQLAlchemy URL. When set it wins over the POSTGRES_* parts below, which lets
    # local dev / CI run on SQLite ("sqlite:///./yt.db") with no server to install.
    # Production (docker-compose) leaves this empty and uses PostgreSQL.
    database_url_override: str = Field(default="", alias="DATABASE_URL")

    postgres_user: str = "yt"
    postgres_password: str = "yt"
    postgres_db: str = "yt_automation"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # --- Redis / Celery ---
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM (OpenRouter) ---
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Verified live via scripts/check_llm.py. Free model ids drift — re-run that script if
    # calls start 404ing. Reasoning models (nvidia/nemotron-3-*) are deliberately excluded:
    # they emit chain-of-thought into the response body, which would get narrated verbatim.
    llm_models: str = (
        "google/gemma-4-31b-it:free,"
        "google/gemma-4-26b-a4b-it:free,"
        "tencent/hy3:free"
    )
    llm_temperature: float = 0.7
    llm_timeout_seconds: int = 120

    # --- Script quality gate ---
    # Drafts generated per video; an LLM judge then picks the strongest one.
    # 1 disables the gate (single-shot generation, previous behavior).
    # Cost is script_candidates + 1 extra LLM calls per video — cheap on free models,
    # and the script is the single biggest lever on retention/likes/shares.
    script_candidates: int = 3

    # --- Provider selection ---
    image_provider: str = "pollinations"
    tts_provider: str = "edge"  # keyless, cross-platform, ships subtitle timings
    subtitle_provider: str = "edge"  # reuse TTS word boundaries; "faster_whisper" to force ASR
    # reddit_til | wikipedia | google_trends | youtube_rss. Falls through the others
    # if the chosen one fails. reddit_til suits short educational "fact" videos best;
    # google_trends is news/celebrity heavy and needs LLM refinement.
    trend_provider: str = "reddit_til"
    trend_geo: str = "US"  # google_trends only (US, IN, GB, ...)

    # --- Media storage ---
    media_root: str = "./media_output"

    # --- Image providers ---
    openai_api_key: str = ""
    stable_diffusion_model: str = "stabilityai/stable-diffusion-xl-base-1.0"
    flux_model: str = "black-forest-labs/FLUX.1-schnell"
    image_width: int = 1024
    image_height: int = 1024
    # Parallel image requests. Measured against Pollinations: 4 workers -> 1/7 images
    # real, 2 workers -> 4/7, sequential -> 4/4. The free endpoint allows exactly one
    # concurrent request per IP, so parallelism buys nothing and costs image quality.
    # Raise this only for providers that genuinely support concurrency (local GPU: keep
    # at 1 to avoid VRAM exhaustion).
    image_concurrency: int = 1

    # --- TTS providers ---
    # edge-tts (default). Browse voices: python scripts/list_voices.py
    edge_tts_voice: str = "en-US-AriaNeural"
    edge_tts_rate: str = "+0%"  # e.g. "+10%" to speed up narration
    edge_tts_pitch: str = "+0Hz"

    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "Rachel"
    piper_voice: str = "en_US-amy-medium"
    kokoro_voice: str = "af_sky"

    # --- Whisper ---
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # --- Video ---
    shorts_width: int = 1080
    shorts_height: int = 1920
    longform_width: int = 1920
    longform_height: int = 1080
    video_fps: int = 30

    # --- YouTube ---
    youtube_client_secrets_file: str = "./secrets/youtube_client_secret.json"
    youtube_token_file: str = "./secrets/youtube_token.json"
    youtube_upload_privacy: str = "private"
    # Plain API key (no OAuth) used only to READ public video statistics for the
    # performance feedback loop. The upload token can't do this: it deliberately
    # carries only the youtube.upload scope.
    youtube_api_key: str = ""

    # --- Feedback-loop state (committed JSON files; survives ephemeral CI runners) ---
    state_dir: str = "./state"

    # --- Scheduler ---
    # Daily mode pins discovery to a fixed UTC time (best for a news channel).
    discover_daily: bool = True
    discover_at_hour: int = 6      # UTC; 06:00 UTC = 11:30 IST
    discover_at_minute: int = 0
    discover_cron_hours: int = 6   # used only when discover_daily=False
    auto_generate: bool = True
    max_topics_per_run: int = 3

    # ------------------------------------------------------------------ helpers
    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_model_chain(self) -> list[str]:
        return [m.strip() for m in self.llm_models.split(",") if m.strip()]

    @property
    def media_path(self) -> Path:
        p = Path(self.media_root)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def project_media_dir(self, project_id: int | str) -> Path:
        p = self.media_path / f"project_{project_id}"
        (p / "images").mkdir(parents=True, exist_ok=True)
        (p / "audio").mkdir(parents=True, exist_ok=True)
        (p / "video").mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
