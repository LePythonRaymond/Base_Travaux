"""Background invoice-ingestion worker.

Runs as its own container (same image as the Streamlit app, different command).
Streamlit executes scripts synchronously, so an extraction started in the page
dies the moment the user navigates away or closes the tab. This process is the
consumer side of `ingestion_jobs`: it claims queued PDFs, runs the Gemini
extraction, and parks the resulting lines in `ingestion_queue` for review on the
« À classifier » page.

    python -m worker            (or: python worker.py)

Design notes
------------
* Claiming uses `FOR UPDATE SKIP LOCKED`, so running several replicas is safe.
* One job = one transaction boundary for the claim, then the extraction runs
  OUTSIDE a transaction (it's slow), then the outcome is written.
* A crash mid-extraction leaves the row in `running`; `_requeue_stale()`
  releases anything stuck beyond STALE_MINUTES on startup and every loop, so a
  container restart self-heals instead of losing the job.
* Failures are retried up to MAX_ATTEMPTS, then marked `failed` with the error
  text (surfaced in the page's progress panel).
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

from sqlalchemy import text

# The image sets WORKDIR=/app, so `lib` is importable as a top-level package.
from lib.db import get_engine
from lib.gemini import extract_invoice
from lib.ingestion_core import persist_extraction

logging.basicConfig(
    level=os.environ.get("WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s worker %(message)s",
)
log = logging.getLogger("worker")

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "5"))
MAX_ATTEMPTS = int(os.environ.get("WORKER_MAX_ATTEMPTS", "3"))
STALE_MINUTES = int(os.environ.get("WORKER_STALE_MINUTES", "30"))
PACE_SECONDS = float(os.environ.get("WORKER_PACE_SECONDS", "2"))

_STOP = False


def _handle_stop(signum, _frame):  # pragma: no cover - signal path
    global _STOP
    log.info("signal %s received — finishing current job then exiting", signum)
    _STOP = True


def _claim_job() -> dict | None:
    """Atomically take the oldest queued job. None when the queue is empty."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                WITH nxt AS (
                    SELECT id FROM ingestion_jobs
                     WHERE status = 'queued'
                     ORDER BY created_at
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                )
                UPDATE ingestion_jobs j
                   SET status = 'running',
                       started_at = now(),
                       attempts = j.attempts + 1
                  FROM nxt
                 WHERE j.id = nxt.id
             RETURNING j.id, j.filename, j.file_path, j.file_sha256, j.attempts
                """
            )
        ).mappings().first()
        return dict(row) if row else None


def _requeue_stale() -> int:
    """Release jobs stuck in `running` (worker crashed / container restarted)."""
    engine = get_engine()
    with engine.begin() as conn:
        res = conn.execute(
            text(
                """
                UPDATE ingestion_jobs
                   SET status = CASE WHEN attempts >= :maxa THEN 'failed' ELSE 'queued' END,
                       error  = CASE WHEN attempts >= :maxa
                                     THEN 'Abandonné après ' || attempts || ' tentatives (worker interrompu).'
                                     ELSE error END,
                       finished_at = CASE WHEN attempts >= :maxa THEN now() ELSE finished_at END
                 WHERE status = 'running'
                   AND started_at < now() - make_interval(mins => :mins)
                """
            ),
            {"maxa": MAX_ATTEMPTS, "mins": STALE_MINUTES},
        )
        return res.rowcount or 0


def _finish(job_id: int, *, n_lines: int, queue_ids: list[int]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ingestion_jobs SET status='done', finished_at=now(), "
                "n_lines=:n, queue_ids=:ids, error=NULL WHERE id=:id"
            ),
            {"n": n_lines, "ids": queue_ids, "id": job_id},
        )


def _fail(job_id: int, attempts: int, exc: Exception) -> None:
    """Retry while attempts remain, otherwise mark failed with the reason."""
    give_up = attempts >= MAX_ATTEMPTS
    msg = f"{type(exc).__name__}: {exc}"[:900]
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ingestion_jobs SET status=:st, error=:err, "
                "finished_at = CASE WHEN :st = 'failed' THEN now() ELSE NULL END "
                "WHERE id=:id"
            ),
            {"st": "failed" if give_up else "queued", "err": msg, "id": job_id},
        )
    log.warning("job %s %s — %s", job_id, "FAILED" if give_up else "requeued", msg)


def _process(job: dict) -> None:
    job_id = int(job["id"])
    path = Path(job["file_path"])
    log.info("job %s — %s (tentative %s)", job_id, job["filename"], job["attempts"])
    if not path.exists():
        raise FileNotFoundError(f"PDF introuvable : {path}")

    raw = path.read_bytes()
    # The prompt adapts to the live catalogue, exactly like the interactive page.
    engine = get_engine()
    with engine.connect() as conn:
        labor = [r[0] for r in conn.execute(
            text("SELECT task_name FROM labor_norms ORDER BY task_name")).all()]
        fams = [r[0] for r in conn.execute(
            text("SELECT name FROM product_families ORDER BY name")).all()]

    extracted = extract_invoice(raw, labor_norm_names=labor, family_names=fams)
    queue_ids = persist_extraction(
        extracted, file_path=path, file_hash=job["file_sha256"]
    )
    _finish(job_id, n_lines=len(queue_ids), queue_ids=queue_ids)
    log.info("job %s — OK, %s ligne(s) en file d'attente", job_id, len(queue_ids))


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    if not os.environ.get("DATABASE_URL"):
        log.error("DATABASE_URL manquant — worker arrêté.")
        return 2

    log.info(
        "worker démarré (poll=%ss, max_attempts=%s, stale=%smin)",
        POLL_SECONDS, MAX_ATTEMPTS, STALE_MINUTES,
    )
    # Wait for Postgres to accept connections (compose ordering is best-effort).
    for attempt in range(30):
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            break
        except Exception as exc:  # noqa: BLE001
            log.info("attente de la base (%s/30): %s", attempt + 1, exc)
            time.sleep(2)
    else:
        log.error("base injoignable — worker arrêté.")
        return 3

    while not _STOP:
        try:
            released = _requeue_stale()
            if released:
                log.warning("%s job(s) bloqué(s) libéré(s)", released)
            job = _claim_job()
            if job is None:
                time.sleep(POLL_SECONDS)
                continue
            try:
                _process(job)
            except Exception as exc:  # noqa: BLE001 — one bad PDF must not stop the loop
                _fail(int(job["id"]), int(job["attempts"]), exc)
            # Pace Gemini calls: no rate limiting exists upstream.
            time.sleep(PACE_SECONDS)
        except Exception as exc:  # noqa: BLE001 — DB blip, keep the loop alive
            log.exception("boucle worker : %s", exc)
            time.sleep(POLL_SECONDS)

    log.info("worker arrêté proprement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
