# state/

The channel's committed memory — survives ephemeral CI runners. Created automatically
by the workflows on first run:

- `seen.json` — news stories already covered (dedupe)
- `seen_dev_humor.json` — humor themes used recently (rotation)
- `seen_code_heartbreak.json` — heartbreak themes used recently (rotation)
- `published.json` — ledger of every upload (feeds weekly stats collection)
- `performance.json` — measured stats per video (drives theme weighting)
- `performance_report.md` — human-readable league table, refreshed every Monday

No `.json` files ship in this repo — a fresh clone starts with an empty memory
rather than inheriting somebody else's covered stories. Your first run creates
them, and the workflows commit them back to your fork automatically. That commit
is load-bearing: a CI runner is wiped between jobs, so if the memory is not in
git it does not exist, and the pipeline repeats the same stories forever. Do not
add `state/*.json` to `.gitignore` for that reason — the workflows gate on
`git status --porcelain state/`, which ignores ignored files, so the commit step
would silently stop firing. `performance_report.md` is checked in as a sample of
what the weekly job produces.
