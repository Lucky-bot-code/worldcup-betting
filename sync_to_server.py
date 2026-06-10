#!/usr/bin/env python3
"""Sync local project changes to Alibaba Cloud server.

Usage: python3 sync_to_server.py [--full]
  --full  也同步 strategies/ 和 scraper/ 目录（默认只同步核心文件）
"""

import paramiko
import os
import sys

HOST = "116.62.152.64"
USER = "root"
PASSWORD = "4725036Qq"
APP_DIR = "/opt/worldcup-betting"

BASE = os.path.dirname(os.path.abspath(__file__))

# Core files to sync (relative to project root)
CORE_FILES = [
    "main.py", "models.py", "config.py", "backtest.py",
    "smart_betting.py", "start_server.py",
    "requirements.txt", "render.yaml", "deploy.sh",
    "static/index.html", "static/manual.html", "static/smart.html",
]

# Strategy and scraper directories
EXTRA_DIRS = ["strategies", "scraper"]

def run(cmd, desc=""):
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        print(f"  {out.strip()}")
    if err and "WARNING" not in err:
        print(f"  [stderr] {err.strip()[:200]}")
    return out, err

print("=== 同步代码到阿里云服务器 ===\n")

# Determine which files to sync
full_sync = "--full" in sys.argv
files_to_sync = list(CORE_FILES)
if full_sync:
    for d in EXTRA_DIRS:
        for root, dirs, files in os.walk(os.path.join(BASE, d)):
            for f in files:
                if f.endswith(('.py', '.html', '.js', '.css')):
                    files_to_sync.append(
                        os.path.relpath(os.path.join(root, f), BASE).replace('\\', '/')
                    )
    print(f"全量同步: {len(files_to_sync)} 个文件")
else:
    print(f"核心同步: {len(files_to_sync)} 个文件")
    print("  (加 --full 可同步 strategies/ 和 scraper/ 目录)")

# Connect
print("\n[1/4] 连接服务器...")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, port=22, username=USER, password=PASSWORD, timeout=15)
print("  已连接")

# Upload files
print("\n[2/4] 上传文件...")
sftp = client.open_sftp()
uploaded = 0
for rel_path in files_to_sync:
    local_path = os.path.join(BASE, rel_path)
    normalized = rel_path.replace('\\', '/')
    remote_path = f"{APP_DIR}/{normalized}"
    if not os.path.exists(local_path):
        print(f"  跳过(不存在): {rel_path}")
        continue
    # Ensure remote directory exists
    remote_dir = os.path.dirname(remote_path)
    try:
        sftp.stat(remote_dir)
    except FileNotFoundError:
        stdin, stdout, stderr = client.exec_command(f"mkdir -p {remote_dir}")
        stdout.read(); stderr.read()
    sftp.put(local_path, remote_path)
    uploaded += 1
sftp.close()
print(f"  已上传 {uploaded} 个文件")

# Install deps
print("\n[3/4] 检查依赖...")
run(
    f"/usr/local/bin/python3.11 -m pip install -r {APP_DIR}/requirements.txt -q 2>&1 | tail -3",
    "安装依赖",
)

# Restart service
print("\n[4/4] 重启服务...")
run("systemctl restart worldcup", "重启 worldcup")
import time
time.sleep(2)
out, _ = run("systemctl status worldcup --no-pager -l | head -8", "服务状态")
if "active (running)" in out:
    print("\n  同步成功，服务运行中")
    print(f"  http://{HOST}:8000")
else:
    print("\n  [警告] 服务可能未正常启动，请检查日志:")
    run("journalctl -u worldcup --no-pager -n 10")

client.close()
