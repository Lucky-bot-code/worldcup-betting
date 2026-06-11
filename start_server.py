"""启动服务器脚本 —— 自动清理端口 + 清除缓存 + 打开浏览器"""
import subprocess, os, sys, time, webbrowser, socket

PORT = 8000
URL = f"http://localhost:{PORT}"

# Step 1: 杀掉占用端口的进程
print(f"[1/4] Cleaning port {PORT}...")
ps_cmd = f'''Get-NetTCPConnection -LocalPort {PORT} -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}'''
subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, timeout=10)
time.sleep(1.5)

# Step 2: 验证端口释放
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.bind(('0.0.0.0', PORT))
    s.close()
    print(f"[2/4] Port {PORT} is free")
except OSError:
    print(f"ERROR: Port {PORT} still in use. Restart computer or close the program manually.")
    sys.exit(1)

# Step 3: 清除 Python 缓存
print("[3/4] Clearing cache...")
base = os.path.dirname(os.path.abspath(__file__))
for root, dirs, files in os.walk(base):
    for d in list(dirs):
        if d == "__pycache__":
            path = os.path.join(root, d)
            try:
                for f in os.listdir(path):
                    os.unlink(os.path.join(path, f))
                os.rmdir(path)
            except Exception:
                pass

# Step 4: 启动服务 + 自动打开浏览器
print(f"[4/4] Starting server...")
import uvicorn
import threading

def open_browser():
    time.sleep(2)
    webbrowser.open(URL)
    print(f"\n  浏览器已打开: {URL}")
    print("  按 Ctrl+C 停止服务\n")

threading.Thread(target=open_browser, daemon=True).start()

uvicorn.run("main:app", host="0.0.0.0", port=PORT)
