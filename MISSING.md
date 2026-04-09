# MISSING.md — What's not yet implemented in teb

This file lists everything currently missing or incomplete in the teb project.
Each item is written as an actionable prompt that can be handed to an AI or developer.

---

## One-liner start script (`start.sh`)
**Status:** ✅ done
**Prompt:** A `start.sh` script has been added to the repo root. It auto-generates `TEB_JWT_SECRET` and `TEB_SECRET_KEY`, copies `.env.example` to `.env` if absent, installs dependencies, and starts the server. Usage: `git clone https://github.com/aiparallel0/teb.git && cd teb && bash start.sh` or `bash start.sh --docker` for Docker mode.

---

## PyPI package / `pip install teb`
**Status:** ✅ done
**Prompt:** `pyproject.toml` includes entry points (`teb` CLI command via `teb.main:cli`), project URLs, and complete metadata. A `publish.yml` GitHub Actions workflow uses `pypa/gh-action-pypi-publish` to build and upload the wheel automatically on every GitHub release. Set the `PYPI_API_TOKEN` secret to enable publishing.

---

## Docker Hub image
**Status:** ✅ done
**Prompt:** A `docker-publish.yml` GitHub Actions workflow builds and pushes the Docker image to `aiparallel0/teb:latest` (plus semver tags) on every GitHub release. Set `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets to enable publishing. Users can then run `docker pull aiparallel0/teb && docker run -p 8000:8000 aiparallel0/teb`.

---

## CI/CD pipeline for auto-deploying to portearchive.com
**Status:** ✅ done
**Prompt:** `.github/workflows/deploy.yml` triggers on push to `main`. It SSHs into the server, runs `git pull`, `docker compose up -d --build`, copies `nginx/teb.conf` as a snippet, and reloads nginx. Required secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`.

---

## Hosted version at portearchive.com/teb
**Status:** ready to deploy (requires server-side action)
**Prompt:** All configuration is in place: `TEB_BASE_PATH=/teb` is set in `docker-compose.yml`, `nginx/teb.conf` routes `/teb` to the upstream, and the deploy workflow automates updates. To activate: run the deploy workflow or manually `docker compose up -d` on the server and include `nginx/teb.conf` in the nginx server block.

---

## Systemd service file
**Status:** ✅ done
**Prompt:** `deploy/systemd/teb.service` includes full installation and prerequisite comments. Install with: `sudo cp deploy/systemd/teb.service /etc/systemd/system/teb.service && sudo systemctl daemon-reload && sudo systemctl enable --now teb`.

---

## Automated database backup for `teb.db`
**Status:** ✅ done
**Prompt:** `deploy/backup.sh` performs safe SQLite backups and prunes old copies. A systemd timer (`deploy/systemd/teb-backup.timer` + `teb-backup.service`) runs it daily at 02:00. Alternatively, add a cron entry. Documented in README under "Database Backups".

---

## Playwright install step
**Status:** ✅ done
**Prompt:** `start.sh` installs Playwright when `ENABLE_BROWSER=true` is set. The `Dockerfile` installs Playwright with Chromium automatically (`RUN playwright install --with-deps chromium`). Documented in README under "Browser Automation (Playwright)".

---

## `TEB_SECRET_KEY` auto-generation
**Status:** ✅ done
**Prompt:** `start.sh` generates `TEB_SECRET_KEY` (Fernet) on first run. For Docker, `deploy/docker-entrypoint.sh` generates it at container start if not already set via environment variable or `.env` file.

---

## HTTPS / TLS setup instructions
**Status:** ✅ done
**Prompt:** README includes an "HTTPS / TLS" section with two options: (A) Certbot with the existing nginx config (`certbot --nginx -d portearchive.com`), and (B) Caddy with a minimal `Caddyfile` for automatic HTTPS.

---

## Database migration system
**Status:** ✅ done
**Prompt:** `migrations/migrate.py` is a lightweight SQL migration runner. Migrations are numbered `.sql` files in `migrations/versions/`. Run `python -m migrations.migrate` to apply, or `python -m migrations.migrate --new "description"` to scaffold a new migration. Tracked in a `schema_migrations` table. Documented in README under "Database Migrations".

---

## `TEB_BASE_PATH` set for hosted deployment
**Status:** ✅ done
**Prompt:** `TEB_BASE_PATH=/teb` is set in `docker-compose.yml`. The nginx config in `nginx/teb.conf` already routes `/teb` to the upstream service.
