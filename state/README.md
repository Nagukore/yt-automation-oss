# state/

The channel's committed memory — survives ephemeral CI runners. Created automatically
by the workflows on first run:

- `seen.json` — news stories already covered (dedupe)
- `seen_dev_humor.json` — humor themes used recently (rotation)
- `published.json` — ledger of every upload (feeds weekly stats collection)
- `performance.json` — measured stats per video (drives theme weighting)
- `performance_report.md` — human-readable league table, refreshed every Monday
