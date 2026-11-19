# local/: importers, database and lookup tool

| File | What it does |
|---|---|
| `run_import.py` | Runs all importers and publishes to the database in one transaction (`--dry-run`: parse only) |
| `import_decisions.py` | The decisions log (one item per entry) and structured records under `records/` |
| `import_stagemap.py` | The story list `plans/stage-map.yaml` (depends_on, decisions, closes_hops become links) |
| `import_reviews.py` | Review logs `logs/rv*.log` and merge reviews `logs/merge-review-*.md` (findings and their outcome) |
| `brainlib.py` | Shared helpers: settings, read-only git access, redaction, privacy, the in-memory graph |
| `db.py`, `schema.sql` | Database access and schema (nodes, links, unresolved references, import runs) |
| `bridge.py` | `lookup`, `neighbours`, `impact` on the command line, or `serve` as an MCP server over stdio |
| `evidence.py` | Writes measurement runs as immutable, hash-chained folders (see METHOD.md section 12) |
| `docker-compose.yml` | Postgres 17 + pgvector, image pinned by digest, bound to 127.0.0.1:55444 |

## Rules the importers follow

- Sources are never written. Tracked files are read at one commit (`git show`), so every item's location (file, line,
  commit) can be checked later. Untracked logs are identified by their file hash.
- Old IDs stay as aliases. A reference that matches no imported item is kept in `unresolved`, never guessed.
- Private (claude-only) material is skipped and only counted: IDs whose layer or store is claude-only, and anything
  naming a path in `lab_paths` (layers.yaml).
- 12-digit numbers and secret-looking values are removed before anything is stored.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `BRAIN_SOURCE` | `example/` in this repo | The project folder to import (a git repository or a folder inside one) |
| `BRAIN_SOURCE_STORE` | `example` | Store name written into every item's location |
| `BRAIN_PROJECT` | `example` | Project for items that do not name one (must be in `layers.yaml` `projects`) |
| `BRAIN_DECISIONS_LOG` | `DECISIONS.md` | Decisions log, relative to the source folder |
| `BRAIN_STAGE_MAP` | `plans/stage-map.yaml` | Story list, relative to the source folder |
| `BRAIN_GITHUB_ORG` | `example-org` | Organisation used for pull request links |
| `BRAIN_DSN` | `postgresql://brain:<password>@127.0.0.1:55444/brain` | Database connection |
| `BRAIN_DB_PASSWORD` | `brain-local` | Local database password (set it before the first `docker compose up`) |
| `BRAIN_FOLD` | unset | Folder with an optional `fold.py` (`pack`/`unfold`) for a more compact answer format; plain JSON otherwise |
| `BRAIN_PACK_LOG` | unset | File to append one JSON line per answer (sha256, size, exact-decode result) |

## Database

```sh
docker compose -f local/docker-compose.yml up -d
docker compose -f local/docker-compose.yml ps        # wait for "healthy"
python3 local/run_import.py
```

The data lives in a Docker volume, never in git. Graph walks are plain recursive SQL (`bridge.py`, `WALK`).
To stop: `docker compose -f local/docker-compose.yml down` (add `-v` to delete the data; a re-import rebuilds it).

## The lookup tool

```sh
python3 local/bridge.py lookup STORY-104                    # ID or alias; several matches are listed, not guessed
python3 local/bridge.py neighbours STORY-104 --depth 2 --direction upstream --limit 60
python3 local/bridge.py impact DEC-0001                     # what depends on it: downstream only, depth 2
python3 local/bridge.py serve                               # MCP server (tools: lookup, neighbours, impact)
```

Claude-only items are never returned. Every answer carries `coverage` (what was imported and what was not) and an
`omitted` line (how much was left out by the limit).
