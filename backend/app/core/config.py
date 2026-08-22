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
    # This chain serves SHORTS and is deliberately left as-is: three daily streams and
    # 80+ published videos have an established voice on it. Long-form overrides both the
    # order and the timeout below at runtime — see llm_models_long / run_daily.py.
    llm_models: str = (
        "google/gemma-4-31b-it:free,"
        "google/gemma-4-26b-a4b-it:free,"
        "poolside/laguna-s-2.1:free,"
        "cohere/north-mini-code:free"
    )
    llm_temperature: float = 0.7
    # Wall-clock ceiling for ONE request, enforced by the client itself rather than
    # left to httpx. httpx timeouts are per-socket-operation: OpenRouter answers a
    # slow free-model request with headers immediately and then dribbles keepalive
    # bytes while the model queues, so every individual read lands well inside the
    # timeout and the call can hang for as long as the model takes. One such call
    # burned 50 minutes on 2026-08-13 and the CI job was cancelled mid-render with
    # nothing published. See LLMClient._call_model.
    llm_timeout_seconds: int = 120
    # Ceiling for one logical chat() — every model in the chain, every sweep. Without
    # it the per-request budget multiplies out (4 models x 3 sweeps x 120s = 24 min
    # for a single script judge). Past this we give up and let the caller's fallback
    # handle it, which is far cheaper than losing the whole run.
    llm_chat_budget_seconds: int = 420

    # --- Long-form overrides (applied by run_daily.py for --format long only) ---
    # Long-form asks far more of a model in one call than Shorts do (an 850-word
    # script, then a 16-entry scene-prompt JSON), and the chain order that suits
    # Shorts does not survive it. Measured on the real prompts: laguna handled both
    # jobs cleanly in ~15s, gemma-4-26b truncated large JSON into a parse error,
    # north-mini-code needed ~206s, and gemma-4-31b returned 429 on 6/6 calls.
    # Shorts keep the chain above; only long-form runs see this order.
    llm_models_long: str = (
        "poolside/laguna-s-2.1:free,"
        "google/gemma-4-26b-a4b-it:free,"
        "cohere/north-mini-code:free,"
        "google/gemma-4-31b-it:free"
    )
    # 120s is fine for Shorts but strands the slower models on long-form's bigger
    # responses, turning a working (if slow) model into a needless failover.
    llm_timeout_seconds_long: int = 300
    llm_chat_budget_seconds_long: int = 1200

    # Which LLM backend to use: "openrouter" (default, free-model chain above) or
    # "gemini" (Google's OpenAI-compatible endpoint, its own generous free tier).
    # run_daily flips this to "gemini" for long-form runs when a Gemini key is set,
    # so long videos draw on Gemini's quota and Shorts stay on OpenRouter — see
    # scripts/run_daily.py. Providers never share a request budget this way.
    llm_provider: str = "openrouter"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    # Free-tier Gemini chain, tried in order. Model ids/limits drift — verify at
    # https://ai.google.dev/gemini-api/docs/models and override via GEMINI_MODELS.
    # The previous chain (gemini-2.0-flash, -2.0-flash-lite, -1.5-flash) was ALL
    # retired: every call 404'd and long-form died at the research node before
    # writing a word. Google shuts old ids down rather than aliasing them forward,
    # so this list needs re-checking whenever long-form starts 404ing again.
    # 2.5-flash leads because it is the established free-tier workhorse; the 3.5
    # entries follow as newer fallbacks whose free-tier access is less certain.
    # Confirm what YOUR key can actually reach:
    #   curl "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY"
    gemini_models: str = (
        "gemini-2.5-flash,"
        "gemini-2.5-flash-lite,"
        "gemini-3.5-flash,"
        "gemini-3.5-flash-lite"
    )

    # --- Script quality gate ---
    # Drafts generated per video; an LLM judge then picks the strongest one.
    # 1 disables the gate (single-shot generation, previous behavior).
    # Cost is script_candidates + 1 extra LLM calls per video — cheap on free models,
    # and the script is the single biggest lever on retention/likes/shares.
    script_candidates: int = 3

    # --- Tracing (LangSmith) ---
    # Off by default: the pipeline stays zero-infrastructure unless you opt in. When on,
    # every graph node, LLM call and judge verdict is shipped to LangSmith. No extra
    # dependency — langchain-core already requires the langsmith SDK.
    # Traces carry full prompts and scripts, so treat the key as a secret.
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "yt-automation"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

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

    # How often the picture changes, and the ceiling on scenes per video. Scene
    # count is derived from the narration's own length (see thumbnail_prompt_node),
    # so these set the cut *rhythm* rather than a fixed number of images.
    #
    # Shorts ran at one image per 8s for the first 100 videos, which on a 50-second
    # script is six or seven stills — slow for a format where the competition cuts
    # every two to four. The Ken Burns move covers some of it, but eight seconds is
    # a long time on one AI still. Cost is real and worth knowing before tuning
    # this down further: images generate serially (IMAGE_CONCURRENCY=1, forced by
    # the free Pollinations endpoint) at ~35s each, so every extra scene is ~35s of
    # run time, multiplied by MAX_TOPICS_PER_RUN videos, against RUN_BUDGET_MINUTES.
    # At 5s a typical news Short asks for ten scenes instead of seven: about two
    # extra minutes per video. run_daily's budget guard degrades safely if a run
    # does overrun — it stops starting new videos rather than being cancelled
    # mid-render — so the failure mode of tuning this too low is fewer videos that
    # day, not a broken run.
    seconds_per_scene: int = 5
    max_scenes_short: int = 12
    # Long-form cuts slower on purpose, and these values are load-bearing rather
    # than aesthetic. At 8s a 6-minute script wants 40+ scenes, and that is what
    # kept long-form from ever finishing a run: the scene prompts are requested as
    # ONE JSON object, and free models either truncate it into invalid JSON at ~30
    # entries or take ~200s to emit it, while the resulting images generate
    # serially (~25 min) against the workflow timeout. ~20s per scene is still
    # faster cutting than most long-form and roughly halves both costs.
    seconds_per_scene_long: int = 20
    max_scenes_long: int = 16

    # Word-by-word karaoke captions (ASS/libass) instead of static SRT blocks.
    # Falls back to SRT automatically when the TTS provider supplies no word timings.
    animated_captions: bool = True
    # Fonts committed to the repo, so a render looks the same on a dev machine and
    # on a CI runner (which share almost no system fonts). Optional: if the folder
    # is empty the caption styles fall back to whatever the OS provides.
    caption_font_dir: str = "./assets/fonts"
    # Background-music bed mixed under the voiceover. Tracks live in
    # assets/music/<content_type>/; a type with no tracks simply gets no music.
    music_dir: str = "./assets/music"
    music_volume: float = 0.12  # fraction of full scale; keep well under the voice

    # --- YouTube ---
    youtube_client_secrets_file: str = "./secrets/youtube_client_secret.json"
    youtube_token_file: str = "./secrets/youtube_token.json"
    youtube_upload_privacy: str = "private"
    # Plain API key (no OAuth) used only to READ public video statistics for the
    # performance feedback loop. The upload token can't do this: it deliberately
    # carries only the youtube.upload scope.
    youtube_api_key: str = ""

    # Language of the title/description and of the narration. Left unset on the
    # first 110 uploads, which meant YouTube had to infer the language from the
    # audio and the metadata text — the most direct signal available for routing
    # a video to an English-speaking audience, and we were not sending it. The
    # voice is en-US (see edge_tts_voice), so declaring it costs nothing and is
    # simply true.
    youtube_default_language: str = "en-US"

    # Exact UTC wall-clock time ("HH:MM") a run's videos should go live, set per
    # workflow. When set, uploads go up PRIVATE with status.publishAt and YouTube
    # flips them public itself at that instant.
    #
    # This exists because the publish time was really "whenever the GitHub runner
    # got round to it". Measured over 2026-08-05..19, the crons drifted +18 min
    # (humor), +47 min (news) and +2h39m (heartbreak) — so the slot a cron claims
    # to target and the slot a video actually lands in were hours apart, and no
    # timing change could be measured because the noise dwarfed the signal.
    # Empty string = publish immediately on upload (previous behavior).
    youtube_publish_slot: str = ""
    # Minutes between videos within one run. Two Shorts published in the same
    # second compete with each other for the same feed impressions.
    youtube_publish_stagger_minutes: int = 15
    # If the run finishes so late that the slot is further away than this, give up
    # on scheduling and publish immediately. Without a ceiling, a run that overran
    # its slot by a minute would hold a same-day news Short for another 23 hours.
    youtube_publish_max_lead_hours: int = 6

    # Video category per content type. News is genuinely Science & Technology (28);
    # it had been going up as People & Blogs (22) along with everything else, which
    # tells YouTube nothing about what the video is. The themed streams stay on 22 —
    # developer comedy and coding-metaphor poetry really are People & Blogs.
    youtube_category_ids: str = "news:28,dev_humor:22,code_heartbreak:22"

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
    # Wall-clock budget for one scripts/run_daily.py run, kept under the CI job's
    # timeout-minutes. A job that GitHub cancels publishes nothing and uploads no
    # artifact; stopping ourselves first means whatever already rendered still gets
    # uploaded and the run reports honestly.
    run_budget_minutes: int = 50

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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def gemini_model_chain(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]

    @property
    def category_map(self) -> dict[str, str]:
        """{content_type: categoryId} parsed from youtube_category_ids."""
        out: dict[str, str] = {}
        for pair in self.youtube_category_ids.split(","):
            name, _, value = pair.partition(":")
            if name.strip() and value.strip().isdigit():
                out[name.strip()] = value.strip()
        return out

    def category_for(self, content_type: str) -> str:
        """categoryId for a stream, falling back to People & Blogs."""
        return self.category_map.get(content_type, "22")

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
