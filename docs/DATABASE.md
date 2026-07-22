# Database setup

The app reads one setting: `DATABASE_URL`. Everything else (Alembic, FastAPI, Celery,
the pipeline) follows from it. Three supported setups:

| Setup | When to use | Cost |
|-------|-------------|------|
| SQLite | Local dev, tests, offline work | free, zero setup |
| Supabase / Neon | Always-on, data survives machine reinstalls | free tier |
| Local PostgreSQL | Docker Compose / self-hosted | free |

Verify any of them with:

```bash
python scripts/check_db.py
```

---

## SQLite (default for local dev)

```
DATABASE_URL=sqlite:///./yt_dev.db
```

Nothing to install. Fine for one worker; not suitable for concurrent Celery workers
(SQLite locks on write).

---

## Supabase (recommended free serverless)

### 1. Create the project
- supabase.com → New project. Save the database password you set — it is shown once.
- Any region works; a nearer region means lower latency per query.

### 2. Get the RIGHT connection string

Project Settings → **Database** → Connection string → **URI**.

Supabase offers three endpoints. **Use the Session pooler.**

| Endpoint | Port | Use it? | Why |
|---|---|---|---|
| Direct (`db.<ref>.supabase.co`) | 5432 | ❌ | **IPv6-only.** Most home ISPs and Windows setups can't route to it; you get a confusing DNS/timeout failure. |
| **Session pooler** (`...pooler.supabase.com`) | **5432** | ✅ | IPv4, behaves like a normal Postgres connection, migrations work. |
| Transaction pooler (`...pooler.supabase.com`) | 6543 | ⚠️ | Highest concurrency, but **Alembic migrations are unreliable** on it (no advisory locks). The app detects it and disables prepared statements automatically, but run migrations on the session pooler. |

### 3. Put it in `.env`

```
DATABASE_URL=postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

Two edits Supabase's copy button does **not** do for you:
1. Change `postgresql://` → **`postgresql+psycopg://`** (selects the psycopg3 driver).
2. Append **`?sslmode=require`**.

If your password contains special characters, URL-encode them (`@` → `%40`, `#` → `%23`,
`/` → `%2F`). An un-encoded `@` splits the URL in the wrong place and yields a
"could not translate host name" error.

### 4. Migrate and verify

```bash
pip install -e ".[dev]"     # installs alembic + the console script
alembic upgrade head
python scripts/check_db.py
```

> Use the `alembic` command, not `python -m alembic`. This project has a directory
> named `alembic/` (the migrations), which Python picks up ahead of the installed
> package and reports as
> `No module named alembic.__main__; 'alembic' is a package and cannot be directly executed`.

### Free tier limits worth knowing
- **500 MB** database. This app stores text + file *paths*, not video, so that's tens of
  thousands of projects. Videos stay on local disk (`MEDIA_ROOT`).
- **Projects pause after ~1 week of inactivity** and need a manual click to resume. If the
  pipeline runs daily this never triggers.
- Connection ceiling is modest — the engine is capped at a small pool for this reason
  (see `backend/app/db/session.py`).

---

## Neon

Same as Supabase but simpler — no IPv6 problem. Use the pooled connection string and add
`?sslmode=require`. If it contains `pgbouncer=true`, the app disables prepared statements
automatically.

---

## Local PostgreSQL / Docker

Leave `DATABASE_URL` empty and set the `POSTGRES_*` variables instead. Docker Compose does
this for you; the compose file sets `POSTGRES_HOST=db`.

---

## ⚠️ Do not put Redis on a serverless free tier

Celery workers hold a blocking `BRPOP` poll against the broker continuously. An **idle**
worker still issues thousands of Redis commands per day, which exhausts command-metered
free tiers (e.g. Upstash's 10k/day) while doing no work.

Run Redis locally or in Docker — it needs ~10 MB of RAM and no maintenance.
