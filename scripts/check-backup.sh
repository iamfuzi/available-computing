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

ac_sql() {
  # sqlite3 CLI 不一定安装（mini 主机），用 python3 标准库执行同一条 SQL
  python3 - "$ac_restore_dir/db.sqlite" "$1" <<'PYEOF'
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
rows = conn.execute(sys.argv[2]).fetchall()
print("\n".join(str(r[0]) for r in rows))
conn.close()
PYEOF
}

ac_integrity="$(ac_sql 'PRAGMA integrity_check;')"
ac_foreign_keys="$(ac_sql 'PRAGMA foreign_key_check;')"
ac_revision="$(ac_sql 'SELECT version_num FROM alembic_version;')"

if [[ "$ac_integrity" != "ok" || -n "$ac_foreign_keys" ]]; then
  echo "Restore check failed." >&2
  exit 1
fi

echo "Restore check passed at migration: $ac_revision"
