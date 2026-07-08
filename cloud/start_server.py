"""
启动脚本 — 检查环境、释放端口、启动服务器
"""
import sys
import subprocess
import socket


def check_port(port=8000):
    """检查端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def kill_port(port=8000):
    """尝试释放被占用的端口（Windows）"""
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                print(f"  端口 {port} 被 PID {pid} 占用，正在终止...")
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True)
        import time
        time.sleep(2)
    except Exception as e:
        print(f"  释放端口失败: {e}")


def main():
    print("=" * 40)
    print("  草莓种植园巡检机器人 - 云端控制平台")
    print("=" * 40)
    print()

    # 检查端口
    if check_port(8000):
        print("[!] 端口 8000 已被占用，尝试释放...")
        kill_port(8000)
        if check_port(8000):
            print("[错误] 无法释放端口 8000，请手动关闭占用程序")
            return

    print("  访问地址: http://localhost:8000")
    print("  API文档:  http://localhost:8000/docs")
    print("  按 Ctrl+C 停止服务器")
    print()

    try:
        import uvicorn
        uvicorn.run("main:app", host="0.0.0.0", port=8000)
    except Exception as e:
        print(f"\n[错误] 服务器启动失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
