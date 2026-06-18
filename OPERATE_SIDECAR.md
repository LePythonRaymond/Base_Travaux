# Operating the Pricing App (sidecar deploy on the n8n VPS)

The pricing app runs as an **isolated Docker stack next to n8n** on the Hostinger VPS
(`72.61.166.144`), routed by n8n's existing **Traefik** (we did NOT install Coolify).
It never touches n8n, n8n's Postgres, or the Traefik config.

| Thing | Value |
|---|---|
| Live URL | `https://prices.srv1082911.hstgr.cloud/` (HTTP basic-auth → Streamlit login) |
| Bordereau API | `https://prices.srv1082911.hstgr.cloud/api/bordereau.csv?key=<BORDEREAU_API_KEY>` |
| Compose file | `merci-raymond-pricing/docker-compose.sidecar.yml` |
| Compose project | `mrprices` (containers `mrprices-postgres-1`, `mrprices-streamlit-1`, `mrprices-bordereau_api-1`) |
| Secrets | `merci-raymond-pricing/.env` on the VPS (NEVER committed) |
| Repo on VPS | `~/Customs/Base_Travaux` |
| Shared proxy network | `root_merci_net` (Traefik's) — reused, not created |

All commands below run on the VPS in `~/Customs/Base_Travaux`.

---

## Day-to-day

**Logs / status**
```bash
docker compose -f docker-compose.sidecar.yml -p mrprices ps
docker compose -p mrprices logs -f streamlit bordereau_api
```

**Restart / stop / remove (n8n is never affected)**
```bash
docker compose -f docker-compose.sidecar.yml -p mrprices restart
docker compose -f docker-compose.sidecar.yml -p mrprices down       # stop+remove containers (keeps data volumes)
docker compose -f docker-compose.sidecar.yml -p mrprices down -v    # ALSO wipe the DB + invoices volumes
```

## Update the app after a code change (git)
```bash
cd ~/Customs/Base_Travaux && git pull
docker compose -f docker-compose.sidecar.yml -p mrprices up -d --build
```
`init/*.sql` runs **only on first boot** (empty DB). New SQL migrations after that are
applied by hand:
```bash
docker exec -i mrprices-postgres-1 psql -U mr_app -d merci_raymond < init/0N_new.sql
```

## Re-load / refresh the catalogue from a new review spreadsheet
Whenever the `dpgf_corpus_review.xlsx` is regenerated (more DPGFs, fixes), re-load it.
The loader is **idempotent**: approved (✓) rows upsert into `products`; un-ticked rows
refresh the `needs_info` ("à vérifier") queue without touching rows a human already
approved/rejected.
```bash
# 1) copy the new sheet from your Mac:
#    scp dpgf_corpus_review.xlsx root@72.61.166.144:~/Customs/Base_Travaux/
docker cp tools mrprices-streamlit-1:/app/tools
docker cp dpgf_corpus_review.xlsx mrprices-streamlit-1:/app/dpgf_corpus_review.xlsx
# 2) dry-run (counts only), then real:
docker exec -w /app/tools -e PYTHONPATH=/app mrprices-streamlit-1 \
  python -m dpgf_corpus_etl.run_load --review /app/dpgf_corpus_review.xlsx --dry-run
docker exec -w /app/tools -e PYTHONPATH=/app mrprices-streamlit-1 \
  python -m dpgf_corpus_etl.run_load --review /app/dpgf_corpus_review.xlsx
```
The 160-style "à vérifier" rows land in **À classifier → Ingestion en attente** in the app.
A reviewer sets the unit / confirms the forfait / rejects junk → approve → it joins the
live catalogue. Nothing unverified reaches the bordereau.

## Back up the database (do this regularly)
```bash
docker exec mrprices-postgres-1 pg_dump -U mr_app merci_raymond | gzip > ~/mr_backup_$(date +%F).sql.gz
# restore into a fresh stack:
gunzip -c ~/mr_backup_YYYY-MM-DD.sql.gz | docker exec -i mrprices-postgres-1 psql -U mr_app -d merci_raymond
```

## Rotate a secret (e.g. the bordereau key)
```bash
sed -i "s/^BORDEREAU_API_KEY=.*/BORDEREAU_API_KEY=$(openssl rand -hex 32)/" .env
docker compose -f docker-compose.sidecar.yml -p mrprices up -d
# then update the key in the Google Sheet's IMPORTDATA URL
```

---

## ⚠️ Before go-live: switch to your own domain (TLS reliability)
`*.hstgr.cloud` is shared by thousands of Hostinger customers and frequently hits Let's
Encrypt's rate limit (the box's `dashboard-commercial` app couldn't get a cert for weeks).
Our cert works now, but **renewal (~60 days) can get stuck** — which would break the
Google-Sheet `IMPORTDATA` (it reads over HTTPS).

Fix once: point your own domain at the VPS and switch:
```bash
# 1) DNS: A record  prices.<yourdomain>  →  72.61.166.144
# 2) on the VPS:
sed -i "s/^PUBLIC_DOMAIN=.*/PUBLIC_DOMAIN=prices.yourdomain.fr/" .env
docker compose -f docker-compose.sidecar.yml -p mrprices up -d
```
Your own domain has its own rate-limit budget = dependable auto-renewing TLS.

## Cutover (point Vincent's master Google Sheet at the live prices)
In the master DPGF Sheet, set the bordereau import to:
```
=IMPORTDATA("https://<PUBLIC_DOMAIN>/api/bordereau.csv?key=<BORDEREAU_API_KEY>")
```

---

## Notes
- The offline extractor/loader lives in `tools/dpgf_corpus_etl/` (`RESULTS.md` documents it).
  Add more DPGFs to `sources/dpgf/new/` on the Mac, re-run `worklist → _seed_cache →
  extract`, then re-load (above).
- `price_history.source = 'historical_dpgf'` tags every cost from this bulk load.
- The app's own review pages (Produits, Normes de pose, À classifier) are the place to
  refine the ~160 à-vérifier rows and the 71 labor norms — no spreadsheet round-trip needed.
