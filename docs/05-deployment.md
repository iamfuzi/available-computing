# Available Computing —— 部署文档

> 版本：v0.5
> 日期：2026-05-06

---

## 目录

1. [Docker 部署（推荐）](#1-docker-部署推荐)
2. [本地开发](#2-本地开发)
3. [环境变量参考](#3-环境变量参考)
4. [生产部署建议](#4-生产部署建议)
5. [数据备份与恢复](#5-数据备份与恢复)
6. [常见问题](#6-常见问题)

---

## 1. Docker 部署（推荐）

### 前置要求

- Docker Engine 20.10+ 或 OrbStack
- 1GB 磁盘空间（镜像约 400MB）

### 步骤

```bash
# 1. 克隆仓库
git clone https://github.com/iamfuzi/available-computing.git
cd available-computing

# 2. 创建密钥文件
mkdir -p secrets
echo "your-secure-password" > secrets/admin_password.txt
python3 -c "import secrets; open('secrets/jwt_secret.txt','w').write(secrets.token_hex(32))"

# 3. 启动
docker compose up -d

# 4. 验证
curl -s http://localhost:8080/ | head -3
```

浏览器访问 `http://localhost:8080`，用设置的密码登录。

### 管理命令

```bash
# 查看日志
docker compose logs -f

# 停止
docker compose down

# 停止并清除数据
docker compose down -v

# 重新构建（代码更新后）
docker compose build --no-cache && docker compose up -d
```

### 架构

```
docker compose up
  └─ 容器 (Python 3.12 slim)
     ├─ FastAPI (uvicorn, port 8080)
     │   ├─ /api/v1/*     管理接口（需 JWT）
     │   ├─ /v1/*         OpenAI 兼容代理（需 JWT）
     │   ├─ /ws/events    WebSocket 推送（需 JWT）
     │   └─ /*            React 前端静态文件
     ├─ SQLite            /app/data/db.sqlite
     └─ 定时任务          发现 / 探测 / 清理
```

单容器，无 Nginx，FastAPI 直接服务前端静态文件。

---

## 2. 本地开发

### 后端

```bash
cd backend

# 安装依赖
pip install -r requirements.txt

# 设置必要环境变量
export JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export ADMIN_PASSWORD=dev

# 启动（热重载）
uvicorn main:app --reload --port 8000
```

后端运行在 `http://localhost:8000`。

### 前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端运行在 `http://localhost:5173`，自动代理 `/api` 和 `/ws` 到后端 `:8000`。

### 构建前端到后端

```bash
cd frontend && npm run build
cp -r dist ../backend/static
# 后端重启后即可通过 :8000 访问前端
```

---

## 3. 环境变量参考

### 必须设置

| 变量 | 说明 | 示例 |
|------|------|------|
| `JWT_SECRET` | JWT 签名密钥，至少 32 字节随机 | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_PASSWORD` | 管理员登录密码 | 任意非空字符串 |

两个变量都支持 `*_FILE` 后缀读取文件（Docker Secrets 模式）：

| 变量 | 说明 |
|------|------|
| `JWT_SECRET_FILE` | JWT 密钥文件路径，如 `/run/secrets/ac_jwt_secret` |
| `ADMIN_PASSWORD_FILE` | 密码文件路径，如 `/run/secrets/ac_admin_password` |

`*_FILE` 优先于直接设值。

### 可选设置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATA_DIR` | `./data` | 数据库存储目录 |
| `WHITELIST_PATH` | `whitelist/providers.yaml` | 免费模型白名单文件 |
| `SLOW_THRESHOLD_MS` | `1000` | 响应时间超过此值标记为"慢" |

### 运行时可调（通过 Settings 页面）

| 设置项 | 默认 | 范围 | 说明 |
|--------|------|------|------|
| `discovery_interval_hours` | 6 | 1-48 | 自动重新发现模型的间隔 |
| `probe_interval_hours` | 2 | 1-24 | 主动健康探测间隔 |
| `slow_threshold_ms` | 1000 | 100-10000 | 慢速阈值（毫秒） |

修改后立即生效，无需重启。

---

## 4. 生产部署建议

### 反向代理 + HTTPS

生产环境建议在前面加一层 Caddy 或 Nginx 终止 TLS：

#### Caddy（推荐，自动 HTTPS）

```Caddyfile
ai.yourdomain.com {
    reverse_proxy localhost:8080
}
```

```bash
# 启动
caddy reload --config Caddyfile
```

#### Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name ai.yourdomain.com;

    ssl_certificate     /etc/ssl/cert.pem;
    ssl_certificate_key /etc/ssl/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket
    location /ws/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### 安全加固

```bash
# 密码至少 16 位
echo "$(openssl rand -base64 24)" > secrets/admin_password.txt

# JWT 密钥 64 字节
python3 -c "import secrets; open('secrets/jwt_secret.txt','w').write(secrets.token_hex(64))"
```

### 完整 docker-compose 示例（含 Caddy）

```yaml
services:
  app:
    build:
      context: .
      dockerfile: docker/Dockerfile
    ports:
      - "127.0.0.1:8080:8080"   # 仅本地可达，由 Caddy 代理
    volumes:
      - app-data:/app/data
    environment:
      - ADMIN_PASSWORD_FILE=/run/secrets/ac_admin_password
      - JWT_SECRET_FILE=/run/secrets/ac_jwt_secret
    secrets:
      - ac_admin_password
      - ac_jwt_secret
    restart: unless-stopped

  caddy:
    image: caddy:2
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy-data:/data
      - caddy-config:/config
    restart: unless-stopped

volumes:
  app-data:
  caddy-data:
  caddy-config:

secrets:
  ac_admin_password:
    file: ./secrets/admin_password.txt
  ac_jwt_secret:
    file: ./secrets/jwt_secret.txt
```

---

## 5. 数据备份与恢复

所有数据存储在单个 SQLite 文件中。

### 备份

```bash
# Docker 部署
docker compose exec app sqlite3 /app/data/db.sqlite ".backup /app/data/backup.sqlite"
docker compose cp app:/app/data/backup.sqlite ./backup-$(date +%Y%m%d).sqlite

# 或直接复制（先停止容器）
docker compose down
cp data/db.sqlite ./backup-$(date +%Y%m%d).sqlite
docker compose up -d
```

### 恢复

```bash
docker compose down
cp backup-20260506.sqlite data/db.sqlite
docker compose up -d
```

### 白名单更新

白名单文件 `whitelist/providers.yaml` 打包在镜像内。更新时：

```bash
# 修改 whitelist/providers.yaml 后重新构建
docker compose build --no-cache && docker compose up -d
```

---

## 6. 常见问题

### 启动报 `JWT_SECRET is required`

必须设置 `JWT_SECRET` 或 `JWT_SECRET_FILE` 环境变量。

### 忘记管理员密码

```bash
# 重新生成密码文件
echo "new-password" > secrets/admin_password.txt
docker compose restart
```

### 端口冲突

```yaml
# docker-compose.yml 中修改端口
ports:
  - "9090:8080"   # 改为 9090
```

### 模型发现后健康状态全是 unknown

健康探测是定时任务（默认每 2 小时），新添加的模型需要等待下一轮探测。也可以通过 OpenAI 代理发一次真实调用来立即生成被动健康记录。

### Docker 构建慢

首次构建需要下载 Node 和 Python 基础镜像。后续构建会利用缓存，只重新构建变更层。

### 数据库锁定错误

SQLite 使用 WAL 模式，支持并发读写。如果遇到锁定错误，检查是否有多个进程同时写入同一个数据库文件。
