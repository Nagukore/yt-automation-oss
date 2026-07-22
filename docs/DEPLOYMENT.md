# Deployment & 24/7 free operation

## 1. What "free" means here

| Component | How it's free |
|-----------|---------------|
| LLM | OpenRouter free-tier models (the `:free` suffixed models). Rate-limited but $0. |
| Images | Pollinations.ai hosted endpoint — no key, no GPU. |
| Voiceover | Piper / Kokoro run locally on CPU. |
| Subtitles | faster-whisper runs locally on CPU. |
| Video | MoviePy + FFmpeg, local. |
| Infra | Postgres + Redis containers on your own hardware or a free-tier VM. |

The only account you must create is a free **OpenRouter** key.

## 2. Recommended free hosts for 24/7

- **Your own always-on box / mini-PC / Raspberry Pi 5 (8GB)** — best option. `docker compose up -d`.
- **Oracle Cloud Always-Free** — 4 ARM cores + 24GB RAM VM, genuinely free forever. Big enough
  for local TTS + Whisper + rendering. Install Docker, clone, `docker compose up -d`.
- **Google Cloud e2-micro / AWS free tier** — works but 1GB RAM is tight; keep `WHISPER_MODEL=tiny`,
  use Pollinations for images, and render short videos only.

> Free LLM tiers are rate-limited. `LLM_MODELS` is a fallback chain — if the first free model is
> throttled the client automatically tries the next. Keep 2-3 free models listed.

## 3. First run

```bash
cp .env.example .env
#   set OPENROUTER_API_KEY, JWT_SECRET_KEY, FIRST_ADMIN_PASSWORD
docker compose up -d --build
docker compose logs -f api        # watch it boot + migrate
```

Open http://localhost:5173, log in with the admin creds from `.env`.

## 4. Enabling local media engines (optional, higher quality)

The base image installs only light dependencies. To use local Piper/Kokoro/Whisper/Stable Diffusion,
edit the `Dockerfile` line:

```dockerfile
RUN pip install -e ".[media]"
```

and rebuild. For Stable Diffusion / FLUX you need an NVIDIA GPU + `nvidia-container-toolkit`; set
`IMAGE_PROVIDER=stable_diffusion` and `WHISPER_DEVICE=cuda`.

## 5. YouTube publishing setup (one-time)

1. Google Cloud Console → new project → enable **YouTube Data API v3**.
2. Create an **OAuth client ID** of type *Desktop app* (or Web app with the redirect URI
   `http://localhost:8000/api/publish/oauth/callback`). Download the JSON.
3. Save it as `./secrets/youtube_client_secret.json` (mounted into the containers).
4. Log in to the dashboard as admin, then visit `http://localhost:8000/api/publish/authorize`
   and complete Google consent. A refresh token is stored in `./secrets/youtube_token.json`.
5. Uploads default to **private** (`YOUTUBE_UPLOAD_PRIVACY`). Review on YouTube, then flip to public.

> YouTube API has a daily upload quota (~6 uploads/day on the default 10k-unit quota). Plan volume
> accordingly, or request a quota increase.

## 6. The human-approval guarantee

Nothing is ever uploaded automatically. The pipeline always stops at `PENDING_APPROVAL`. Only an
explicit **Approve** action (dashboard → Approvals, or `POST /api/approval/{id}`) queues an upload,
and only `APPROVED` projects are accepted by the uploader task. Keep it that way.

## 7. Scheduler

Discovery runs via **Celery beat** (the `beat` service) every `DISCOVER_CRON_HOURS`. If
`AUTO_GENERATE=true` it also starts the full pipeline for the top `MAX_TOPICS_PER_RUN` topics — which
still land in `PENDING_APPROVAL`. Prefer a standalone scheduler process? Run
`python -m app.scheduler.jobs` (APScheduler) instead of the `beat` service.

## 8. Scaling / operations

- Add more `worker` replicas for parallel rendering: `docker compose up -d --scale worker=3`.
- Media is written to the `media` volume; back it up or point `MEDIA_ROOT` at object storage.
- Logs: stdout (Loguru) + rotating files under `logs/`.
- Health check: `GET /health`. Swagger: `GET /docs`.

## 9. Compliance

Keep the human review step. You are responsible for copyright, disclosure of AI-generated/altered
content where required, YouTube's Terms of Service, and the terms of every model/API you enable.
