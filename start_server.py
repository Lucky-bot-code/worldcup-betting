"""启动服务器脚本 —— 自动清理端口占用后启动"""
import subprocess, os, sys, time

PORT = 8000

# Step 1: 用 PowerShell 强制杀掉占用端口的进程
print(f"Cleaning port {PORT}...")
ps_cmd = f'''Get-NetTCPConnection -LocalPort {PORT} -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}'''
subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, timeout=10)

time.sleep(2)

# Step 2: 验证端口是否释放
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.bind(('0.0.0.0', PORT))
    s.close()
    print(f"Port {PORT} is free, starting server...")
except OSError:
    print(f"ERROR: Port {PORT} still in use. Please restart your computer.")
    sys.exit(1)

# Step 3: 清除缓存并启动
for root, dirs, files in os.walk(os.path.dirname(__file__)):
    for d in dirs:
        if d == "__pycache__":
            path = os.path.join(root, d)
            for f in os.listdir(path):
                os.unlink(os.path.join(path, f))
            os.rmdir(path)

import uvicorn
uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
