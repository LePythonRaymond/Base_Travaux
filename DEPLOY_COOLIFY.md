# Deploying Merci Raymond Pricing DB on Coolify

Production deploy runbook for the Hostinger VPS using **Coolify** + the GitHub
App, with **Coolify's Traefik proxy** replacing the bundled Caddy.

- Production compose: [`docker-compose.coolify.yml`](docker-compose.coolify.yml)
- Local dev compose (unchanged, still uses Caddy): [`docker-compose.yml`](docker-compose.yml)

> **Why Caddy is dropped:** Coolify's Traefik proxy owns ports 80/443 on the
> VPS. Caddy can't also bind them. So Traefik now does TLS, basic-auth, and the
> `/api` vs `/` split. (If the Traefik labels give you trouble on first deploy,
> see **Plan B** at the bottom — you can keep Caddy as an internal router.)

---

## 0. Prerequisites

1. A Hostinger VPS (Ubuntu) you can SSH into, with ports **80/443 open**.
2. **DNS A record** for your chosen hostname → VPS public IP, e.g.
   `prices.merciraymond.fr → <VPS IP>`. Must resolve **before** first deploy so
   Let's Encrypt's HTTP-01 challenge succeeds.
3. The GitHub repo `LePythonRaymond/Base_Travaux` (this repo), with the
   `deploy/coolify-migration` branch merged to `main` (or deploy that branch).

## 1. Install Coolify on the VPS (one-time)

```bash
ssh root@<VPS IP>
curl -fsSL https://cdn.coolify.io/v4/install.sh | bash
```

Open `http://<VPS IP>:8000`, create the admin account. Coolify installs its
Traefik proxy automatically.

## 2. Connect the GitHub App (one-time)

Coolify → **Sources → GitHub → Add** → install the Coolify GitHub App on the
`LePythonRaymond` account and grant access to `Base_Travaux`. This enables
**push-to-deploy**.

## 3. Create the resource

Coolify → **Project → New Resource → Docker Compose** (Git-based):
- Source: the `Base_Travaux` repo, branch `main`.
- **Compose file path:** `merci-raymond-pricing/docker-compose.coolify.yml`
  (Base directory `merci-raymond-pricing` if Coolify asks).
- **Leave each service's "Domains" field EMPTY** — the Traefik labels in the
  compose do all routing. Setting a domain here too will double-generate labels
  and conflict.

## 4. Environment variables

Set these in the resource's **Environment Variables** (Coolify stores them as
secrets — do **not** commit a `.env`):

| Variable | How to get it |
|---|---|
| `PUBLIC_DOMAIN` | your hostname, e.g. `prices.merciraymond.fr` (no scheme) |
| `POSTGRES_DB` | `merci_raymond` |
| `POSTGRES_USER` | `mr_app` |
| `POSTGRES_PASSWORD` | `openssl rand -hex 24` |
| `GEMINI_API_KEY` | Google AI Studio |
| `BORDEREAU_API_KEY` | `openssl rand -hex 32` |
| `STREAMLIT_AUTH_USER` | e.g. `admin` |
| `STREAMLIT_AUTH_PASSWORD_HASH` | bcrypt hash — see **Auth** below |

## 5. Deploy & verify

Click **Deploy**. First boot runs `init/01..06_*.sql` (schema + seed +
taxonomy) because `postgres_data` starts empty. Then:

```bash
curl https://<PUBLIC_DOMAIN>/api/health          # → {"ok": true}
curl -i https://<PUBLIC_DOMAIN>/api/bordereau.csv # → 401 (no key) — proves API auth
curl -H "X-API-Key: <BORDEREAU_API_KEY>" https://<PUBLIC_DOMAIN>/api/bordereau.csv  # → CSV
```

Open `https://<PUBLIC_DOMAIN>/` → browser basic-auth prompt → then the Streamlit
login. Both layers should challenge you.

---

