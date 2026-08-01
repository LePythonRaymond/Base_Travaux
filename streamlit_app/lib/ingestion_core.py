"""Invoice-ingestion logic shared by the Streamlit page and the background worker.

Deliberately Streamlit-free: the `worker` container imports this in a headless
process. Anything that needs `st.*` stays in `pages/5_Ingestion_facture.py`.

The two consumers:
  * page 5  — interactive: extract now, review, commit.
  * worker  — background: claim a queued job, extract, park the lines in
              `ingestion_queue` for later review on the « À classifier » page.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path

from sqlalchemy import text

from .db import get_engine
from .schemas import ExtractedInvoice

log = logging.getLogger(__name__)

INGESTION_SOURCE = "supplier_catalog"


def invoice_dir() -> Path:
    return Path(os.environ.get("INVOICE_DIR", "/data/invoices"))


def save_pdf_bytes(name: str, raw: bytes) -> tuple[Path, str]:
    """Persist an uploaded PDF under INVOICE_DIR. Returns (path, sha256)."""
    sha = hashlib.sha256(raw).hexdigest()
    d = invoice_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{uuid.uuid4().hex}__{Path(name).name}"
    path.write_bytes(raw)
    return path, sha


def persist_extraction(
    extracted: ExtractedInvoice,
    *,
    file_path: Path,
    file_hash: str,
) -> list[int]:
    """Write one `ingestion_queue` row per extracted line. Returns the row ids.

    Non-product lines (totals, VAT, discounts, headers) are stored as
    `rejected` so they never reach a human review screen — they're kept only
    for traceability against the source document.
    """
    queue_ids: list[int] = []
    engine = get_engine()
    with engine.begin() as conn:
        for line in extracted.line_items:
            payload = json.loads(extracted.model_dump_json())
            payload["_invoice_sha256"] = file_hash
            payload["_line_index"] = len(queue_ids)
            payload["_line"] = json.loads(line.model_dump_json())
            row = conn.execute(
                text(
                    """
                    INSERT INTO ingestion_queue (
                        source, source_reference, raw_payload,
                        candidate_reference_name, candidate_family_hint,
                        candidate_packaging, candidate_unit_type,
                        candidate_supplier_hint, candidate_labor_hint,
                        candidate_cost_ht, status, review_notes
                    )
                    VALUES (
                        :source, :ref, CAST(:payload AS jsonb),
                        :name, :family, :packaging, :unit, :supplier_hint,
                        :labor_hint, :cost, :status, :notes
                    )
                    RETURNING id
                    """
                ),
                {
                    "source": INGESTION_SOURCE,
                    "ref": file_path.name,
                    "payload": json.dumps(payload, default=str, ensure_ascii=False),
                    "name": line.reference_name,
                    "family": line.family_hint,
                    "packaging": line.packaging,
                    "unit": line.unit_type_normalized,
                    "supplier_hint": extracted.supplier.name,
                    "labor_hint": line.suggested_labor_task,
                    "cost": float(line.unit_price_ht) if line.unit_price_ht is not None else None,
                    "status": "rejected" if not line.is_product_line else "pending",
                    "notes": "non-product line" if not line.is_product_line else None,
                },
            ).first()
            queue_ids.append(row[0])
    return queue_ids


def find_duplicate(sha: str) -> dict | None:
    """Has this exact PDF already been ingested? (SHA-256 over the bytes.)"""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, source_reference, created_at, status
                  FROM ingestion_queue
                 WHERE source = :src
                   AND raw_payload->>'_invoice_sha256' = :h
                 ORDER BY created_at DESC
                 LIMIT 1
                """
            ),
            {"src": INGESTION_SOURCE, "h": sha},
        ).mappings().first()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
#  Job queue — the producer side (the page) and helpers the worker shares
# ---------------------------------------------------------------------------
def enqueue_job(
    *,
    filename: str,
    file_path: Path,
    file_sha256: str,
    submitted_by: str | None = None,
    batch_label: str | None = None,
) -> int:
    """Queue a PDF for background extraction. Returns the job id."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO ingestion_jobs
                    (filename, file_path, file_sha256, submitted_by, batch_label)
                VALUES (:name, :path, :sha, :by, :batch)
                RETURNING id
                """
            ),
            {
                "name": filename,
                "path": str(file_path),
                "sha": file_sha256,
                "by": submitted_by,
                "batch": batch_label,
            },
        ).first()
        return int(row[0])


def recent_jobs(limit: int = 25) -> list[dict]:
    """Latest jobs for the progress panel."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, status, filename, batch_label, n_lines, error,
                       created_at, started_at, finished_at
                  FROM ingestion_jobs
                 ORDER BY created_at DESC
                 LIMIT :n
                """
            ),
            {"n": limit},
        ).mappings().all()
        return [dict(r) for r in rows]


def job_counts() -> dict[str, int]:
    """{queued: n, running: n, ...} — drives the 'en cours' badge."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status, count(*) AS n FROM ingestion_jobs GROUP BY status")
        ).mappings().all()
        return {r["status"]: int(r["n"]) for r in rows}
