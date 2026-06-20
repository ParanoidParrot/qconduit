# Deploying qconduit to Railway

Railway doesn't run `docker-compose.yml` directly — each service in
`docker-compose.yml` becomes a separate Railway service.

You'll end up with **4 Railway services** inside one Railway project:

| Service | Source | Public domain? |
|---------|--------|-----------------|
| `Redis` | Railway's Redis plugin (managed) | No |
| `qconduit` | This repo, root Dockerfile | **Yes** |
| `prometheus` | `deploy/railway/prometheus/` | No |
| `grafana` | `deploy/railway/grafana/` | **Yes** |

> Each service has its **own dedicated subfolder**, each with its own
> `Dockerfile` and `railway.toml`. This avoids the most common Railway
> pitfall: services sharing a Root Directory end up reading the wrong
> `railway.toml`, which silently overrides whichever Dockerfile you
> configured in the dashboard. One folder per service, one config per
> folder — no ambiguity.

---

## Step 1 — Create the Railway project

```bash
railway login
railway init
```

Or do this in the Railway dashboard — click **New Project**.

---

## Step 2 — Add Redis (managed plugin)

1. **New** → **Database** → **Redis**
2. Railway provisions it and exposes `REDIS_URL` automatically
3. Note the service name Railway assigns (usually `Redis`)

---

## Step 3 — Deploy the main API service

1. **New** → **GitHub Repo** → select your `qconduit` repo
2. Railway auto-detects the root `railway.toml` and `Dockerfile`
3. Rename this service to exactly `qconduit` — Prometheus's scrape config
   depends on this exact name for internal DNS (`qconduit.railway.internal`)
4. **Variables** tab — set:

```
REDIS_URL          = ${{Redis.REDIS_URL}}
TOTAL_BUDGET_USD   = 5.0
API_BASE_URL       = https://<this-service-domain>.up.railway.app
GRAFANA_URL        = https://<grafana-domain>.up.railway.app/d/qconduit
SARVAM_API_KEY     = <your key>
OPENAI_API_KEY     = <your key>
ANTHROPIC_API_KEY  = <your key>
GOOGLE_API_KEY     = <your key>
META_API_KEY       = <your key>
```

> Fill in `GRAFANA_URL` after Step 5, once that service has a real domain.

5. **Settings** → **Networking** → **Generate Domain**

---

## Step 4 — Deploy Prometheus (internal only)

1. **New** → **GitHub Repo** → same repo, new service
2. **Settings** → **Source**:
   - **Root Directory** → `deploy/railway/prometheus`
3. **Settings** → **Build**:
   - **Config File Path** → `deploy/railway/prometheus/railway.toml`
4. Rename this service to exactly `prometheus`
5. **Do NOT generate a public domain.**

---

## Step 5 — Deploy Grafana (public)

1. **New** → **GitHub Repo** → same repo, new service
2. **Settings** → **Source**:
   - **Root Directory** → `deploy/railway/grafana`
3. **Settings** → **Build**:
   - **Config File Path** → `deploy/railway/grafana/railway.toml`
4. **Variables** tab — set:

```
GF_SECURITY_ADMIN_PASSWORD = <pick a real password>
```

5. **Settings** → **Networking** → **Generate Domain**
6. Go back to Step 3 and update `GRAFANA_URL` on `qconduit` with this real domain

---

## Step 6 — Verify internal networking

Railway services reach each other via `<service-name>.railway.internal:<port>`.
This only works if services are named **exactly**:

- `qconduit`
- `prometheus`

If named differently, update:
- `deploy/railway/prometheus/prometheus.yml` → target hostname
- `deploy/railway/grafana/grafana/provisioning/datasources/datasource.yml` → datasource URL

…then redeploy those two services.

---

## Step 7 — Smoke test

```bash
curl https://<your-api-domain>.up.railway.app/health
open https://<your-api-domain>.up.railway.app
open https://<your-grafana-domain>.up.railway.app
```

Click **Run demo** on the landing page, then check Grafana — queue panels
should show jobs draining within a few seconds.

---

## Troubleshooting: build fails with "requirements.txt not found"

This means the service is reading the **wrong** `railway.toml` — almost
always because **Config File Path** wasn't set explicitly in the dashboard,
so Railway defaulted to the repo-root one (which points at the Python
Dockerfile). Double check **Settings → Build → Config File Path** matches
exactly what's listed in Steps 4 and 5 above.

---

## Cost note

Running 4 always-on services costs more than one. For a portfolio project,
check Railway's current usage-based pricing before deploying long-term.
To reduce cost, consider only deploying `qconduit` publicly and demoing
Grafana locally via `docker compose up` during interviews instead.