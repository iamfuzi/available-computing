#!/usr/bin/env bash
set -euo pipefail

ac_root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ac_database="${1:-$ac_root_dir/backend/data/db.sqlite}"
ac_backup_dir="$ac_root_dir/backend/data/backups"
ac_timestamp="$(date '+%Y%m%d-%H%M%S')"
ac_backup="$ac_backup_dir/available-computing-$ac_timestamp.db"

if [[ ! -f "$ac_database" ]]; then
  echo "Database not found: $ac_database" >&2
  exit 1
fi

mkdir -p "$ac_backup_dir"
umask 077
sqlite3 "$ac_database" ".backup '$ac_backup'"

ac_integrity="$(sqlite3 "$ac_backup" 'PRAGMA integrity_check;')"
if [[ "$ac_integrity" != "ok" ]]; then
  echo "Backup integrity check failed: $ac_integrity" >&2
  exit 1
fi

echo "$ac_backup"
