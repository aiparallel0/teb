#!/usr/bin/env bash
# deploy/docker-entrypoint.sh — Docker entrypoint for teb
#
# Generates TEB_SECRET_KEY (Fernet) if not already set, then execs the main process.

set -euo pipefail

if [[ -z "${TEB_SECRET_KEY:-}" ]]; then
  echo "[entrypoint] TEB_SECRET_KEY not set — generating Fernet key …"
  export TEB_SECRET_KEY
  TEB_SECRET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
fi

exec "$@"
