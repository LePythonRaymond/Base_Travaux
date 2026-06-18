"""Database helpers for the Streamlit app.

Single-source plumbing for SQLAlchemy engine, transactional execute, and the
ingestion-source attribution that the `log_price_change` trigger reads.

The trigger uses `current_setting('app.ingestion_source', true)` to decide
what to write into `price_history.source`. That setting is per-transaction
when set with `SET LOCAL`. Therefore EVERY mutating call must run the SET
LOCAL and the actual statement(s) inside the same `engine.begin()` block.
That's what `execute()` does. Don't bypass it for product writes.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

import pandas as pd
import streamlit as st
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

log = logging.getLogger(__name__)

VALID_SOURCES = {
    "admin_streamlit",     # manual entry — bypasses ingestion_queue
    "supplier_catalog",    # invoice-extracted, reviewed
    "historical_devis",    # bulk import of past devis (future)
    "historical_dpgf",     # one-time pre-launch load mined from Vincent's worked DPGFs
    "dpgf_return",         # reverse-ingested S cells (future)
    "direct_update",       # fallback the trigger uses if SET LOCAL was missed
}


@st.cache_resource(show_spinner=False)
def get_engine() -> Engine:
    """Return a process-wide SQLAlchemy engine.

    Streamlit reruns the script per interaction, so we cache the engine in
    the resource cache to avoid building a new pool every rerun.
    """
    return create_engine(
        os.environ["DATABASE_URL"],
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


@contextmanager
def get_conn() -> Iterator[Connection]:
    """Yield a Connection inside a transaction. Commits on exit, rollbacks on error."""
    engine = get_engine()
    with engine.begin() as conn:
        yield conn


def fetch_df(sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    """Run a SELECT and return the result as a DataFrame."""
    engine = get_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Run a SELECT and return the first row as a dict, or None."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text(sql), params or {}).mappings().first()
        return dict(row) if row else None


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a SELECT and return all rows as a list of dicts."""
    engine = get_engine()
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def execute(
    sql: str,
    params: dict[str, Any] | None = None,
    ingestion_source: str = "admin_streamlit",
    ingestion_actor: str | None = None,
) -> None:
    """Execute a single mutating statement with ingestion attribution.

    The SET LOCAL and the statement run in one transaction so the
    `log_price_change` trigger sees the correct source.
    """
    if ingestion_source not in VALID_SOURCES:
        raise ValueError(f"Invalid ingestion_source: {ingestion_source!r}. Use one of {VALID_SOURCES}.")

    actor = ingestion_actor or os.environ.get("STREAMLIT_AUTH_USER", "system")
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL app.ingestion_source = :s"), {"s": ingestion_source})
        conn.execute(text("SET LOCAL app.ingestion_actor = :a"), {"a": actor})
        conn.execute(text(sql), params or {})


@contextmanager
def transaction(
    ingestion_source: str = "admin_streamlit",
    ingestion_actor: str | None = None,
) -> Iterator[Connection]:
    """Open a transaction with ingestion attribution baked in.

    Use this when you need to run multiple statements atomically (e.g. the
    invoice commit step writes a supplier, multiple products, and multiple
    queue rows in one go).
    """
    if ingestion_source not in VALID_SOURCES:
        raise ValueError(f"Invalid ingestion_source: {ingestion_source!r}. Use one of {VALID_SOURCES}.")

    actor = ingestion_actor or os.environ.get("STREAMLIT_AUTH_USER", "system")
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL app.ingestion_source = :s"), {"s": ingestion_source})
        conn.execute(text("SET LOCAL app.ingestion_actor = :a"), {"a": actor})
        yield conn


def get_product_flat_columns() -> list[str]:
    """Return the user-meaningful flat columns of `products`.

    Used to build a dynamic forbidden-keys list for Gemini's `attributes`
    field: anything that already has a flat column shouldn't be duplicated
    inside JSONB. We exclude technical / FK / system columns so the LLM
    only sees the columns a human reviewer would think about.
    """
    rows = fetch_all(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'products'
          AND column_name NOT IN (
              'id', 'created_at', 'updated_at', 'last_price_update',
              'is_active', 'attributes', 'notes',
              'family_id', 'supplier_id', 'labor_norm_id',
              'cost_currency', 'quality_rating'
          )
        ORDER BY ordinal_position
        """
    )
    return [r["column_name"] for r in rows]


def get_setting(key: str, default: str | None = None) -> str | None:
    """Read an app_settings value. Returns default if key absent."""
    row = fetch_one("SELECT value FROM app_settings WHERE key = :k", {"k": key})
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Upsert an app_settings value."""
    execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (:k, :v, now())
        ON CONFLICT (key) DO UPDATE
          SET value = EXCLUDED.value, updated_at = now()
        """,
        {"k": key, "v": value},
    )
