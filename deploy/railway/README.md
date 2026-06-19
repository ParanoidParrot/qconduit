# Deploying qconduit to Railway

Railway doesn't run `docker-compose.yml` directly — each service in
`docker-compose.yml` becomes a separate Railway service, wired together
with Railway's internal networking instead of Docker's.

You'll end up with **4 Railway services** inside one Railway project:

| Service | Source | Public domain? |
|---------|--------|-----------------|
| `redis` | Railway's Redis plugin (managed) | No |
| `qconduit-api` | This repo, root Dockerfile | **Yes** |
| `prometheus` | `deploy/railway/Dockerfile.prometheus` | No |
| `grafana` | `deploy/railway/Dockerfile.grafana` | **Yes** |

---

## Step 1 — Create the Railway project

```bash
railway login
railway init
```

Or do this in the Railway dashboard — click **New Project**.

---

## Step 2 — Add Redis (managed plugin, easiest option)

In the Railway dashboard:
1. **New** → **Database** → **Redis**
2. Railway provisions it automatically and gives you a `REDIS_URL` variable
3. Note the **service name** Railway assigns (usually `Redis`) — you'll reference it

You do *not* need to deploy `redis:7-alpine` yourself — Railway's managed Redis is simpler and handles persistence for you.

---

## Step 3 — Deploy the API service

1. **New** → **GitHub Repo** → select your `qconduit` repo
2. Railway detects `railway.toml` at the repo root and uses the Dockerfile automatically
3. **Rename this service to exactly `qconduit-api`** — Prometheus's scrape config depends on this exact name for internal DNS resolution (`qconduit-api.railway.internal`)
4. Go to **Variables** and set:

```
REDIS_URL          = ${{Redis.REDIS_URL}}        ← reference Railway's Redis variable
TOTAL_BUDGET_USD    = 5.0
GRAFANA_URL         = https://<your-grafana-service>.up.railway.app/d/qconduit
API_BASE_URL        = https://<this-service>.up.railway.app
SARVAM_API_KEY      = <your key>
OPENAI_API_KEY      = <your key>
ANTHROPIC_API_KEY   = <your key>
GOOGLE_API_KEY      = <your key>
META_API_KEY        = <your key>
```

> The `${{Redis.REDIS_URL}}` syntax is Railway's variable reference — it pulls
> the value from the Redis service automatically. You'll fill in the real
> Grafana URL after Step 5 once that service has a domain.

5. **Settings** → **Networking** → **Generate Domain** — this is your public API URL

---

## Step 4 — Deploy Prometheus (internal only)

1. **New** → **GitHub Repo** → same repo
2. **Settings** → **Source** → set **Root Directory** to `deploy/railway`
3. **Settings** → **Build** → set **Dockerfile Path** to `Dockerfile.prometheus`
4. **Rename this service to exactly `prometheus`** — Grafana's datasource config depends on this name
5. **Do NOT generate a public domain.** This service should only be reachable internally.

---

## Step 5 — Deploy Grafana (public)

1. **New** → **GitHub Repo** → same repo
2. **Settings** → **Source** → set **Root Directory** to `deploy/railway`
3. **Settings** → **Build** → set **Dockerfile Path** to `Dockerfile.grafana`
4. Go to **Variables** and set:

```
GF_SECURITY_ADMIN_PASSWORD = <pick a real password — keep it secret>
```

5. **Settings** → **Networking** → **Generate Domain** — this is your public Grafana URL
6. Go back to **Step 3** and update `GRAFANA_URL` on `qconduit-api` with this real domain

---

## Step 6 — Verify internal networking

Railway services reach each other via `<service-name>.railway.internal:<port>`.
This only works if you named the services **exactly**:

- `qconduit-api`
- `prometheus`

If you used different names, update:
- `deploy/railway/prometheus.yml` → target hostname
- `deploy/railway/grafana/provisioning/datasources/prometheus.yml` → datasource URL

…to match, then redeploy those two services.

---

## Step 7 — Smoke test

```bash
# API health check
curl https://<your-api-domain>.up.railway.app/health

# Landing page
open https://<your-api-domain>.up.railway.app

# Grafana — should load with no login (anonymous Viewer access)
open https://<your-grafana-domain>.up.railway.app
```

Click **Run demo** on the landing page, then check Grafana — the queue
panels should show jobs draining within a few seconds.

---

## Cost note

Railway bills per service based on usage. Running 4 services (API, Redis,
Prometheus, Grafana) continuously will cost more than a single service.
For a portfolio project this is usually a few dollars a month on Railway's
usage-based pricing — check Railway's current pricing page before deploying
long-term, since their free tier and pricing structure can change.

To reduce cost, you could:
- Use Railway's **sleep on inactivity** setting for services with low traffic
- Skip Prometheus/Grafana entirely on Railway and just demo them locally in
  interviews — keep only `qconduit-api` deployed publicly with the landing
  page, and mention "full observability stack runs via `docker compose up`"
  in your README