## Auth (the bcrypt-hash detail)

Generate the hash (bcrypt; `$2y$`/`$2a$` both accepted by Traefik):

```bash
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'YOUR_PASSWORD'
# or: htpasswd -nbB admin 'YOUR_PASSWORD'   (take only the part AFTER the colon)
```

Put `admin` in `STREAMLIT_AUTH_USER` and the hash in
`STREAMLIT_AUTH_PASSWORD_HASH`. The compose builds the Traefik `basicauth.users`
value as `${STREAMLIT_AUTH_USER}:${STREAMLIT_AUTH_PASSWORD_HASH}`.

**If the basic-auth prompt rejects a correct password after deploy** (Coolify
mis-escaped the `$` in the hash): hardcode it instead — in
`docker-compose.coolify.yml` replace the interpolated middleware label with the
literal value, single-quoted so `$` is preserved:

```yaml
      - 'traefik.http.middlewares.mr-auth.basicauth.users=admin:$2y$12$theRestOfYourBcryptHashHere'
```

(Committing a bcrypt hash to a private repo is acceptable; it's not the
plaintext. Rotate by regenerating.)

---

## Persistence, backups, redeploys

- **`postgres_data`** and **`invoices_data`** are named volumes → survive
  redeploys. `init/*.sql` will **not** re-run while `postgres_data` has data.
- Enable **Coolify scheduled backups** for the Postgres service (daily). Also
  back up `invoices_data` if you ingest invoice PDFs.
- **Schema changes after first boot** are not auto-applied (no migration tool).
  Apply by hand: `docker exec -i <postgres> psql -U mr_app -d merci_raymond < init/0N_new.sql`.

## Promote the curated dataset to prod (at handover)

Production starts with an empty catalog (fresh schema only). When the
pre-launch data load (see `pre-launch-data-load` in memory) is approved on the
local DB, promote it:

```bash
# on the Mac (local stack running):
docker compose exec -T postgres pg_dump -U mr_app --data-only --disable-triggers merci_raymond > mr_data.sql
# copy to VPS, then load into the Coolify postgres:
docker exec -i <coolify_postgres> psql -U mr_app -d merci_raymond < mr_data.sql
```

(Use `--data-only` so the schema/seed from `init/*.sql` is preserved; load order
follows the FK chain suppliers → labor_norms → taxonomy → products.)

## Cutover

Point Vincent's master Google Sheet `IMPORTDATA` at
`https://<PUBLIC_DOMAIN>/api/bordereau.csv?key=<BORDEREAU_API_KEY>`, then stop
the local Mac stack and remove the ngrok stopgap.

---

## Plan B — keep Caddy as an internal router (lower-risk fallback)

If the Traefik labels fight you, keep the **proven** Caddy routing and let
Traefik only terminate TLS and forward the whole domain to Caddy:

1. In `docker-compose.coolify.yml`, re-add the `caddy` service **without** host
   ports, exposing `8080`, with labels:
   ```yaml
       expose: ["8080"]
       labels:
         - traefik.enable=true
         - "traefik.http.routers.mr-edge.rule=Host(`${PUBLIC_DOMAIN}`)"
         - traefik.http.routers.mr-edge.entryPoints=https
         - traefik.http.routers.mr-edge.tls=true
         - traefik.http.routers.mr-edge.tls.certresolver=letsencrypt
         - traefik.http.services.mr-edge.loadbalancer.server.port=8080
   ```
   …and remove the Traefik labels from `streamlit` / `bordereau_api`
   (set `traefik.enable=false` on both — Caddy reaches them internally).
2. Change the `Caddyfile` site address from `{$PUBLIC_DOMAIN}` to `:8080` and
   delete the TLS-related behavior (Traefik now does TLS). Keep the existing
   `handle /api/*` and `basic_auth` blocks exactly as they are.

This reuses Caddy's already-tested `/api` split + basic-auth and only changes
where TLS is terminated.
