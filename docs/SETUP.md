# Complete Setup Guide

From zero to a self-running YouTube Shorts channel. Follow top to bottom — each part ends
with a ✅ check so you know it worked before moving on.

**What you'll have at the end:** two daily videos (AI news + dev humor) generated, quality-gated
and uploaded **private** to your channel automatically, weekly performance stats committed to
your repo, and theme selection that tunes itself to what your audience likes. Your only job:
flip good videos to Public in YouTube Studio.

**Time:** ~30 minutes. **Cost:** $0 (free tiers throughout).

---

## Part 0 — What you need

| Account | For | Cost |
|---|---|---|
| GitHub | Hosts the code and runs the pipeline on schedule | Free |
| [OpenRouter](https://openrouter.ai) | LLM for research/scripts/metadata | Free tier |
| Google account | The YouTube channel + Google Cloud APIs | Free |

On your computer (one-time, for minting the YouTube token): **Python 3.12** and **git**.
FFmpeg is only needed if you want to generate videos locally — CI installs its own.

---

## Part 1 — Get the code

Fork this repository on GitHub (or clone and push to your own **private** repo):

```bash
git clone https://github.com/<you>/<this-repo>.git
cd <this-repo>
python -m venv .venv
. .venv/Scripts/activate        # Windows PowerShell: .venv\Scripts\activate
pip install -e .
```

> ⚠️ Keep your working repo **private** if you customize it — your channel's publishing
> history and state live in `state/`.

✅ **Check:** `python -c "import app" --version` errors are fine, but `pip install -e .` finished without errors.

---

## Part 2 — OpenRouter key (the LLM)

1. Sign up at [openrouter.ai](https://openrouter.ai) → [Keys](https://openrouter.ai/keys) → **Create key**
2. Copy it somewhere safe — you'll add it to GitHub in Part 5

The default `LLM_MODELS` chain uses only `:free` models, so the key never gets charged.

✅ **Check (optional):** `cp .env.example .env`, put the key in `.env`, run `python scripts/check_llm.py`

---

## Part 3 — Google Cloud: one project, two credentials

Both YouTube credentials come from one Google Cloud project. Go to
[console.cloud.google.com](https://console.cloud.google.com), create a project (any name).

### 3a. Enable the API

**APIs & Services → Library** → search **"YouTube Data API v3"** → **Enable**.

### 3b. OAuth client (for uploading)

1. **APIs & Services → OAuth consent screen** → External → fill the 3 required fields →
   add your own Google account under **Test users**
2. **Credentials → Create credentials → OAuth client ID** → Application type **Desktop app**
3. **Download JSON** → save it as `secrets/youtube_client_secret.json` in the repo folder
   (the `secrets/` folder is gitignored — it never leaves your machine)

### 3c. API key (for reading stats — the feedback loop)

1. **Credentials → Create credentials → API key**
2. **Edit key** → API restrictions → **Restrict key** → select **YouTube Data API v3** only
3. Application restrictions: **None** (GitHub runners have changing IPs; the API restriction
   is the real protection — a leaked key could only read public YouTube data)
4. Copy the key for Part 5

> **Why two credentials?** The OAuth token is minted with ONLY the `youtube.upload` scope
> (least privilege — it can upload but can't read, delete or change anything). Reading public
> statistics needs no OAuth at all, just this API key.

✅ **Check:** you have `secrets/youtube_client_secret.json` locally + an API key copied.

### 3d. Mint the upload token (one-time, ~1 minute)

```bash
python scripts/youtube_auth.py
```

Your browser opens → sign in with the channel's Google account → Continue past the
"unverified app" warning (it's *your* app) → Allow. The script writes `secrets/youtube_token.json`.

✅ **Check:** `secrets/youtube_token.json` exists and contains `"refresh_token"`.

---

## Part 4 — Your YouTube channel

If you don't have one yet: youtube.com → profile picture → **Create a channel**.
Nothing else to configure — uploads arrive private, and the first upload activates the
channel's Shorts shelf automatically.

---

## Part 5 — GitHub secrets (connects everything)

Repo → **Settings → Secrets and variables → Actions → New repository secret**.

The first four are required for the daily Shorts. The last two unlock the weekly
long-form stream and tracing — skip them and everything else still runs:

| Secret name | Value |
|---|---|
| `OPENROUTER_API_KEY` | The key from Part 2 |
| `YOUTUBE_CLIENT_SECRET_JSON` | The **entire contents** of `secrets/youtube_client_secret.json` (open it, copy all, paste) |
| `YOUTUBE_TOKEN_JSON` | The **entire contents** of `secrets/youtube_token.json` |
| `YOUTUBE_API_KEY` | The API key from Part 3c |
| `GEMINI_API_KEY` | *Long-form only.* Free key from [aistudio.google.com](https://aistudio.google.com/apikey). The weekly long-form workflow drives Gemini; the Shorts stay on OpenRouter |
| `LANGSMITH_API_KEY` | *Optional.* Enables run tracing and per-video cost reporting. Left unset, tracing quietly no-ops |

> Paste raw JSON exactly as-is — no quotes added, nothing removed.

✅ **Check:** at least the four required secrets listed on the Actions secrets page.

---

## Part 6 — First run

Repo → **Actions** tab → enable workflows if prompted, then dry-run the pipeline for real:

1. **Daily Dev Humor Video** → **Run workflow** → set `dry_run` = `true` → Run
   - Generates a full video inside the runner but uploads nothing. Takes ~5-10 min.
   - ✅ Green run = LLM, image generation, TTS and rendering all work.
2. Run it again with `dry_run` = `false` (leave privacy = `private`)
   - ✅ A private video appears in [YouTube Studio](https://studio.youtube.com) → Content.
3. **Weekly Performance Stats** → **Run workflow**
   - ✅ Green run = your stats API key works.

From now on everything is automatic:

| Workflow | Schedule (UTC) | Does |
|---|---|---|
| Daily AI News Video | 21:00 daily | AI news short → upload private |
| Daily Dev Humor Video | 23:00 daily | Dev humor short → upload private |
| Daily Code Heartbreak Video | 00:30 daily | Sad coding-quote short → upload private |
| Weekly Long-Form Video | Wed & Sat 17:50 | Long-form video (Gemini) → upload private |
| Weekly Performance Stats | Monday 03:50 | Fetch stats → commit `state/performance_report.md` |

These UTC times are deliberately odd: they are set so that uploads land in US
prime time, and staggered so two streams never render in the same runner slot.
Retime them by editing the `cron:` line in the matching
`.github/workflows/*.yml` — the value is **always UTC**, never your local time.

---

## Part 7 — Your routine (2 minutes a day)

1. **YouTube Studio → Content**: watch the new private uploads; flip the good ones to
   **Public**. This is the human approval gate — nothing goes live without you.
2. **Mondays**: read `state/performance_report.md` in your repo — a ranked table of every
   video's views/likes/comments. The pipeline reads the same data and automatically favors
   humor themes that measurably perform.

---

## Tuning (all optional)

Set these as workflow env vars or in `.env` (full list: `backend/app/core/config.py`):

| Setting | Default | Effect |
|---|---|---|
| `SCRIPT_CANDIDATES` | `3` | Script drafts per video; LLM judge keeps the best. `1` = single-shot (faster, lower quality) |
| `MAX_TOPICS_PER_RUN` | `3` | News videos per daily run |
| `EDGE_TTS_VOICE` | `en-US-AriaNeural` | Any voice from `python scripts/list_voices.py` |
| `YOUTUBE_UPLOAD_PRIVACY` | `private` | Keep `private` — it *is* your approval gate |
| `LLM_MODELS` | free Gemma chain | Fallback chain; verify with `scripts/check_llm.py` |

Optional extras:
- **Persistent database** (Supabase free tier) for cross-run topic memory beyond the
  committed `state/` files — see [DATABASE.md](DATABASE.md)
- **Dashboard + API server mode** (approve/reject UI instead of YouTube Studio) — see
  [DEPLOYMENT.md](DEPLOYMENT.md)

---

## Troubleshooting

| Symptom | Cause → fix |
|---|---|
| Run fails at "Restore YouTube OAuth token" | A JSON secret was pasted incompletely — re-paste the entire file contents |
| Upload fails `invalid_grant` | Token expired/revoked — rerun `python scripts/youtube_auth.py`, update `YOUTUBE_TOKEN_JSON` secret |
| Upload fails `quotaExceeded` | YouTube allows ~6 uploads/day — lower counts or wait until quota resets (midnight PT) |
| Weekly stats fails "API credential check failed" | `YOUTUBE_API_KEY` wrong or YouTube Data API v3 not enabled for it |
| All LLM calls fail / 404 models | Free model IDs drift — run `python scripts/check_llm.py` and update `LLM_MODELS` |
| Video generated but images look wrong | Pollinations free endpoint hiccup — rerun; or switch `IMAGE_PROVIDER` |
| "no topics to generate" | All discovered stories already in `state/seen.json` — normal on reruns the same day |
