#!/usr/bin/env bash
# ShopMind 比赛演示环境一键部署（Ubuntu 22.04+）
# 用法：sudo bash scripts/deploy-server.sh /opt/shopmind
set -euo pipefail

APP_DIR="${1:-/opt/shopmind}"
SERVICE_NAME="shopmind"
NGINX_SITE="shopmind"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 运行：sudo bash scripts/deploy-server.sh"
  exit 1
fi

echo "==> 安装系统依赖"
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip nginx ffmpeg

echo "==> 准备应用目录：${APP_DIR}"
mkdir -p "${APP_DIR}"
if [[ ! -f "${APP_DIR}/pyproject.toml" ]]; then
  echo "请先把 ShopMind 项目上传到 ${APP_DIR}（含 app/、pyproject.toml）"
  exit 1
fi

cd "${APP_DIR}"
if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -U pip setuptools wheel
.venv/bin/pip install -e .

if [[ ! -f ".env" ]]; then
  if [[ -f "deploy/env.production.example" ]]; then
    cp deploy/env.production.example .env
    echo "已生成 .env，请编辑 ${APP_DIR}/.env 填入 DASHSCOPE_API_KEY"
  else
    echo "缺少 .env，请手动创建并填入 DASHSCOPE_API_KEY"
  fi
fi

chown -R www-data:www-data "${APP_DIR}"
chmod 600 "${APP_DIR}/.env" 2>/dev/null || true

echo "==> 安装 systemd 服务"
cp deploy/shopmind.service "/etc/systemd/system/${SERVICE_NAME}.service"
sed -i "s|/opt/shopmind|${APP_DIR}|g" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "==> 配置 Nginx"
cp deploy/nginx-shopmind.conf "/etc/nginx/sites-available/${NGINX_SITE}"
ln -sf "/etc/nginx/sites-available/${NGINX_SITE}" "/etc/nginx/sites-enabled/${NGINX_SITE}"
nginx -t
systemctl reload nginx

echo ""
echo "部署完成。"
echo "1. 编辑 ${APP_DIR}/.env 填入百炼密钥"
echo "2. 修改 /etc/nginx/sites-available/${NGINX_SITE} 中的 server_name"
echo "3. 执行：systemctl restart ${SERVICE_NAME}"
echo "4. 浏览器访问：http://你的服务器IP/"
echo ""
systemctl --no-pager status "${SERVICE_NAME}" || true
