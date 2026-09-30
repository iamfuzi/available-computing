#!/usr/bin/env bash
# SQLite 在线备份（WAL 安全）。使用 python3 标准库的 backup API，
# 不依赖 sqlite3 CLI（mini 主机未安装 CLI，python3 必有）。
set -euo pipefail

ac_root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ac_database="${1:-$ac_root_dir/backend/data/db.sqlite}"
# backend/data 由容器以 root 写入，普通用户无法在其下建目录；
# 默认输出到用户主目录，可用 AC_BACKUP_DIR 覆盖
ac_backup_dir="${AC_BACKUP_DIR:-$HOME/ac-backups}"
ac_timestamp="$(date '+%Y%m%d-%H%M%S')"
ac_backup="$ac_backup_dir/available-computing-$ac_timestamp.db"

if [[ ! -f "$ac_database" ]]; then
  echo "Database not found: $ac_database" >&2
  exit 1
fi

mkdir -p "$ac_backup_dir"
umask 077

python3 - "$ac_database" "$ac_backup" <<'PYEOF'
import sqlite3, sys

src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)  # SQLite online backup API：对 WAL 模式运行库安全
src.close()
integrity = dst.execute("PRAGMA integrity_check;").fetchone()[0]
dst.close()
if integrity != "ok":
    sys.stderr.write(f"Backup integrity check failed: {integrity}\n")
    sys.exit(1)
PYEOF

# 保留最近 14 份，更早的自动清理
ls -1t "$ac_backup_dir"/available-computing-*.db 2>/dev/null | tail -n +15 | xargs -r rm -f

echo "$ac_backup"
