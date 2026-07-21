#!/usr/bin/env bash
set -euo pipefail

ac_root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ac_backup="${1:-}"

if [[ -z "$ac_backup" || ! -f "$ac_backup" ]]; then
  echo "Usage: $0 /path/to/available-computing-backup.db" >&2
  exit 1
fi

for ac_secret in "$ac_root_dir/secrets/admin_password.txt" "$ac_root_dir/secrets/jwt_secret.txt"; do
  if [[ ! -f "$ac_secret" ]]; then
    echo "Missing required secret file: $ac_secret" >&2
    exit 1
  fi
done

ac_restore_dir="$(mktemp -d "${TMPDIR:-/tmp}/available-computing-restore.XXXXXX")"
cleanup() {
  rm -f \
    "$ac_restore_dir/db.sqlite" \
    "$ac_restore_dir/db.sqlite-wal" \
    "$ac_restore_dir/db.sqlite-shm"
  rmdir "$ac_restore_dir"
}
trap cleanup EXIT

cp "$ac_backup" "$ac_restore_dir/db.sqlite"
chmod 600 "$ac_restore_dir/db.sqlite"

(
  cd "$ac_root_dir/backend"
  env \
    DATA_DIR="$ac_restore_dir" \
    ADMIN_PASSWORD_FILE="$ac_root_dir/secrets/admin_password.txt" \
    JWT_SECRET_FILE="$ac_root_dir/secrets/jwt_secret.txt" \
    alembic -c alembic.ini upgrade head
)

ac_integrity="$(sqlite3 "$ac_restore_dir/db.sqlite" 'PRAGMA integrity_check;')"
ac_foreign_keys="$(sqlite3 "$ac_restore_dir/db.sqlite" 'PRAGMA foreign_key_check;')"
ac_revision="$(sqlite3 "$ac_restore_dir/db.sqlite" 'SELECT version_num FROM alembic_version;')"

if [[ "$ac_integrity" != "ok" || -n "$ac_foreign_keys" ]]; then
  echo "Restore check failed." >&2
  exit 1
fi

echo "Restore check passed at migration: $ac_revision"
