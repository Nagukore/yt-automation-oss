# Running 24/7

Four processes. Each must be running for the system to be fully autonomous.

| # | Process | Purpose | Required for |
|---|---------|---------|--------------|
| 1 | Redis | Celery broker | everything |
| 2 | Celery **worker** | runs pipelines + uploads | generating videos |
| 3 | Celery **beat** | fires discovery on a schedule | unattended operation |
| 4 | FastAPI | dashboard + approval + OAuth | approving/publishing |

Nothing publishes without you clicking approve — beat only *generates*.

---

## Start everything (Windows / PowerShell)

```powershell
# 1. Redis (Docker). --restart unless-stopped survives reboots.
docker start yt-redis
#    first time only:
#    docker run -d --name yt-redis --restart unless-stopped -p 6379:6379 redis:7-alpine

# 2. Worker  -- NOTE: --pool=solo is REQUIRED on Windows.
.\.venv\Scripts\celery.exe -A app.core.celery_app.celery worker --pool=solo -l info

# 3. Beat (separate terminal)
.\.venv\Scripts\celery.exe -A app.core.celery_app.celery beat -l info

# 4. API (separate terminal)
.\.venv\Scripts\uvicorn.exe app.main:app --app-dir backend --port 8000
```

### Why `--pool=solo`

Celery's default `prefork` pool relies on `fork()`, which does not exist on Windows.
Without `--pool=solo` the worker starts but tasks fail with obscure errors. `solo`
runs one task at a time — fine here, since video generation is the bottleneck and
Pollinations only allows one image request at a time anyway.

---

## Verify it's actually working

```powershell
python scripts\health_check.py
```

Checks Redis, the worker, the database, the LLM chain and YouTube auth in one shot.

---

## The autonomous loop

```
beat (every DISCOVER_CRON_HOURS)
  └─> pipeline.scheduled_discovery
        ├─ pulls trending topics (Reddit TIL / Wikipedia / Google Trends RSS)
        ├─ stores them, skipping ones already used
        └─ if AUTO_GENERATE=true: creates MAX_TOPICS_PER_RUN projects
              └─> pipeline.generate_project  (~9 min each)
                    research → script → metadata → images → voice → subs → render
                    └─ status = PENDING_APPROVAL   ← STOPS HERE, waits for a human
```

You then review in the dashboard and approve. Approval queues
`pipeline.publish_project`, which uploads to YouTube.

Tuning knobs in `.env`:

```
DISCOVER_CRON_HOURS=6     # how often discovery runs
AUTO_GENERATE=true        # false = collect topics only, generate manually
MAX_TOPICS_PER_RUN=3      # projects created per discovery run
```

### Capacity reality check

- ~9 minutes per video, single-threaded → **max ~6-7 videos/hour**, but see quotas.
- **YouTube: ~6 uploads/day** (10,000 units/day, ~1,600 per upload). This is the real
  ceiling. `MAX_TOPICS_PER_RUN=3` every 6h = 12 videos/day — more than you can upload.
  Either lower it or accept a growing approval backlog.
- **OpenRouter free tier**: 50 requests/day (1,000 if you've ever bought $10 credit).
  Each video uses ~4 requests.

---

## Surviving reboots

Redis already restarts with Docker. For the rest, the simplest reliable option on
Windows is **Task Scheduler**, one task per process:

- Trigger: *At startup*
- Action: `C:\...\Youtube\.venv\Scripts\celery.exe`
  Arguments: `-A app.core.celery_app.celery worker --pool=solo -l info`
  Start in: `C:\...\Youtube`
- Check **Run whether user is logged on or not**, and **Restart on failure**

Repeat for `beat` and `uvicorn`.

> Your PC must be awake. Set Power Options → Sleep → **Never**, or the pipeline
> silently stops overnight. A cheap always-on mini PC, or an Oracle Cloud
> Always-Free VM, is the better long-term host.

---

## Health checks / troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `.delay()` → connection refused on **5672** | Celery bound to its default RabbitMQ broker | Import tasks via `app.tasks.pipeline_tasks` (it imports the configured app) |
| Connection refused on **6379** | Redis not running | `docker start yt-redis` |
| Worker starts, tasks never run | missing `--pool=solo` on Windows | add it |
| Everything queues but nothing generates | beat not running | start beat |
| `invalid_grant` after ~7 days | OAuth app still in *Testing* | Google Cloud → Audience → **Publish app** |
| Images all ~10 KB | Pollinations rate-limited → placeholders | keep `IMAGE_CONCURRENCY=1` |
| Video has no voiceover | TTS fell back to silence | check `TTS_PROVIDER=edge` |

Logs: `logs/app_YYYY-MM-DD.log` (rotated daily, kept 14 days).

---

## Cost

Everything above is free. The only optional spend is a cheap non-`:free` OpenRouter
model as the last link in `LLM_MODELS` (~$1-2/month) so generation never stalls when
the free daily cap is hit.
