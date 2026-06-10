#!/bin/bash
# 世界杯彩票盈利系统 - 阿里云一键部署脚本
# 在服务器上执行: bash deploy.sh

set -e

echo "=== 世界杯彩票盈利系统 部署 ==="

# 1. 安装依赖
echo "[1/5] 安装系统依赖..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip python3-venv git nginx
elif command -v yum &>/dev/null; then
    sudo yum install -y python3 python3-pip git nginx
else
    echo "无法识别包管理器，请手动安装 python3, pip, git, nginx"
    exit 1
fi

# 2. 克隆项目
APP_DIR="/opt/worldcup-betting"
echo "[2/5] 克隆项目到 $APP_DIR..."
if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR" && git pull
else
    sudo mkdir -p /opt
    sudo chown $USER:$USER /opt
    git clone https://github.com/Lucky-bot-code/worldcup-betting.git "$APP_DIR"
fi
cd "$APP_DIR"

# 3. 安装 Python 依赖
echo "[3/5] 安装 Python 依赖..."
pip3 install -r requirements.txt -q

# 4. 配置 systemd 服务
echo "[4/5] 配置 systemd 服务..."
sudo tee /etc/systemd/system/worldcup.service > /dev/null << 'SERVICE'
[Unit]
Description=World Cup Betting System
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/worldcup-betting
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable worldcup
sudo systemctl restart worldcup

# 5. 配置防火墙
echo "[5/5] 配置防火墙..."
# 阿里云安全组需要手动在控制台开放 8000 端口
# 这里先开本地防火墙
if command -v ufw &>/dev/null; then
    sudo ufw allow 8000/tcp 2>/dev/null || true
elif command -v firewall-cmd &>/dev/null; then
    sudo firewall-cmd --add-port=8000/tcp --permanent 2>/dev/null || true
    sudo firewall-cmd --reload 2>/dev/null || true
fi

echo ""
echo "=== 部署完成 ==="
echo "访问地址: http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_SERVER_IP'):8000"
echo ""
echo "常用命令:"
echo "  查看状态: sudo systemctl status worldcup"
echo "  重启服务: sudo systemctl restart worldcup"
echo "  查看日志: sudo journalctl -u worldcup -f"
echo ""
echo "⚠ 重要: 请在阿里云控制台 → 安全组 → 入方向规则中，添加允许 8000 端口"
