# AI YouTube Automation System

A production-ready, **fully-free-capable** system that runs a YouTube Shorts channel end to end:
it discovers topics, writes and quality-gates scripts, generates images, voiceover, subtitles and
a finished video, uploads it — then **learns from real view/like/comment data** to make the next
video better.

> Human-in-the-loop by design: uploads default to **private** and nothing becomes visible
> without a human flipping it to Public in YouTube Studio.

---

## Content streams

| Stream | What it makes | Source of topics |
|--------|---------------|------------------|
| **AI News** (`news`) | Daily developer-focused AI news Shorts — new models, releases, what actually ships | RSS / trend feeds (Reddit TIL, Google Trends, YouTube RSS), deduped against history |
| **Dev Humor** (`dev_humor`) | Bittersweet developer comedy — programming concepts as heartbreak metaphors (*"She was my primary key, but in her query she never called me."*) | 25 curated themes (SQL, git, async, regex, HTTP codes, broken quotes…), rotation weighted by measured engagement |

---

## The generation pipeline

Every video runs through a **LangGraph** state machine. Each stage persists its output to the
database, so the dashboard shows live progress and failed runs are inspectable.

```mermaid
flowchart LR
    A[research] --> B[script<br/>best-of-3 + judge]
    B --> C[metadata<br/>title / SEO / hashtags]
    C --> D[thumbnail &<br/>scene prompts]
    D --> E[images]
    E --> F[voiceover]
    F --> G[subtitles]
    G --> H[video render]
    H --> I[upload private<br/>+ ledger entry]
```

### Script quality gate (best-of-N + LLM judge)

The script decides retention, likes and shares more than anything downstream — and single-shot
free models produce very uneven drafts. So the script stage:

1. Generates **3 candidate drafts** at varied temperatures (comedy runs hotter than news, so drafts genuinely differ)
2. An **LLM judge ranks them** against an engagement rubric — hook-in-2-seconds, concrete specifics, payoff, quotable last line — and only the winner gets rendered
3. The gate never sinks a run: judge failures fall back to the first draft, individual draft failures are tolerated

Set `SCRIPT_CANDIDATES=1` to disable (single-shot, cheapest). Cost of the gate is ~4 LLM calls
per video instead of 1 — free on OpenRouter's free tier.

### Performance feedback loop

The pipeline doesn't just publish — it measures what worked and steers itself:

```mermaid
flowchart TD
    A[daily upload] -->|append| B[state/published.json<br/>committed ledger]
    B -->|Mon 04:00 UTC| C[collect_stats.py<br/>YouTube Data API v3]
    C --> D[state/performance.json<br/>views / likes / comments / score]
    C --> E[state/performance_report.md<br/>readable league table on GitHub]
    D -->|next humor run| F[theme rotation:<br/>proven themes get up to 4x draw odds]
```

- **Score** = likes + 2 × comments + views/100 — likes/comments dominate on purpose: for Shorts,
  raw views mostly measure the feed algorithm, while reactions measure the content.
- Untested themes always keep base exploration weight, so rotation never tunnel-visions.
- All cross-run state is **committed JSON in `state/`** (not the DB) because CI runners are
  ephemeral — the repo itself is the channel's memory.

---

## Architecture

Two ways to run the same pipeline:

### 1. CI mode (zero infrastructure — how the channel actually runs)

GitHub Actions runs everything on a schedule. No server, no database to host, no cost.

| Workflow | Schedule | What it does |
|----------|----------|--------------|
| `daily-video.yml` | 06:00 UTC daily | Discover AI news → generate → upload private → record ledger |
| `daily-humor.yml` | 15:00 UTC daily | Pick humor theme (engagement-weighted) → generate → upload private → record ledger |
| `weekly-stats.yml` | Mondays 04:00 UTC | Fetch public stats for every ledger video → commit `performance.json` + report |

### 2. Server mode (dashboard + API)

```
                         ┌──────────────────────────────────────────────┐
                         │              FastAPI backend (JWT)            │
                         │  /auth /topics /projects /approval /publish   │
                         └───────────────┬──────────────────────────────┘
                                         │
        APScheduler (cron) ──► Celery ──►│──► LangGraph pipeline
        "discover every 6h"    (Redis)   │
                         ┌───────────────▼──────────────┐        ┌──────────────────┐
                         │   PostgreSQL (SQLAlchemy)     │◄──────►│  React dashboard  │
                         │   projects / assets / logs    │        │  (approve/reject) │
                         └───────────────────────────────┘        └──────────────────┘
```

Each media capability (LLM, TTS, images, subtitles, video) is a **pluggable provider** selected
by config, so you can run 100% free/local or swap in paid APIs without touching pipeline code.

---

## Free-first provider matrix

| Capability     | Free default (no key)        | Free/local alternative     | Paid (optional)      |
|----------------|------------------------------|----------------------------|----------------------|
| LLM            | OpenRouter `*:free` models   | Ollama (local)             | any OpenRouter model |
| Trends         | Reddit TIL / Google Trends   | YouTube RSS / search       | —                    |
| Images         | Pollinations.ai (no key)     | Stable Diffusion / FLUX    | OpenAI Images        |
| Voiceover      | Edge TTS (keyless)           | Piper / Kokoro (local)     | ElevenLabs           |
| Subtitles      | Edge TTS word timings        | faster-whisper (local)     | —                    |
| Video          | MoviePy + FFmpeg (local)     | —                          | —                    |
| Stats          | YouTube Data API v3 (free API key) | —                    | —                    |
| DB / Queue     | SQLite (CI) / PostgreSQL + Redis (server) | —             | —                    |

