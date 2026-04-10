# Deploying teb to portearchive.com

## How Auto-Deploy Works

When you push to `main`, GitHub Actions:
1. Runs the CI test suite (all tests must pass)
2. SSHes into the production server
3. Pulls the latest code, rebuilds Docker, and reloads nginx
4. Verifies the health endpoint responds

## Required GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description | Example |
|--------|-------------|---------|
| `DEPLOY_HOST` | Server IP or hostname | `portearchive.com` |
| `DEPLOY_USER` | SSH username | `deploy` or `root` |
| `DEPLOY_SSH_KEY` | Private SSH key (ed25519 or RSA) | Contents of `~/.ssh/id_ed25519` |

## Required .env on the Server

At `/opt/teb/.env`, make sure these are set:

```bash
# REQUIRED
TEB_JWT_SECRET=<generate with: python -c "import secrets; print(secrets.token_urlsafe(64))">
TEB_BASE_PATH=/teb

# AI PROVIDER — set at least one for AI to actually work!
# Without these, teb uses template mode only (no real AI involvement).
ANTHROPIC_API_KEY=sk-ant-...
# OR
OPENAI_API_KEY=sk-...

# Optional
TEB_AI_PROVIDER=auto
TEB_SECRET_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
```

## Why AI Isn't Working ($0 API Spend)

If you see template-only responses (generic tasks, "manual execution required"):

1. **Check if AI keys are set in `.env`**: `grep -E "OPENAI_API_KEY|ANTHROPIC_API_KEY" /opt/teb/.env`
2. **Check if keys are passed to Docker**: `docker exec teb-teb-1 env | grep -E "OPENAI|ANTHROPIC"`
3. **Check if keys are commented out**: Lines starting with `#` in `.env` are ignored

The most common issue is having `ANTHROPIC_API_KEY` in `.env` but the Docker container
not receiving it because it wasn't listed in `docker-compose.yml`'s `environment:` section.
This is now fixed — both `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are passed through.

## Manual Deploy

```bash
ssh deploy@portearchive.com
cd /opt/teb
git pull origin main
docker compose up -d --build
sudo systemctl reload nginx
curl -s http://localhost:8000/health | python3 -m json.tool
```

## Triggering Deploy Without a Push

Go to **Actions → Deploy to portearchive.com → Run workflow** (uses `workflow_dispatch`).
