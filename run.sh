#!/bin/bash
# vConfig 一键部署并启动（生产/开发通用）
set -e
cd "$(dirname "$0")"
SCRIPT_DIR="$(pwd)"

_install_openssl() {
    if command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y openssl
    elif command -v apt &>/dev/null; then
        sudo apt update && sudo apt install -y openssl
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y openssl
    elif command -v yum &>/dev/null; then
        sudo yum install -y openssl
    elif command -v apk &>/dev/null; then
        sudo apk add openssl
    elif command -v pacman &>/dev/null; then
        sudo pacman -Sy --noconfirm openssl
    elif command -v zypper &>/dev/null; then
        sudo zypper install -y openssl
    elif command -v brew &>/dev/null; then
        brew install openssl
    else
        return 1
    fi
}

_install_nginx() {
    if command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y nginx
    elif command -v apt &>/dev/null; then
        sudo apt update && sudo apt install -y nginx
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y nginx
    elif command -v yum &>/dev/null; then
        sudo yum install -y nginx
    elif command -v apk &>/dev/null; then
        sudo apk add nginx
    elif command -v pacman &>/dev/null; then
        sudo pacman -Sy --noconfirm nginx
    elif command -v zypper &>/dev/null; then
        sudo zypper install -y nginx
    elif command -v brew &>/dev/null; then
        brew install nginx
    else
        return 1
    fi
}

_install_python3() {
    if command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
    elif command -v apt &>/dev/null; then
        sudo apt update && sudo apt install -y python3 python3-venv python3-pip
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3 python3-pip
    elif command -v yum &>/dev/null; then
        sudo yum install -y python3 python3-pip
    elif command -v apk &>/dev/null; then
        sudo apk add python3 py3-pip
    elif command -v pacman &>/dev/null; then
        sudo pacman -Sy --noconfirm python python-pip
    elif command -v zypper &>/dev/null; then
        sudo zypper install -y python3 python3-pip
    elif command -v brew &>/dev/null; then
        brew install python3
    else
        return 1
    fi
}

echo "[1/7] 检查并安装 OpenSSL..."
if ! command -v openssl &>/dev/null; then
    echo "未检测到 OpenSSL，正在尝试自动安装..."
    _install_openssl || true
fi
if ! command -v openssl &>/dev/null; then
    echo "提示：OpenSSL 未安装，将回退到 HTTP 模式（FLASK_HTTPS=0）。"
    export FLASK_HTTPS=0
fi

echo "[2/7] 检查并安装 Nginx..."
if ! command -v nginx &>/dev/null; then
    echo "未检测到 Nginx，正在尝试自动安装..."
    _install_nginx || true
fi
command -v nginx &>/dev/null && echo "Nginx 已就绪。" || echo "提示：Nginx 未安装，vConfig 将直接监听端口运行。"

echo "[3/7] 检查并安装 Python3..."
PYTHON_CMD=""
if command -v python3 &>/dev/null && python3 -c 'import sys; exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PYTHON_CMD=python3
elif command -v python &>/dev/null && python -c 'import sys; exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PYTHON_CMD=python
fi
if [ -z "$PYTHON_CMD" ]; then
    echo "未检测到 Python 3.8+，正在尝试自动安装..."
    _install_python3 || true
    command -v python3 &>/dev/null && PYTHON_CMD=python3
    command -v python &>/dev/null && [ -z "$PYTHON_CMD" ] && python -c 'import sys; exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null && PYTHON_CMD=python
fi
if [ -z "$PYTHON_CMD" ]; then
    echo "未检测到 Python 3.8+，已尝试自动安装。若仍失败，请手动安装后重新运行。"
    exit 1
fi

echo "[4/7] 创建虚拟环境并安装依赖..."
if [ ! -d venv ]; then
    $PYTHON_CMD -m venv venv
    ./venv/bin/pip install -q --upgrade pip
    ./venv/bin/pip install -q -r requirements.txt
else
    ./venv/bin/pip install -q -r requirements.txt
fi

echo "[5/7] 初始化数据目录与数据库..."
mkdir -p data data/configs data/log
if [ ! -f vconfig.db ] && [ ! -f config_backup.db ] && [ ! -f data/vconfig.db ] && [ ! -f data/config_backup.db ]; then
    ./venv/bin/flask --app app init-db
fi
./venv/bin/flask --app app reset-admin-password

echo "[6/7] 确定监听端口与访问地址..."
# 非 root 时使用 8443 避免绑定 443 失败
if [ "$(id -u)" != "0" ] && [ -z "$FLASK_PORT" ]; then
    export FLASK_PORT=8443
fi
PORT="${FLASK_PORT:-443}"
# 本机访问地址（供客户点击或复制）
if command -v hostname &>/dev/null; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
if [ -z "$IP" ] && [ -n "$(command -v ip)" ]; then
    IP=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')
fi
if [ -z "$IP" ] && [ -n "$(command -v ipconfig)" ]; then
    IP=$(ipconfig getifaddr en0 2>/dev/null) || IP=$(ipconfig getifaddr en1 2>/dev/null)
fi
[ -z "$IP" ] && IP="127.0.0.1"
if [ "$PORT" = "443" ]; then
    ACCESS_URL="https://${IP}"
else
    ACCESS_URL="https://${IP}:${PORT}"
fi

echo "[7/7] 启动服务..."
echo ""
echo "=============================================="
echo "  vConfig 部署完成"
echo "=============================================="
echo "  访问链接（请复制给客户）："
echo "  ${ACCESS_URL}"
echo ""
echo "  首次登录：用户名 admin  密码 admin123"
echo "  登录后请在「系统设置」中修改管理员密码。"
echo "=============================================="
echo "  若需绑定 443 端口，请使用: sudo ./run.sh"
echo "  停止服务: 在当前终端按 Ctrl+C"
echo "=============================================="
echo ""

exec ./venv/bin/python app.py
