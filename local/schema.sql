-- The Brain, local slice schema. Rebuilt by run_import.py inside ONE transaction (atomic publication):
-- a failed import leaves the previous data in place.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS nodes (
  id            text PRIMARY KEY,                 -- canonical key (store-qualified where needed)
  kind          text NOT NULL,                    -- decision, story, fix, hop, finding, review, pr, lesson, ...
  layer         text NOT NULL,                    -- layers.yaml layer
  project       text[] NOT NULL,
  privacy       text NOT NULL,                    -- strictest of project and layer (privacy_rule)
  title         text NOT NULL,
  body          text NOT NULL DEFAULT '',
  meta          jsonb NOT NULL DEFAULT '{}',      -- kind-specific fields (stage, date, outcome, severity, ...)
  location      jsonb NOT NULL,                   -- {store, path, line, commit, ...}
  aliases       text[] NOT NULL DEFAULT '{}',
  source_commit text,                             -- commit the source was read at (null = untracked file)
  importer      text NOT NULL,
  embedding     vector(384),                      -- unused in this slice (no embeddings yet)
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  tsv           tsvector GENERATED ALWAYS AS (
                  setweight(to_tsvector('english', coalesce(id,'') || ' ' || coalesce(title,'')), 'A') ||
                  setweight(to_tsvector('english', coalesce(body,'')), 'B')) STORED
);
CREATE INDEX IF NOT EXISTS nodes_tsv ON nodes USING gin (tsv);
CREATE INDEX IF NOT EXISTS nodes_aliases ON nodes USING gin (aliases);
CREATE INDEX IF NOT EXISTS nodes_kind ON nodes (kind);

CREATE TABLE IF NOT EXISTS links (
  src            text NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  dst            text NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  type           text NOT NULL,                   -- reads src -> dst (layers.yaml link direction)
  tier           text NOT NULL CHECK (tier IN ('exact','structural','suggested')),
  evidence       text NOT NULL,                   -- where the link was read, and the text that made it
  confidence     real NOT NULL,
  confirmed_by   text,                            -- null until a person or reviewer confirms
  valid_from     date,
  valid_to       date,
  read_at_commit text,
  importer       text NOT NULL,
  PRIMARY KEY (src, dst, type)
);
CREATE INDEX IF NOT EXISTS links_dst ON links (dst);

-- References an importer could not resolve to a node (kept, counted, never guessed).
CREATE TABLE IF NOT EXISTS unresolved (
  src      text NOT NULL,
  raw_id   text NOT NULL,
  field    text NOT NULL,
  importer text NOT NULL,
  reason   text NOT NULL
);

CREATE TABLE IF NOT EXISTS import_runs (
  run_id      bigserial PRIMARY KEY,
  started_at  timestamptz NOT NULL,
  finished_at timestamptz NOT NULL,
  ops_commit  text,
  brain_commit text,
  counts      jsonb NOT NULL
);
