-- =============================================================================
-- Background ingestion jobs — lets an invoice scan survive the browser tab
-- =============================================================================
-- Streamlit runs synchronously: navigating away (or closing the tab) kills the
-- running script, so a Gemini extraction started in the page dies with it.
--
-- With this table the page becomes a PRODUCER: it saves the PDF, inserts one
-- `queued` row, and returns immediately. A separate `worker` container is the
-- CONSUMER: it claims jobs, runs the extraction, writes the resulting lines to
-- `ingestion_queue`, and records the outcome here. The user can close the tab
-- and come back later — the work continues server-side.
--
-- Idempotent: safe to re-run.
-- =============================================================================

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id              BIGSERIAL PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'done', 'failed')),
    filename        TEXT NOT NULL,            -- display name (as uploaded)
    file_path       TEXT NOT NULL,            -- absolute path under INVOICE_DIR
    file_sha256     TEXT NOT NULL,            -- duplicate detection
    submitted_by    TEXT,                     -- STREAMLIT_AUTH_USER
    batch_label     TEXT,                     -- groups a multi-file / zip drop
    attempts        SMALLINT NOT NULL DEFAULT 0,
    n_lines         INTEGER,                  -- lines written to ingestion_queue
    queue_ids       INTEGER[],                -- the rows this job produced
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

-- The worker's claim query: oldest queued first.
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_claim
    ON ingestion_jobs(status, created_at);

-- The page's "mes traitements" panel.
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_recent
    ON ingestion_jobs(created_at DESC);
