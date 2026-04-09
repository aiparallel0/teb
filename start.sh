#!/usr/bin/env bash
# start.sh — one-liner launcher for teb (Task Execution Bridge)
#
# Usage:
#   bash start.sh            # local mode (uvicorn)
#   bash start.sh --docker   # docker compose mode
#
# One-liner:
#   git clone https://github.com/aiparallel0/teb.git && cd teb && bash start.sh
#   git clone https://github.com/aiparallel0/teb.git && cd teb && bash start.sh --docker

set -euo pipefail

DOCKER_MODE=false
if [[ "${1:-}" == "--docker" ]]; then
  DOCKER_MODE=true
fi

PLACEHOLDER="change-me-in-production-not-safe"

# ── 1. Copy .env.example → .env if .env does not exist ───────────────────────
if [[ ! -f .env ]]; then
  echo "[start.sh] .env not found — copying from .env.example"
  cp .env.example .env
fi

# ── 2. Auto-generate TEB_JWT_SECRET if missing or placeholder ────────────────
current_jwt=$(grep -E "^TEB_JWT_SECRET=" .env | cut -d= -f2- | tr -d '"' || true)
if [[ -z "$current_jwt" || "$current_jwt" == "$PLACEHOLDER" ]]; then
  echo "[start.sh] Generating TEB_JWT_SECRET …"
  new_jwt=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
  # Replace or append
  if grep -q "^TEB_JWT_SECRET=" .env; then
    sed -i'' -e "s|^TEB_JWT_SECRET=.*|TEB_JWT_SECRET=${new_jwt}|" .env
  else
    echo "TEB_JWT_SECRET=${new_jwt}" >> .env
  fi
fi

# ── 3. Auto-generate TEB_SECRET_KEY (Fernet) if missing or blank ─────────────
current_sk=$(grep -E "^TEB_SECRET_KEY=" .env | cut -d= -f2- | tr -d '"' || true)
if [[ -z "$current_sk" ]]; then
  echo "[start.sh] Generating TEB_SECRET_KEY (Fernet) …"
  new_sk=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || true)
  if [[ -n "$new_sk" ]]; then
    if grep -q "^TEB_SECRET_KEY=" .env; then
      sed -i'' -e "s|^TEB_SECRET_KEY=.*|TEB_SECRET_KEY=${new_sk}|" .env
    elif grep -q "^# TEB_SECRET_KEY=" .env; then
      sed -i'' -e "s|^# TEB_SECRET_KEY=.*|TEB_SECRET_KEY=${new_sk}|" .env
    else
      echo "TEB_SECRET_KEY=${new_sk}" >> .env
    fi
  else
    echo "[start.sh] cryptography not installed yet — TEB_SECRET_KEY will be generated at runtime."
  fi
fi

# ── 4. Run ────────────────────────────────────────────────────────────────────
if $DOCKER_MODE; then
  echo "[start.sh] Starting via docker compose …"
  docker compose up --build
else
  echo "[start.sh] Installing dependencies …"
  pip install -r requirements.txt -q
  echo "[start.sh] Starting server at http://localhost:8000 …"
  uvicorn teb.main:app --reload
fi
