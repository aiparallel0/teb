#!/usr/bin/env bash
# deploy/backup.sh — Daily SQLite backup for teb
#
# Usage:
#   bash deploy/backup.sh                       # backs up default path
#   bash deploy/backup.sh /path/to/teb.db       # backs up a specific database
#
# Cron example (daily at 02:00):
#   0 2 * * * /opt/teb/deploy/backup.sh >> /var/log/teb-backup.log 2>&1
#
# The script keeps the most recent 30 backups and removes older ones.

set -euo pipefail

DB_PATH="${1:-/opt/teb/data/teb.db}"
BACKUP_DIR="${BACKUP_DIR:-/opt/teb/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
TIMESTAMP="$(date +%F_%H%M%S)"

if [[ ! -f "$DB_PATH" ]]; then
  echo "[backup] ERROR: database not found at $DB_PATH"
  exit 1
fi

mkdir -p "$BACKUP_DIR"

BACKUP_FILE="$BACKUP_DIR/teb-${TIMESTAMP}.db"

# Use SQLite's .backup command for a safe, consistent copy
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

echo "[backup] Created $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Prune old backups
find "$BACKUP_DIR" -name "teb-*.db" -mtime "+${KEEP_DAYS}" -delete 2>/dev/null || true

echo "[backup] Done — kept last $KEEP_DAYS days of backups"
