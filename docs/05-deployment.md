# Available Computing —— 部署与运维指南

> 版本：Personal V1
> 更新日期：2026-07-21
> 关联文档：[当前架构](./03-architecture.md) · [接入手册](./06-integration.md)

---

## 1. 先选运行方式

| 模式 | 页面地址 | 后端地址 | 用途 |
|---|---|---|---|
| 源码开发 | `http://localhost:5173/` | `http://localhost:8002/` | 开发、调试、看热更新日志 |
| Docker 单容器 | `http://localhost:8081/` | 同一地址 | 长期个人运行 |

不要在源码开发模式访问 `8081`；该宿主机端口只在 Docker 容器启动后存在。容器内部仍监听 `8080`。

---

## 2. Docker 部署（推荐）

### 2.1 前置要求

- Docker Engine / Docker Desktop / OrbStack，支持 Docker Compose v2。
- 本机可访问所添加厂商的 API。
- `sqlite3` 仅在执行宿主机备份脚本时需要。

### 2.2 首次启动

```bash
git clone https://github.com/iamfuzi/available-computing.git
cd available-computing

mkdir -p secrets
openssl rand -base64 24 > secrets/admin_password.txt
python3 -c "import secrets; open('secrets/jwt_secret.txt','w').write(secrets.token_hex(64))"
chmod 600 secrets/admin_password.txt secrets/jwt_secret.txt

docker compose up -d --build
curl http://localhost:8081/api/status
```

浏览器打开 `http://localhost:8081/`。登录密码就是 `secrets/admin_password.txt` 中的内容。

Compose 默认把宿主机 `8081` 映射到容器 `8080`。如需改用其他宿主机端口，在被 Git 忽略的 `.env` 中设置：

```bash
AC_PORT=8090
```

首次登录后的建议顺序：

1. 在“厂商管理”添加符合个人免费规则的渠道。
2. 等待自动发现完成，在算力池确认存在健康模型。
3. 在“设置 → API 密钥”创建一个 `ac_` 代理 Key，并按用途设置厂商范围及 RPM/RPD。
4. 调用 `/v1/ac/self-test`，再接入实际应用。

### 2.3 日常命令

```bash
# 状态
docker compose ps

# 持续查看日志
docker compose logs -f app

# 重启
docker compose restart app

# 拉取代码后重新构建
docker compose up -d --build

# 停止但保留数据
docker compose down
```

当前 Compose 把宿主机 `./backend/data` 挂载到容器 `/app/data`。不要执行会删除该目录的命令；项目也不要求使用 `docker compose down -v`。

---

## 3. 源码开发

### 3.1 安装依赖

需要 Python 3.11+、Node.js 22+ 和 npm。

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd ../frontend
npm install
cd ..
```

### 3.2 准备本地 Secret

```bash
mkdir -p secrets
openssl rand -base64 24 > secrets/admin_password.txt
python3 -c "import secrets; open('secrets/jwt_secret.txt','w').write(secrets.token_hex(64))"
chmod 600 secrets/admin_password.txt secrets/jwt_secret.txt
```

如果仓库已经在安全位置保存了这两个文件，不要覆盖现有值。管理员密码参与上游凭证加密；更换它会导致旧密文无法解密。

### 3.3 一键启动

```bash
./scripts/dev.sh
```

脚本同时启动：

- 前端：`http://localhost:5173/`
- 后端：`http://localhost:8002/`

Vite 把 `/api`、`/v1` 与 `/ws` 转发到后端。终端中的两组日志会合并显示；按 `Ctrl-C` 会清理两个子进程。

快速检查：

```bash
curl http://localhost:8002/api/status
curl -I http://localhost:5173/
```

如果提示端口已占用，先用 `lsof -nP -iTCP:5173 -sTCP:LISTEN` 和 `lsof -nP -iTCP:8002 -sTCP:LISTEN` 找到旧进程；不要重复启动第二套服务。

---

## 4. 配置参考

### 4.1 必填项

直接变量与文件变量二选一，文件变量优先：

| 直接变量 | 文件变量 | 用途 |
|---|---|---|
| `ADMIN_PASSWORD` | `ADMIN_PASSWORD_FILE` | 管理员登录和上游 Key 加密密钥派生 |
| `JWT_SECRET` | `JWT_SECRET_FILE` | 管理端 JWT 签名 |

### 4.2 路径与跨域

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATA_DIR` | `./data` | SQLite 和运行数据目录 |
| `WHITELIST_PATH` | 仓库 `whitelist/providers.yaml` | 免费模型审定基线 |
| `PROVIDERS_PATH` | 仓库 `providers/` | 声明式厂商目录 |
| `CORS_ORIGINS` | `*` | 逗号分隔的允许 Origin；公网部署建议收紧 |

### 4.3 探测与复核

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `SLOW_THRESHOLD_MS` | `1000` | 慢响应阈值 |
| `PROBE_INTERVAL_BETWEEN_MODELS_SEC` | `2` | 同一渠道模型探测间隔 |
| `PROBE_GLOBAL_CONCURRENCY` | `5` | 全局探测并发 |
| `HEARTBEAT_IDLE_DAYS` | `3` | 多久无真实流量后才允许心跳 |
| `HEARTBEAT_MIN_PROVIDER_RPD` | `100` | 配额不明确或过小则不做心跳 |
| `HEARTBEAT_BUDGET_RATIO` | `0.01` | 心跳预算占已知日限额比例 |
| `EVENT_RECHECK_MAX_ATTEMPTS` | `3` | 事件复核最大次数 |
| `EVENT_RECHECK_WINDOW_MINUTES` | `30` | 多次复核关联窗口 |

目录刷新与探测扫描周期可在设置页修改，默认分别为 6 小时和 2 小时。

### 4.4 代理保护

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PROXY_RATE_WINDOW_SECONDS` | `60` | 本地代理限流窗口 |
| `PROXY_API_KEY_RATE_LIMIT` | `120` | 未单独配置时每个代理 Key 的基础路由限流 |
| `PROXY_ADMIN_RATE_LIMIT` | `600` | 管理 JWT 调用代理的限制 |
| `PROXY_IP_FALLBACK_RATE_LIMIT` | `600` | IP 兜底保护 |
| `PROXY_MODEL_CONCURRENCY_LIMIT` | `2` | 单渠道单模型并发上限 |