Everything in the "Free default" column runs **without paying anyone**. You only need a free
[OpenRouter](https://openrouter.ai) API key for the LLM.

---

## Setup

### CI mode (recommended)

Fork/clone, then add these **GitHub Actions secrets** (repo → Settings → Secrets and variables → Actions):

| Secret | What it is |
|--------|------------|
| `OPENROUTER_API_KEY` | Free key from [openrouter.ai](https://openrouter.ai) — powers all LLM stages |
| `YOUTUBE_CLIENT_SECRET_JSON` | Contents of the OAuth Desktop client JSON (Google Cloud, YouTube Data API v3 enabled) |
| `YOUTUBE_TOKEN_JSON` | Contents of the minted OAuth token (run the OAuth flow once locally via `/publish/authorize`) |
| `YOUTUBE_API_KEY` | Plain API key restricted to YouTube Data API v3 — **read-only public stats** for the feedback loop. Needed because the upload token deliberately carries only the `youtube.upload` scope, which cannot read statistics |

That's it — the three workflows take over. Trigger any of them manually from the Actions tab to test.

### Docker (server mode)

```bash
cp .env.example .env          # then edit: set OPENROUTER_API_KEY and JWT_SECRET_KEY
docker compose up --build
```

- API + Swagger docs: http://localhost:8000/docs
- Dashboard: http://localhost:5173
- Default admin is bootstrapped from `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` in `.env`.

### Local dev (no Docker)

Prereqs: **Python 3.12** (some ML wheels lag on 3.13/3.14), Node 20+, FFmpeg on PATH.
PostgreSQL + Redis only needed for server mode; the scripts run on SQLite.

```bash
# backend
python -m venv .venv && . .venv/Scripts/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --app-dir backend       # API
celery -A app.core.celery_app.celery worker -l info   # worker  (separate shell)
celery -A app.core.celery_app.celery beat  -l info    # scheduler (separate shell)

# frontend
cd frontend && npm install && npm run dev

# or skip the server entirely — one-shot runs:
python scripts/run_daily.py --content-type news --dry-run
python scripts/run_daily.py --content-type dev_humor --count 1
python scripts/collect_stats.py
```

---

## Project layout

```
backend/app/
  core/        config, logging, security (JWT), celery
  db/          SQLAlchemy models + session
  schemas/     Pydantic request/response models
  api/routes/  auth, topics, projects, approval, publish
  pipeline/    LangGraph graph + nodes + prompts + judge + OpenRouter client
  media/       tts/ images/ subtitles/ video/  (pluggable providers)
  services/    trend discovery, YouTube upload, performance feedback loop
  tasks/       Celery tasks
  scheduler/   APScheduler cron jobs
frontend/      React + TypeScript + Tailwind dashboard
scripts/
  run_daily.py       one-shot CI run: discover → generate → upload → ledger
  collect_stats.py   weekly stats collection + markdown report
state/                     the channel's committed memory (survives ephemeral CI)
  seen.json                news stories already covered
  seen_dev_humor.json      humor themes used recently (rotation)
  published.json           ledger of every upload (feeds stats collection)
  performance.json         measured stats per video (drives theme weighting)
  performance_report.md    human-readable league table — check it on Mondays
.github/workflows/         daily-video, daily-humor, weekly-stats
alembic/                   migrations
tests/                     pytest (19 tests, incl. quality gate + feedback loop)
```

---

## Key configuration

All via environment variables / `.env` (see `backend/app/core/config.py` for the full list):

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_MODELS` | free Gemma chain | Comma-separated OpenRouter fallback chain |
| `SCRIPT_CANDIDATES` | `3` | Drafts per video for the quality gate; `1` = single-shot |
| `MAX_TOPICS_PER_RUN` | `3` | Videos per discovery run |
| `YOUTUBE_UPLOAD_PRIVACY` | `private` | Keep `private` — it *is* the approval gate in CI mode |
| `EDGE_TTS_VOICE` | `en-US-AriaNeural` | News voice (humor workflow overrides to a warmer, slower voice) |
| `TREND_PROVIDER` | `reddit_til` | `reddit_til` \| `google_trends` \| `youtube_rss` \| `wikipedia` |
| `STATE_DIR` | `./state` | Where the committed feedback-loop JSON lives |

---

## Human approval flow

- **Server mode:** `discover → generate → PENDING_APPROVAL`. Review script/images/audio/video in
  the dashboard, then **Approve** (queued for upload) or **Reject**. Only `APPROVED` projects reach YouTube.
- **CI mode:** every upload lands **private**. You review in YouTube Studio and flip the good ones
  to Public. Only public videos accumulate stats, so unreviewed content never influences the feedback loop.

Every generated description discloses that narration and visuals are AI-generated.

---

## Legal / ToS note

You are responsible for complying with the YouTube Terms of Service, content policies, and the
terms of every model/API you enable. Keep the human approval step on. See `docs/DEPLOYMENT.md`.

---

## License

MIT — see [LICENSE](LICENSE). TTS default `edge-tts` is a GPL-3.0 dependency installed separately by users.
