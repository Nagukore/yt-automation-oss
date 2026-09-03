# Deploy on GitHub Actions (free, no credit card, no server)

> ⚠️ **Superseded:** the current step-by-step guide is [SETUP.md](SETUP.md) — it covers the
> newer dev-humor workflow, the weekly stats feedback loop and the `YOUTUBE_API_KEY` secret,
> which this page predates. Kept for its extra background detail.
>
> Two things below are out of date: the cron triggers are now **commented out** in every
> workflow (manual `workflow_dispatch` only), and the news schedule moved from 06:00 to
> 21:00 UTC. See the schedule table in [SETUP.md](SETUP.md#going-automatic).

Runs the whole pipeline on a schedule inside a GitHub runner:

```
06:00 UTC daily
  └─ discover AI news → script → images → voice → subtitles → render
     └─ upload to YouTube as PRIVATE
        └─ you review in YouTube Studio → flip to Public
```

**Your approval gate is YouTube Studio.** Nothing is ever publicly visible without you
switching it. No dashboard or always-on machine required.

**Cost:** free. 2,000 Actions minutes/month on private repos; ~10 min per video, so
2 videos/day ≈ 600 min/month.

---

## 1. Push the repo (PRIVATE)

```bash
git init
git add .
git commit -m "AI YouTube automation"
gh repo create yt-automation --private --source=. --push
```

> **The repo must be private.** It references secrets that grant upload access to your
> channel. `.gitignore` already excludes `.env`, `secrets/` and `media_output/` — verify
> with `git status` before the first push that no token file is staged.

## 2. Set up a persistent database (required)

Actions runners are wiped after every run. Without an external database the system has
no memory of which stories it already covered, and will repeat them.

Follow [DATABASE.md](DATABASE.md) to get your Supabase **session pooler** URL, then run
the migration once from your machine:

```powershell
alembic upgrade head
```

## 3. Add repository secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `OPENROUTER_API_KEY` | your OpenRouter key |
| `DATABASE_URL` | Supabase session-pooler URL (`postgresql+psycopg://...?sslmode=require`) |
| `JWT_SECRET_KEY` | any long random string (unused by the runner, but config expects it) |
| `YOUTUBE_TOKEN_JSON` | **entire contents** of `secrets/youtube_token.json` |
| `YOUTUBE_CLIENT_SECRET_JSON` | **entire contents** of `secrets/youtube_client_secret.json` |

Print them to copy:

```powershell
Get-Content secrets\youtube_token.json
Get-Content secrets\youtube_client_secret.json
```

Paste the whole JSON, including braces, as a single secret value.

## 4. Test before trusting the schedule

**Actions → Daily AI News Video → Run workflow**, with:
- `count`: `1`
- `dry_run`: `true`  ← generates but does not upload

That proves discovery, LLM, images, TTS and rendering all work in CI without touching
your channel or spending upload quota. Then re-run with `dry_run: false`.

## 5. Let it run

The cron fires daily at 06:00 UTC. Change the `cron:` line in
`.github/workflows/daily-video.yml` to move it — the value is **always UTC**:

| Target audience | Good UTC time | cron |
|---|---|---|
| India morning | 03:30 | `30 3 * * *` |
| US East morning | 13:00 | `0 13 * * *` |
| US West morning | 16:00 | `0 16 * * *` |

---

## Things that will bite you

- **GitHub disables cron after 60 days of repo inactivity.** One commit resets it.
- **Scheduled runs can be delayed** 10-30 min at peak. Harmless here.
- **Refresh token expiry:** if your Google OAuth app is still in *Testing*, the token
  dies after 7 days and every run fails with `invalid_grant`. Set the consent screen to
  **In production** (Google Cloud → Audience → Publish app).
- **YouTube quota:** ~6 uploads/day. `count: 2` is safe.
- **OpenRouter free tier:** 50 requests/day (1,000 after a one-time $10 credit). Each
  video costs ~4 requests.
- **Media is discarded** when the runner exits. The video lives on YouTube; local
  copies do not survive. Lower `count` rather than trying to persist artifacts.

## When a run fails

Actions → the failed run → logs. The workflow also uploads `logs/` as an artifact on
failure (kept 7 days). Common causes:

| Log message | Cause |
|---|---|
| `invalid_grant` | OAuth token expired — re-run `scripts/youtube_auth.py`, update the secret |
| `quotaExceeded` | daily YouTube upload quota gone; resets midnight Pacific |
| `all models failed` | OpenRouter free cap hit, or model IDs drifted — run `scripts/check_llm.py` |
| `no unused topics found` | every discovered story already covered; usually means feeds are stale |
| `image generation mostly failed` | Pollinations throttling; keep `IMAGE_CONCURRENCY=1` |