代理 Key 页面设置的 RPM/RPD 会叠加执行，不会覆盖系统保护。

---

## 5. 数据、迁移与备份

### 5.1 数据位置

- 源码开发：`backend/data/db.sqlite`
- Docker：宿主机同一文件，通过挂载映射到 `/app/data/db.sqlite`
- 上游凭证、代理 Key、健康历史、候选池和通知都在 SQLite 中。
- `secrets/` 与 `backend/data/` 被 Git 忽略，但仍应限制本机文件权限。

应用启动时创建缺失表并应用 Alembic 迁移。升级前仍应先做一致性备份。

已有数据库会先执行 Alembic，再由 SQLModel 补充不受迁移管理的缺失对象；
全新数据库则从当前模型创建并直接记录为最新 revision。迁移失败会阻止服务
启动，避免 `/api/status` 看似正常但业务表仍缺字段。不要在未核对实际 schema
时手工执行 `alembic stamp head`。

### 5.2 在线备份

SQLite 使用 WAL，运行中不要只复制 `db.sqlite`。使用仓库脚本调用 SQLite Online Backup：

```bash
./scripts/backup.sh
```

脚本会把权限为 `600` 的备份写到 `backend/data/backups/`，然后执行完整性检查并输出文件路径。

### 5.3 恢复演练

```bash
./scripts/check-backup.sh \
  backend/data/backups/available-computing-YYYYMMDD-HHMMSS.db
```

检查脚本在临时目录复制备份、升级到当前迁移版本，并执行 `integrity_check` 与 `foreign_key_check`；不会修改正在使用的数据库。

### 5.4 真正恢复

真正替换数据库属于有状态操作：

1. 停止应用。
2. 先再次备份当前数据库。
3. 对目标备份运行恢复检查。
4. 把目标备份复制为 `backend/data/db.sqlite`，权限设为 `600`。
5. 删除与旧数据库对应的 `db.sqlite-wal`、`db.sqlite-shm` 前，确认应用已经停止且目标路径准确。
6. 启动应用，检查 `/api/status`、登录、渠道数和 `/v1/ac/status`。

管理员密码 Secret 也必须与备份时期一致，否则已加密上游 Key 无法解密。

---

## 6. 公网访问

个人使用优先选择仅局域网、Tailscale/WireGuard，或反向代理 HTTPS。若必须暴露公网：

- Compose 端口改为 `127.0.0.1:8081:8080`，只让反向代理连接。
- 为域名启用 TLS，保留 WebSocket Upgrade 头。
- 设置精确的 `CORS_ORIGINS`。
- 不共享管理员密码、上游 Key 或高权限代理 Key。
- 为不同客户端创建独立 `ac_` Key，设置厂商范围和 RPM/RPD，方便单独停用。

Caddy 最小配置：

```caddyfile
ai.example.com {
    reverse_proxy 127.0.0.1:8081
}
```

---

## 7. 升级流程

```bash
# 1. 先备份并检查
backup_path="$(./scripts/backup.sh)"
./scripts/check-backup.sh "$backup_path"

# 2. 获取代码后重建
git pull --ff-only
docker compose up -d --build

# 3. 检查
docker compose ps
curl http://localhost:8081/api/status
docker compose logs --tail=100 app
```

源码开发升级后分别执行 `pip install -r backend/requirements.txt` 与 `npm install --prefix frontend`，再运行 `./scripts/dev.sh`。

---

## 8. 常见问题

### 页面打不开

- 源码开发请打开 `http://localhost:5173/`，并确认 `5173`、`8002` 都在监听。
- Docker 请打开 `http://localhost:8081/`，并运行 `docker compose ps` 与 `docker compose logs app`。
- `curl /api/status` 正常而页面异常时，优先检查前端构建或 Vite 进程。

### 添加渠道后没有模型

查看渠道状态与后台日志。常见原因是 Key 无效、上游网络不可达、模型目录不含审定免费项，或该厂商不再满足准入政策。系统不会为了显示数量把未知/付费模型自动标成免费。

### 模型存在但不能路由

在管理页查看健康状态，或使用 `/v1/ac/models`、`/v1/ac/status`。模型可能尚未真实验证、进入 429 冷却、渠道被停用、代理 Key 策略排除了厂商，或能力类型与端点不匹配。

### 修改管理员密码后渠道全部鉴权失败

管理员密码用于派生本地加密密钥，不能在不知道旧密码的情况下直接替换。恢复原密码 Secret，启动并确认渠道可解密后，再通过受控迁移方式重新加密凭证。

### 如何安全提交问题

附上版本、运行模式、错误时间、HTTP 状态和脱敏后的日志。必须移除 `Authorization`、Cookie、上游 Key、`ac_` Key、管理员密码以及包含这些值的请求体。
