# Deploy Ulting at reports.ultex.ma

This package uses two containers: a FastAPI backend and an Nginx frontend. The frontend serves the React build and forwards `/api/` unchanged to FastAPI, including streamed Strategist replies. Only the frontend is published, on `172.17.0.1:8310`. Your existing `ultex_workflow-nginx-proxy-1` terminates HTTPS and forwards this domain to that port. Existing sites and their `/api/` routes keep their own server blocks.

## 1. Prerequisites

- Create the DNS A record for `reports.ultex.ma` pointing to the server.
- Confirm the server has Docker Compose **2.30 or newer** with `docker compose version`. The `raw` env-file format preserves `$` in bcrypt hashes and other secrets.
- Confirm that `172.17.0.1` is the host bridge address used by your current Nginx proxy, and that port `8310` is free. It is distinct from the ports in your supplied `docker ps` output.
- Copy this project into its own server directory, for example `/home/ultex/ulting`. Keep it separate from `/home/ultex/ultex_workflow`.

## 2. Configure secrets on the server

From the Ulting project directory, create `.env.local` from `.env.example` and set `META_ACCESS_TOKEN`, `DASHBOARD_USER`, `DASHBOARD_PASSWORD`, `SESSION_SECRET`, and `OPENAI_API_KEY` if you want the AI features. Use the current System User token with access to the intended ad accounts. Restrict the file with `chmod 600 .env.local`. Do not put the file in Git or send it with a Docker build context; `.dockerignore` excludes it.

`DASHBOARD_PASSWORD` may be a bcrypt hash. Compose's `format: raw` preserves its `$` characters. Do not wrap the value in quote characters in `.env.local`; raw mode passes those quotes literally. The Compose file forces `SESSION_COOKIE_SECURE=true` and `CORS_ORIGINS=https://reports.ultex.ma` for production, regardless of the local defaults in `.env.local`.

If this server already has Ulting-generated images, videos, or an ad-change log, migrate them into the persistent `ulting_data` volume after the first start. New files are stored at `/data/generated` and `/data/changes.jsonl` inside the backend container. The volume survives container rebuilds and restarts. Keep a backup of it because the ad-change log is the record of write operations. After copying existing files into `/data`, set their owner to UID/GID `10001:10001` so the backend can keep writing them.

## 3. Build and start Ulting

These commands start the project; run them only when you intend to launch it:

```bash
cd /home/ultex/ulting
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl -fsS http://172.17.0.1:8310/api/health
```

The health response should show `metaTokenConfigured: true` and `loginConfigured: true`. The backend has no public host port. The frontend port is bound to the Docker bridge address, so the existing proxy can reach it without opening another public port.

## 4. Add the domain to the existing proxy

Your proxy config is `/home/ultex/ultex_workflow/infra/nginx-prod.conf`. Append the contents of `deploy/reports-http.conf` to it; do **not** replace the existing blocks. This block serves the ACME challenge from `/var/www/certbot` and redirects other HTTP traffic to HTTPS. The challenge directory must be the same shared webroot used by your existing certificate process.

Check and reload the existing proxy:

```bash
docker exec ultex_workflow-nginx-proxy-1 nginx -t
docker exec ultex_workflow-nginx-proxy-1 nginx -s reload
```

Issue a certificate for `reports.ultex.ma` with your existing Certbot/ACME process using that shared webroot. The TLS block cannot be loaded before these files exist in the proxy container:

```text
/etc/letsencrypt/live/reports.ultex.ma/fullchain.pem
/etc/letsencrypt/live/reports.ultex.ma/privkey.pem
```

Then append `deploy/reports-https.conf` to the same proxy config, test and reload it again. The TLS block forwards every path to the Ulting frontend on port 8310. Ulting's own Nginx sends `/api/` to FastAPI without the `/api/v1/` rewrite used by your workflow app. Both proxy layers disable response buffering so the Strategist stream reaches the browser as it is produced.

If the proxy's Certbot setup does not use `/var/www/certbot`, change the HTTP snippet to the actual shared challenge path before issuing the certificate. Keep the certificate paths in the TLS snippet aligned with your ACME client.

## 5. Verify after HTTPS is live

```bash
curl -fsS https://reports.ultex.ma/api/health
curl -I https://reports.ultex.ma/
docker compose ps
```

Sign in through `https://reports.ultex.ma`, open an account and verify the audit, Strategist stream, Creative gallery, and Ad manager plan view. Do not apply an ad change just as a smoke test. In browser developer tools, the session cookie should be `HttpOnly`, `Secure`, and `SameSite=Lax`. The Meta and OpenAI tokens must never appear in browser requests or the frontend build.

For later updates, rebuild with `docker compose up -d --build` from the Ulting directory. Back up the `ulting_data` volume before upgrades. To inspect failures, use `docker compose logs --tail=100 backend frontend`.
