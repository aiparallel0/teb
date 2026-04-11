#!/usr/bin/env bash
# =============================================================================
#  deploy/server-setup.sh — teb MEGA SERVER SETUP SCRIPT
#  Run once on a fresh server (or re-run safely; it is idempotent).
#
#  What this script does, in order:
#    1.  Checks prerequisites (OS, root, curl, git, docker, nginx)
#    2.  Creates the deploy user & /opt/teb directory
#    3.  Generates (or reuses) SSH key pair → tells you the 3 GitHub secrets
#    4.  Clones / updates the repo at /opt/teb
#    5.  Writes /opt/teb/.env (interactive or from existing values)
#    6.  Installs / configures nginx snippet (nginx/teb.conf)
#    7.  Brings Docker Compose up
#    8.  Installs systemd backup timer (optional)
#    9.  Smoke-tests the health endpoint
#   10.  Prints the exact secrets to paste into GitHub
#
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/aiparallel0/teb/main/deploy/server-setup.sh | sudo bash
#    — OR —
#    sudo bash deploy/server-setup.sh
#
#  Environment variables you can pre-set to skip prompts:
#    REPO_URL          git clone URL          (default: https://github.com/aiparallel0/teb.git)
#    DEPLOY_USER       OS user for SSH        (default: deploy)
#    INSTALL_DIR       app directory          (default: /opt/teb)
#    TEB_JWT_SECRET    JWT signing secret     (auto-generated if not set)
#    TEB_SECRET_KEY    Fernet key             (auto-generated if not set)
#    ANTHROPIC_API_KEY Anthropic key          (optional)
#    OPENAI_API_KEY    OpenAI key             (optional)
#    TEB_BASE_PATH     proxy mount path       (default: /teb)
#    DOMAIN            server hostname/IP     (default: portearchive.com)
#    SKIP_NGINX        set to 1 to skip nginx (default: 0)
#    SKIP_SYSTEMD      set to 1 to skip timer (default: 0)
#    NONINTERACTIVE    set to 1 for CI        (default: 0)
# =============================================================================

set -euo pipefail

# ─── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
banner()  { echo -e "\n${BOLD}${CYAN}════════════════════════════════════════${RESET}"; \
echo -e "${BOLD}${CYAN}  $*${RESET}"; \
echo -e "${BOLD}${CYAN}════════════════════════════════════════${RESET}\n"; }

# ─── Defaults ─────────────────────────────────────────────────────────────────
REPO_URL="${REPO_URL:-https://github.com/aiparallel0/teb.git}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
INSTALL_DIR="${INSTALL_DIR:-/opt/teb}"
DOMAIN="${DOMAIN:-portearchive.com}"
TEB_BASE_PATH="${TEB_BASE_PATH:-/teb}"
SKIP_NGINX="${SKIP_NGINX:-0}"
SKIP_SYSTEMD="${SKIP_SYSTEMD:-0}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"

SSH_KEY_PATH="/home/${DEPLOY_USER}/.ssh/id_ed25519_github_actions"

# ─── STEP 0: Must be root ─────────────────────────────────────────────────────
banner "Step 0 — Preflight checks"

[[ $EUID -eq 0 ]] || error "Please run as root: sudo bash $0"
info "Running as root ✓"

# Detect OS
if [[ -f /etc/os-release ]]; then
  source /etc/os-release
  info "OS: $PRETTY_NAME"
else
  warn "Cannot detect OS — proceeding anyway."
fi

# ─── STEP 1: Install system packages ──────────────────────────────────────────
banner "Step 1 — System packages"

PKGS_NEEDED=()

command -v git      &>/dev/null || PKGS_NEEDED+=(git)
command -v curl     &>/dev/null || PKGS_NEEDED+=(curl)
command -v nginx    &>/dev/null || PKGS_NEEDED+=(nginx)
command -v docker   &>/dev/null || PKGS_NEEDED+=(docker.io)
command -v python3  &>/dev/null || PKGS_NEEDED+=(python3)

