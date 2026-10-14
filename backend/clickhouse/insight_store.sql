-- Insight Store（PostgreSQL + pgvector）
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS insights (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('funnel','anomaly','risk','campaign','opportunity','answer')),
  title         TEXT NOT NULL,
  summary       TEXT NOT NULL,
  hypothesis    TEXT,
  evidence      JSONB NOT NULL DEFAULT '[]',     -- [{tool,args,digest,sql_hash}]
  chart_spec    JSONB,
  scope         JSONB,                           -- {segment, dimension:{}}
  confidence    NUMERIC(3,2),
  severity      TEXT CHECK (severity IN ('low','medium','high','critical')),
  actions       JSONB NOT NULL DEFAULT '[]',
  created_by    JSONB NOT NULL,                  -- {agent, run_id, on_behalf_of} | {user}
  status        TEXT NOT NULL DEFAULT 'open',
  embedding     VECTOR(1024),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS insights_embedding_idx ON insights USING ivfflat (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS insight_feedback (
  id          BIGSERIAL PRIMARY KEY,
  insight_id  TEXT REFERENCES insights(id) ON DELETE CASCADE,
  user_id     TEXT NOT NULL,
  vote        TEXT CHECK (vote IN ('useful','not_useful','wrong')),
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS insight_subscriptions (
  id          BIGSERIAL PRIMARY KEY,
  user_id     TEXT NOT NULL,
  filter      JSONB NOT NULL,                    -- {kinds:[], min_severity, scope}
  channel     TEXT NOT NULL,                     -- email | line | webhook | in_app
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id            TEXT PRIMARY KEY,
  agent         TEXT NOT NULL,
  trigger       JSONB NOT NULL,                  -- {type: schedule|question|event, ...}
  on_behalf_of  TEXT NOT NULL,
  plan          JSONB,
  tool_calls    JSONB NOT NULL DEFAULT '[]',
  cost          JSONB,                           -- {scan_bytes, llm_tokens, ms}
  outcome       TEXT,
  insight_ids   TEXT[] NOT NULL DEFAULT '{}',
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS knowledge_docs (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,                     -- event_dict | metric_doc | glossary | sop | insight
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  embedding   VECTOR(1024),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
