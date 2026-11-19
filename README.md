# Brain

Linked memory for AI agents.

Brain reads the records a software project already keeps (decisions, stories, code-review results), gives every item
its ID, and links items that name each other. An agent can then ask "what decisions apply to STORY-104, and what did
its reviews find?" and get back only the linked items, each with the file and line it came from, instead of reading
every file.

**Status:** early prototype. It runs locally, on one machine, against a Postgres database in Docker.

## Why

Agents forget between sessions, so they re-read the same long files again and again. Managed agent memory helps, but it
stores conversations: what was said, not which decision a story depends on or which review found a bug in it. When a
decision changes, nothing tells you which stories, settings or documents it affects.

Brain keeps that link. Every item has an ID; a decision lists the stories it `affects`, a story lists what it
`depends_on`, a review finding is `about` a pull request that `delivers` a story. Questions are answered by following
those links, not by searching text.

## What it saves (measured)

On the author's own project, answering "which decisions apply to this story, and what did its reviews find?" used to
mean reading the whole decisions log and the whole story list, about 270,000 tokens. Brain answered the same question
in about 214 tokens: the story, its linked decisions and reviews, and a file:line for each. That is one measurement on
one project, not a benchmark; your numbers will depend on how large your files are and how well items name their IDs.

## What it does

- **Imports** from a project folder (read-only, at one git commit): a decisions log, structured decision records,
  a story list (`plans/stage-map.yaml`) and code-review logs.
- **Links** items by ID: `affects`, `depends_on`, `decided_by`, `replaces`, `delivers`, `found`, `about`.
  References it cannot resolve are kept in an `unresolved` list, never guessed.
- **Answers** `lookup`, `neighbours` and `impact` questions from the command line or as an MCP server, with only the
  linked items, each pointing at its source file and line.
- **Says what it does not know**: every answer ends with `coverage`, listing what was imported and what was not, so a
  short answer is not mistaken for a complete one.
- **Checks records** before they are written (`check.py`): ID formats, required fields, the right folder, privacy rules,
  no secrets or 12-digit account numbers.

## Try it with the example project

`example/` is a tiny made-up project (5 stories, a decisions log, 2 decision records, a few review logs).

You need Python 3.11+ with `PyYAML` (and `pytest` for the tests). For the database: Docker and `psycopg` 3.
For the agent server: the `mcp` package.

```sh
git clone <this repo> brain && cd brain
pip install pyyaml pytest "psycopg[binary]" mcp

python3 -m pytest -q                       # tests, no database needed
python3 check.py layers                    # the rules file is valid
python3 check.py records example/records   # the example's records follow the rules
python3 local/run_import.py --dry-run      # parse the example; counts go to local/out/import-summary.json
```

The import reads `example/` at the last commit (it uses `git show`), so it needs a git checkout of this repository.

With the database:

```sh
docker compose -f local/docker-compose.yml up -d    # Postgres + pgvector on 127.0.0.1:55444
python3 local/run_import.py                         # import (one transaction; sources are never changed)
python3 local/bridge.py neighbours STORY-104
python3 local/bridge.py lookup T-1                  # IDs or aliases
python3 local/bridge.py impact DEC-0001             # what depends on this decision
```

`neighbours STORY-104` returns (shortened):

```json
{
 "pack": "neighbours STORY-104 depth=1 direction=both",
 "centre": {"id": "STORY-104", "kind": "story", "title": "Home page", "at": "example/plans/stage-map.yaml:18"},
 "neighbours": [
  {"id": "DEC-0001", "kind": "decision", "dir": "up", "type": "affects", "tier": "exact",
   "title": "DEC-0001: How long are exported rows kept?", "at": "example/records/decisions/DEC-0001.yaml:1"},
  {"id": "STORY-102", "kind": "story", "dir": "up", "type": "depends_on", "tier": "exact",
   "title": "Project list", "at": "example/plans/stage-map.yaml:10"}
 ],
 "omitted": "0 (nothing left out)",
 "coverage": {"not_imported": "goal run lesson research release design code", "unresolved_ids": 0}
}
```

To give an agent the same tools over MCP (stdio):

```sh
claude mcp add brain -- python3 /path/to/brain/local/bridge.py serve
```

## Use it on your own project

Point Brain at your project folder (a git repository, or a folder inside one):

```sh
BRAIN_SOURCE=~/my-project BRAIN_SOURCE_STORE=my-project BRAIN_PROJECT=my-project \
  python3 local/run_import.py --dry-run
```

Then add your project to `projects:` in `layers.yaml`, and adjust the ID codes there to the IDs you already use.
All settings are listed in [local/README.md](local/README.md).

## Limits

- **Partial coverage.** Only decisions, the story list and review logs are imported; designs, code, goals and runs are not.
- **Fixed file formats.** The importers expect the formats in `example/`; other formats need a new importer.
- **Word search only.** Besides IDs and aliases, lookup matches words; a question phrased differently from the text may
  find nothing.
- **Manual imports.** Nothing updates automatically; re-run the import after the sources change.
- **Not yet compared** with other memory tools on a shared benchmark.

## Where things are

| Path | What it is |
|---|---|
| [local/](local/) | Importers, database schema, the lookup tool (`bridge.py`), the evidence writer |
| [check.py](check.py), [layers.yaml](layers.yaml) | Record rules (ID codes, links, required fields, folders, privacy) and the checker |
| [skills/](skills/) | Agent skills for writing records and plans in this format (`install-skills.sh` installs them) |
| [example/](example/) | The example project |
| [tests/](tests/) | Tests, with small synthetic fixtures |
| [METHOD.md](METHOD.md) | How measurements are run: hypothesis and pass mark fixed before any result |

## License

Apache License 2.0. See [LICENSE](LICENSE).
