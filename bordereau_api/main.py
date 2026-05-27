"""Merci Raymond Bordereau API.

Single-purpose FastAPI app: serves the live bordereau (reference price list)
as CSV to Vincent's Excel via Power Query. Authenticated by a shared API key
in the X-API-Key header (NOT proxy-level basicauth — Power Query needs a
clean response).

Endpoints:
    GET /api/health          — open, returns {"ok": true}
    GET /api/bordereau.csv   — requires X-API-Key, returns CSV of products_with_averages
    GET /api/taxonomy.csv    — requires X-API-Key, returns CSV of product_taxonomy
                               (Famille / Sous-catégorie / Conditionnement triplets)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response
from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bordereau_api")

DATABASE_URL = os.environ["DATABASE_URL"]
EXPECTED_KEY = os.environ["BORDEREAU_API_KEY"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
app = FastAPI(title="Merci Raymond Bordereau API")


def _csv_value(v: Any) -> Any:
    """Serialize a row value for CSV output.

    JSONB columns surface as Python dicts (psycopg2's default JSON adapter).
    The default csv.DictWriter calls str() on them, producing Python repr
    with single quotes — not valid JSON, breaks any downstream parser
    (Google Sheets, jq, JSON.parse). Force valid JSON with ensure_ascii=False
    so French content keeps its accents.
    """
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/bordereau.csv")
def bordereau(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    key: str | None = None,
) -> Response:
    # Accept the key either via the X-API-Key header (preferred — keeps it
    # out of access logs) OR via a `?key=` query string. The latter exists
    # so Google Sheets `IMPORTDATA()` can authenticate when Apps Script is
    # locked down in a Workspace tenant. Treat the URL form as more visible:
    # rotate the key if it leaks via shared sheet URLs.
    provided = x_api_key or key
    if provided != EXPECTED_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    with engine.connect() as conn:
        # `products_with_averages` = real products + synthetic catalogue
        # averages (one row per (family, size_class) for plant families with
        # ≥ 2 ingested products). Each row carries an `is_average` flag so
        # Vincent's Google Sheets picker can distinguish them at a glance.
        rows = conn.execute(text("SELECT * FROM products_with_averages")).mappings().all()

    buf = io.StringIO()
    if rows:
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(
            [{k: _csv_value(v) for k, v in dict(r).items()} for r in rows]
        )
    else:
        # Even with zero rows, return a header line so Power Query gets a valid
        # CSV. Use the column list from the view's expected projection.
        buf.write(
            "id,reference_name,family_name,subcategory,brand,material,packaging,unit_type,"
            "attributes,cost_ht,cost_currency,supplier_name,supplier_rating,labor_task,"
            "heure_u_pose_default,nombre_uth_default,tier_1_label,tier_1_heure_u_decharge,"
            "tier_2_label,tier_2_heure_u_decharge,tier_3_label,tier_3_heure_u_decharge,"
            "quality_rating,last_price_update,months_since_update,freshness_status,"
            "is_active,is_average\n"
        )
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8")


@app.get("/api/taxonomy.csv")
def taxonomy(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    key: str | None = None,
) -> Response:
    """Serve the live (family, subcategory, packaging) taxonomy as CSV.

    The DPGF Sheet uses this as the authoritative source for the cascade
    filter dropdowns (Famille / Sous-catégorie / Conditionnement), separate
    from the products list — so the cascade options stay valid even when no
    products exist yet for a given triplet.
    """
    provided = x_api_key or key
    if provided != EXPECTED_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT pt.family_id,
                       pf.name AS family_name,
                       pt.subcategory,
                       pt.packaging
                  FROM product_taxonomy pt
                  JOIN product_families pf ON pf.id = pt.family_id
              ORDER BY pf.name, pt.subcategory, pt.packaging
                """
            )
        ).mappings().all()

    buf = io.StringIO()
    fieldnames = ["family_id", "family_name", "subcategory", "packaging"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows([dict(r) for r in rows])
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8")
