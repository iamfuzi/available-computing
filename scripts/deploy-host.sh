#!/usr/bin/env bash
# AC 生产容器部署脚本（mini 主机 / 单容器 Docker 环境）
#
# 在仓库根目录执行：./scripts/deploy-host.sh
# 可选：SKIP_BUILD=1 只重建容器不重新构建镜像（当前镜像已是最新代码时用）
#
# 本脚本是生产容器的唯一权威定义：端口、挂载、全部环境变量（含出口
# 代理）都在这里。手工 docker run 容易漏掉代理变量——2026-09-30 起容器
# 依赖主机 v2ray 代理访问 Groq/Kilo，漏掉后这两个渠道会静默失效（模型
# 全部转 down，无任何报错）。修改生产配置请改本文件并提交，不要临时
# 手工 docker run。
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="available-computing:latest"
PORT="${AC_PORT:-8081}"

# 出口代理：指向 mini 主机上 v2ray 的 HTTP 入站（0.0.0.0:10810）。
# docker 默认网桥网关地址是 172.17.0.1。国内厂商走 NO_PROXY 直连，
# 其余（api.groq.com / api.kilo.ai 等）经代理出海。
# AC_DISABLE_PROXY=1 可去掉代理（调试用，Groq/Kilo 会失效）。
PROXY_ADDR="${AC_PROXY_ADDR:-http://172.17.0.1:10810}"
NO_PROXY_LIST="${AC_NO_PROXY:-open.bigmodel.cn,api.siliconflow.cn,maas-api.cn-huabei-1.xf-yun.com,apihub.agnes-ai.com,openrouter.ai,localhost,127.0.0.1}"

if [ "${SKIP_BUILD:-0}" != "1" ]; then
    echo "== 构建镜像 =="
    docker build -t "$IMAGE" -f docker/Dockerfile .
fi

PROXY_FLAGS=(
    -e HTTP_PROXY="$PROXY_ADDR"
    -e HTTPS_PROXY="$PROXY_ADDR"
    -e NO_PROXY="$NO_PROXY_LIST"
)
if [ "${AC_DISABLE_PROXY:-0}" = "1" ]; then
    PROXY_FLAGS=()
    echo "!! 代理已禁用（AC_DISABLE_PROXY=1）：Groq / Kilo 渠道将不可用"
fi

echo "== 重建容器 =="
docker rm -f available-computing 2>/dev/null || true
docker run -d --name available-computing \
    -p "${PORT}:8080" \
    --restart unless-stopped \
    -v "$PWD/secrets/admin_password.txt:/run/secrets/ac_admin_password" \
    -v "$PWD/secrets/jwt_secret.txt:/run/secrets/ac_jwt_secret" \
    -v "$PWD/backend/data:/app/data" \
    -e DATA_DIR=/app/data \
    -e PROXY_PROVIDER_RPM=60 \
    -e PROXY_DEFAULT_MODEL_RPM=30 \
    -e PROXY_PASSTHROUGH_TIMEOUT_SECONDS=60 \
    -e JWT_SECRET_FILE=/run/secrets/ac_jwt_secret \
    -e ADMIN_PASSWORD_FILE=/run/secrets/ac_admin_password \
    "${PROXY_FLAGS[@]}" \
    "$IMAGE"

sleep 5
docker ps --filter name=available-computing --format "状态: {{.Status}}"
echo
echo "完成。日志: docker logs -f available-computing"
