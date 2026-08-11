#!/usr/bin/env bash
# 问墨 Chat 云端一键部署脚本（国内 VPS 优化版：Ubuntu/Debian）
# 用法：把整个 agent-tutorial 目录上传到服务器后，在目录里执行：
#   chmod +x deploy_cloud.sh && sudo ./deploy_cloud.sh
set -e

echo "=== 1/5 安装系统依赖（国内源加速）==="
export DEBIAN_FRONTEND=noninteractive
# 国内服务器默认源通常已是镜像（阿里云/腾讯云），若太慢可换：
# sed -i 's|archive.ubuntu.com|mirrors.aliyun.com|g; s|security.ubuntu.com|mirrors.aliyun.com|g' /etc/apt/sources.list
apt-get update -y
apt-get install -y python3-venv python3-pip python3-dev build-essential fonts-noto-cjk || true

echo "=== 2/5 创建虚拟环境并安装 Python 依赖（清华 PyPI 镜像）==="
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
  || pip install fastapi uvicorn openai pydantic latex2mathml lxml psutil matplotlib requests mcp \
     -i https://pypi.tuna.tsinghua.edu.cn/simple

echo "=== 3/5 生成云端启动脚本 ==="
cat > run_cloud.py <<'EOF'
# 云端启动（不需要本地模型；视觉走免费路由/配置的图像模型）
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

import gui_server  # noqa: F401
import uvicorn

uvicorn.run(gui_server.app, host="0.0.0.0", port=8000, log_level="info")
EOF

echo "=== 4/5 注册 systemd 服务（开机自启 + 崩溃自动重启）==="
mkdir -p /opt/wenmo-chat
cp -r . /opt/wenmo-chat/ 2>/dev/null || true
cat > /etc/systemd/system/wenmo-chat.service <<'SERVICE'
[Unit]
Description=WenMo Chat (问墨)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/wenmo-chat
ExecStart=/opt/wenmo-chat/.venv/bin/python run_cloud.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable wenmo-chat
systemctl restart wenmo-chat

echo "=== 5/5 防火墙放行 8000 ==="
ufw allow 8000/tcp 2>/dev/null || true
iptables -I INPUT -p tcp --dport 8000 -j ACCEPT 2>/dev/null || true

sleep 3
echo ""
echo "=================================================="
echo " 部署完成！手机/电脑访问：http://<你的服务器公网IP>:8000"
echo " 查看状态：systemctl status wenmo-chat"
echo " 查看日志：journalctl -u wenmo-chat -f"
echo " 提示1：用 IP:8000 访问不需要 ICP 备案（80/443 才需要）"
echo " 提示2：providers.json 里的密钥随代码上传，注意服务器安全"
echo "=================================================="