if [[ ${#PKGS_NEEDED[@]} -gt 0 ]]; then
  info "Installing: ${PKGS_NEEDED[*]}"
  if command -v apt-get &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq "${PKGS_NEEDED[@]}"
  elif command -v dnf &>/dev/null; then
    dnf install -y -q "${PKGS_NEEDED[@]}"
  elif command -v yum &>/dev/null; then
    yum install -y -q "${PKGS_NEEDED[@]}"
  else
    error "Unknown package manager. Install manually: ${PKGS_NEEDED[*]}"
  fi
fi

# Ensure docker compose v2 plugin exists
if ! docker compose version &>/dev/null 2>&1; then
  info "Installing docker-compose-plugin …"
  if command -v apt-get &>/dev/null; then
    apt-get install -y -qq docker-compose-plugin
  else
    warn "docker compose v2 plugin not found — install it manually."
  fi
fi

# Enable & start Docker
systemctl enable --now docker 2>/dev/null || true
success "System packages OK"

# ─── STEP 2: Create deploy user ───────────────────────────────────────────────
banner "Step 2 — Deploy user: ${DEPLOY_USER}"

if id "${DEPLOY_USER}" &>/dev/null; then
  info "User '${DEPLOY_USER}' already exists — skipping creation."
else
  useradd -m -s /bin/bash "${DEPLOY_USER}"
  success "Created user '${DEPLOY_USER}'"
fi

# Add to docker group
usermod -aG docker "${DEPLOY_USER}" 2>/dev/null || true

# Passwordless sudo for nginx & systemctl (deploy-specific commands only)
SUDOERS_FILE="/etc/sudoers.d/teb-deploy"
if [[ ! -f "$SUDOERS_FILE" ]]; then
  cat > "$SUDOERS_FILE" <<SUDO
# Auto-generated by teb server-setup.sh — safe to delete if deploy user removed
${DEPLOY_USER} ALL=(ALL) NOPASSWD: /bin/systemctl reload nginx, /bin/systemctl restart nginx, /usr/sbin/nginx, /bin/cp /opt/teb/nginx/* /etc/nginx/snippets/*
SUDO
  chmod 0440 "$SUDOERS_FILE"
  success "Sudoers rule written to $SUDOERS_FILE"
fi

# ─── STEP 3: SSH key for GitHub Actions ───────────────────────────────────────
banner "Step 3 — SSH key pair (GitHub Actions deploy key)"

SSH_DIR="/home/${DEPLOY_USER}/.ssh"
mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"
chown "${DEPLOY_USER}:${DEPLOY_USER}" "$SSH_DIR"

if [[ -f "${SSH_KEY_PATH}" ]]; then
  info "SSH key already exists at ${SSH_KEY_PATH} — reusing."
else
  info "Generating ed25519 key pair …"
  sudo -u "${DEPLOY_USER}" ssh-keygen -t ed25519 \
    -C "github-actions@${DOMAIN}" \
    -f "${SSH_KEY_PATH}" \
    -N ""
  success "Key pair generated."
fi

PUB_KEY="$(cat "${SSH_KEY_PATH}.pub")"
PRIV_KEY="$(cat "${SSH_KEY_PATH}")"

# Authorise the public key so GitHub Actions can SSH in
auth_keys="${SSH_DIR}/authorized_keys"
if ! grep -qF "$PUB_KEY" "$AUTH_KEYS" 2>/dev/null; then
  echo "$PUB_KEY" >> "$AUTH_KEYS"
  chmod 600 "$AUTH_KEYS"
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "$AUTH_KEYS"
  success "Public key added to authorized_keys"
else
  info "Public key already in authorized_keys."
fi

# Ensure sshd allows key auth
sed -i 's/^#*PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

# ─── STEP 4: Clone / update repo ──────────────────────────────────────────────
banner "Step 4 — Repository at ${INSTALL_DIR}"

if [[ -d "${INSTALL_DIR}/.git" ]]; then
  info "Repo already cloned — pulling latest …"
  cd "${INSTALL_DIR}"
  sudo -u "${DEPLOY_USER}" git pull origin main
else
  info "Cloning ${REPO_URL} → ${INSTALL_DIR}"
  git clone "${REPO_URL}" "${INSTALL_DIR}"
  chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${INSTALL_DIR}"
fi

success "Repo ready at ${INSTALL_DIR}"

# ─── STEP 5: Write .env ───────────────────────────────────────────────────────
banner "Step 5 — Environment file (.env)"

ENV_FILE="${INSTALL_DIR}/.env"

# Auto-generate secrets if not supplied
if [[ -z "${TEB_JWT_SECRET:-}" ]]; then
  if [[ -f "$ENV_FILE" ]] && grep -q "^TEB_JWT_SECRET=" "$ENV_FILE"; then
    TEB_JWT_SECRET="$(grep "^TEB_JWT_SECRET=" "$ENV_FILE" | cut -d= -f2-)"
    info "Reusing existing TEB_JWT_SECRET from .env"
  else
    TEB_JWT_SECRET="$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")"
    info "Generated new TEB_JWT_SECRET"
  fi
fi

if [[ -z "${TEB_SECRET_KEY:-}" ]]; then
  if [[ -f "$ENV_FILE" ]] && grep -q "^TEB_SECRET_KEY=" "$ENV_FILE"; then
    TEB_SECRET_KEY="$(grep "^TEB_SECRET_KEY=" "$ENV_FILE" | cut -d= -f2-)"
    info "Reusing existing TEB_SECRET_KEY from .env"
  else
    TEB_SECRET_KEY="$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null \
      || python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")"
    info "Generated new TEB_SECRET_KEY"
  fi
fi

# Interactive prompts for AI keys (unless NONINTERACTIVE=1)
if [[ "$NONINTERACTIVE" != "1" ]]; then
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo -n "Anthropic API key (sk-ant-…) or ENTER to skip: "
    read -r ANTHROPIC_API_KEY </dev/tty || ANTHROPIC_API_KEY=""
  fi
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo -n "OpenAI API key (sk-…) or ENTER to skip: "
    read -r OPENAI_API_KEY </dev/tty || OPENAI_API_KEY=""
  fi
fi

info "Writing ${ENV_FILE} …"
cat > "$ENV_FILE" <<ENV
# ─── teb .env — generated by server-setup.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ") ─────
# DO NOT COMMIT THIS FILE.

# ─── Security (REQUIRED) ──────────────────────────────────────────────────────
TEB_JWT_SECRET=${TEB_JWT_SECRET}
TEB_SECRET_KEY=${TEB_SECRET_KEY}

# ─── Deployment ───────────────────────────────────────────────────────────────
TEB_BASE_PATH=${TEB_BASE_PATH}
DATABASE_URL=sqlite:///data/teb.db

# ─── AI Providers ─────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
OPENAI_API_KEY=${OPENAI_API_KEY:-}
OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://api.openai.com/v1}
TEB_AI_PROVIDER=${TEB_AI_PROVIDER:-auto}

# ─── Optional ─────────────────────────────────────────────────────────────────
# TEB_CORS_ORIGINS=https://${DOMAIN}
# TEB_LOG_LEVEL=INFO
# TEB_EXECUTOR_TIMEOUT=30
# TEB_AUTONOMOUS_EXECUTION=true
ENV

chmod 600 "$ENV_FILE"
chown "${DEPLOY_USER}:${DEPLOY_USER}" "$ENV_FILE"
success ".env written."

# ─── STEP 6: Nginx ────────────────────────────────────────────────────────────
banner "Step 6 — Nginx configuration"

if [[ "$SKIP_NGINX" == "1" ]]; then
  warn "SKIP_NGINX=1 — skipping nginx setup."
else
  NGINX_SNIPPET_SRC="${INSTALL_DIR}/nginx/teb.conf"
  NGINX_SNIPPETS_DIR="/etc/nginx/snippets"
  NGINX_SITE="/etc/nginx/sites-enabled/teb"

  mkdir -p "$NGINX_SNIPPETS_DIR"

  if [[ -f "$NGINX_SNIPPET_SRC" ]]; then
    cp "$NGINX_SNIPPET_SRC" "${NGINX_SNIPPETS_DIR}/teb.conf"
    success "Copied nginx/teb.conf → ${NGINX_SNIPPETS_DIR}/teb.conf"
  else
    warn "nginx/teb.conf not found in repo — writing a default snippet."
    cat > "${NGINX_SNIPPETS_DIR}/teb.conf" <<NGINX
# Auto-generated teb nginx snippet
location ${TEB_BASE_PATH}/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_read_timeout 120s;
    client_max_body_size 10M;
}
NGINX
  fi

  # Create a basic site config that includes the snippet, only if none exists
  if [[ ! -f "$NGINX_SITE" ]] && [[ ! -f "/etc/nginx/sites-enabled/default" ]]; then
    cat > "/etc/nginx/sites-available/teb" <<SITE
server {
    listen 80;
    server_name ${DOMAIN};
    include snippets/teb.conf;
}
SITE
    ln -sf "/etc/nginx/sites-available/teb" "$NGINX_SITE"
    info "Created /etc/nginx/sites-available/teb and symlinked."
  fi

  nginx -t && systemctl reload nginx
  success "Nginx reloaded."
fi

# ─── STEP 7: Docker Compose up ────────────────────────────────────────────────
banner "Step 7 — Docker Compose"

cd "${INSTALL_DIR}"
sudo -u "${DEPLOY_USER}" docker compose pull --quiet 2>/dev/null || true
sudo -u "${DEPLOY_USER}" docker compose up -d --build
success "Containers running."

# ─── STEP 8: Systemd backup timer ─────────────────────────────────────────────
banner "Step 8 — Systemd backup timer"

if [[ "$SKIP_SYSTEMD" == "1" ]]; then
  warn "SKIP_SYSTEMD=1 — skipping systemd timer setup."
else
  SYSTEMD_SRC="${INSTALL_DIR}/deploy/systemd"
  if [[ -d "$SYSTEMD_SRC" ]]; then
    for unit in teb-backup.service teb-backup.timer teb.service; do
      if [[ -f "${SYSTEMD_SRC}/${unit}" ]]; then
        cp "${SYSTEMD_SRC}/${unit}" "/etc/systemd/system/${unit}"
        info "Installed /etc/systemd/system/${unit}"
      fi
    done
    systemctl daemon-reload
    systemctl enable --now teb-backup.timer 2>/dev/null || true
    success "Backup timer enabled."
  else
    warn "deploy/systemd/ not found — skipping timer."
  fi
fi

# ─── STEP 9: Health check ─────────────────────────────────────────────────────
banner "Step 9 — Health check"

info "Waiting 8 seconds for app to initialise …"
sleep 8

HEALTH_URL="http://localhost:8000/health"
if curl -sf "$HEALTH_URL" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  status : {d[\"status\"]}')
print(f'  version: {d.get(\"version\", \"unknown\")}')
sys.exit(0 if d['status'] == 'healthy' else 1)
"; then
  success "Health check passed ✓"
else
  warn "Health check did not return healthy. Check logs:"
  echo "  docker compose -f ${INSTALL_DIR}/docker-compose.yml logs --tail=40"
fi

# ─── STEP 10: Print GitHub secrets ───────────────────────────────────────────
banner "Step 10 — GitHub Actions Secrets"

SERVER_IP="$(curl -sf --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"

echo -e "${BOLD}Add these 3 secrets to your repository:${RESET}"
echo -e "  ${YELLOW}Settings → Secrets and variables → Actions → New repository secret${RESET}\n"

echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}" 
echo -e "${BOLD}Secret 1 — DEPLOY_HOST${RESET}" 
echo -e "${GREEN}${SERVER_IP}${RESET}   (or use your domain: ${DOMAIN})" 
echo ""
echo -e "${BOLD}Secret 2 — DEPLOY_USER${RESET}" 
echo -e "${GREEN}${DEPLOY_USER}${RESET}"
echo ""
echo -e "${BOLD}Secret 3 — DEPLOY_SSH_KEY${RESET}" 
echo -e "${YELLOW}(copy the ENTIRE block below, including -----BEGIN/END----- lines)${RESET}" 
echo -e "${GREEN}${PRIV_KEY}${RESET}" 
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo -e "${BOLD}Quick copy-paste for GitHub CLI (if you have gh installed locally):${RESET}"
echo ""
echo "  gh secret set DEPLOY_HOST    --body '${SERVER_IP}'  --repo aiparallel0/teb" 
echo "  gh secret set DEPLOY_USER    --body '${DEPLOY_USER}' --repo aiparallel0/teb" 
echo "  gh secret set DEPLOY_SSH_KEY < ${SSH_KEY_PATH}      --repo aiparallel0/teb" 
echo ""

# ─── Summary ──────────────────────────────────────────────────────────────────
banner "All done!"
echo -e "  App running at : ${GREEN}http://${DOMAIN}${TEB_BASE_PATH}/${RESET}"
echo -e "  Install dir    : ${INSTALL_DIR}"
echo -e "  Deploy user    : ${DEPLOY_USER}"
echo -e "  SSH private key: ${SSH_KEY_PATH}"
echo -e "  Health endpoint: ${HEALTH_URL}"
echo ""
echo -e "${BOLD}Next steps:${RESET}"
echo "  1. Add the 3 secrets above to GitHub (Settings → Secrets → Actions)."
echo "  2. Push any commit to 'main' — the deploy workflow will run automatically."
echo "  3. Or trigger it manually: Actions → Deploy to portearchive.com → Run workflow."
echo ""
echo -e "${YELLOW}If nginx was installed on a server that already has a domain, run:${RESET}"
echo "  certbot --nginx -d ${DOMAIN}   # for HTTPS / Let's Encrypt"
echo ""