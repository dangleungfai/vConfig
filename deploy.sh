#!/bin/bash
# vConfig 一键部署并启动（生产/开发通用） - 建议使用 ./deploy.sh
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

_install_snmp() {
    # 仅安装 SNMP 客户端工具（snmpwalk），方便在服务器上手动测试设备是否可通过 SNMP 访问
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -y >/dev/null 2>&1 || true
        sudo apt-get install -y snmp >/dev/null 2>&1 || true
    elif command -v apt &>/dev/null; then
        sudo apt update -y >/dev/null 2>&1 || true
        sudo apt install -y snmp >/dev/null 2>&1 || true
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y net-snmp-utils >/dev/null 2>&1 || true
    elif command -v yum &>/dev/null; then
        sudo yum install -y net-snmp-utils >/dev/null 2>&1 || true
    elif command -v apk &>/dev/null; then
        sudo apk add net-snmp >/dev/null 2>&1 || true
    elif command -v pacman &>/dev/null; then
        sudo pacman -Sy --noconfirm net-snmp >/dev/null 2>&1 || true
    elif command -v zypper &>/dev/null; then
        sudo zypper install -y net-snmp >/dev/null 2>&1 || true
    else
        return 1
    fi
}

_kill_port_if_used() {
    local port=$1
    local pids
    # 在 set -e 模式下，lsof 查不到进程会返回非 0，这里用 `|| true` 避免脚本中途退出
    pids=$(lsof -i :"$port" -t 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "端口 $port 已被占用，正在终止占用进程: $pids"
        for pid in $pids; do
            sudo kill -9 "$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
        done
        sleep 1
    fi
}

_install_python3() {
    if command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip python3.10-venv
    elif command -v apt &>/dev/null; then
        sudo apt update && sudo apt install -y python3 python3-venv python3-pip python3.10-venv
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

echo "[1/7] 检查 OpenSSL 环境..."
if ! command -v openssl &>/dev/null; then
    echo "警告：未检测到 openssl，将回退到 HTTP 模式（FLASK_HTTPS=0）。"
    echo "如需启用 HTTPS，请先在系统中安装 openssl（例如：sudo apt install -y openssl），再重新执行 ./deploy.sh。"
    export FLASK_HTTPS=0
else
    echo "OpenSSL 已就绪。"
fi

# 不安装 Nginx：由 vConfig 在 80 端口提供 HTTP→HTTPS 跳转，需保证 80 端口未被占用（若曾安装 Nginx 可 systemctl stop nginx）

echo "[2/7] 检查 Python3 环境..."
PYTHON_CMD=""
if command -v python3 &>/dev/null && python3 -c 'import sys; exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PYTHON_CMD=python3
elif command -v python &>/dev/null && python -c 'import sys; exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PYTHON_CMD=python
fi
if [ -z "$PYTHON_CMD" ]; then
    echo "未检测到 Python 3.8+。"
    echo "请先在系统中安装 Python 3.8+ 及 venv/pip 再执行 ./deploy.sh。"
    if command -v apt-get &>/dev/null || command -v apt &>/dev/null; then
        echo "例如：sudo apt install -y python3 python3-venv python3-pip"
    fi
    exit 1
fi

echo "检查 Python venv 模块支持..."
if ! "$PYTHON_CMD" -m venv --help >/dev/null 2>&1; then
    echo "当前 Python 环境缺少 venv 模块，无法创建虚拟环境。"
    if command -v apt-get &>/dev/null || command -v apt &>/dev/null; then
        echo "请先运行: sudo apt install -y python3-venv python3-pip"
    elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
        echo "请先安装 Python venv 相关软件包后重试。"
    fi
    exit 1
fi

echo "[3/7] 检查并安装 SNMP 客户端(snmpwalk)..."
if ! command -v snmpwalk &>/dev/null; then
    echo "提示：未检测到 snmpwalk，仅影响手工测试，不影响自动发现功能。"
    if command -v apt-get &>/dev/null || command -v apt &>/dev/null; then
        echo "如需在服务器上手工测试 SNMP，可执行: sudo apt install -y snmp"
    fi
else
    echo "SNMP 客户端已就绪 (snmpwalk)。"
fi

echo "[4/7] 创建虚拟环境并安装依赖..."
if [ ! -d venv ]; then
    "$PYTHON_CMD" -m venv venv
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
DEFAULT_PORT="443"
[ "$(id -u)" != "0" ] && DEFAULT_PORT="8443"
if [ -z "$FLASK_PORT" ]; then
    printf "是否使用默认端口 %s？[Y/n]: " "$DEFAULT_PORT"
    read -r use_default
    use_default=${use_default:-Y}
    if [[ "$use_default" =~ ^[Yy] ]]; then
        PORT=$DEFAULT_PORT
    else
        while true; do
            printf "请输入端口号 (1-65535): "
            read -r PORT
            if [[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ]; then
                break
            fi
            echo "无效端口，请重新输入。"
        done
    fi
    export FLASK_PORT=$PORT
else
    PORT=$FLASK_PORT
fi
_kill_port_if_used "$PORT"
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

echo "[7/7] 安装 systemd 服务..."
RUN_USER="${SUDO_USER:-$USER}"
if [ -z "$RUN_USER" ] || [ "$RUN_USER" = "root" ]; then
    RUN_USER="root"
fi
# 若用 sudo 部署，确保数据库与数据目录归运行用户所有，否则服务启动后无法读写
if [ "$(id -u)" = "0" ] && [ "$RUN_USER" != "root" ]; then
    echo "修正数据文件归属为 $RUN_USER..."
    for f in vconfig.db config_backup.db data venv; do
        [ -e "$SCRIPT_DIR/$f" ] && chown -R "$RUN_USER" "$SCRIPT_DIR/$f" 2>/dev/null || true
    done
fi
if command -v systemctl &>/dev/null && [ -d /etc/systemd/system ]; then
    sed -e "s|{{INSTALL_DIR}}|$SCRIPT_DIR|g" \
        -e "s|{{PORT}}|${PORT}|g" \
        -e "s|{{RUN_USER}}|$RUN_USER|g" \
        "$SCRIPT_DIR/vconfig.service" | sudo tee /etc/systemd/system/vconfig.service > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable vconfig
    sudo systemctl start vconfig
    echo "systemd 服务 vconfig 已安装并启动。"
else
    echo "提示：未检测到 systemd，将以前台方式启动（适用于 macOS 或非 systemd 系统）。"
fi

echo ""
echo "=============================================="
echo "  vConfig 部署完成"
echo "=============================================="
echo "  访问链接（请复制给客户）："
echo "  ${ACCESS_URL}"
echo "  HTTP(80) 将自动跳转至 HTTPS。"
echo ""
echo "  首次登录：用户名 admin  密码 admin123"
echo "  登录后请在「系统设置」中修改管理员密码。"
echo "=============================================="
if command -v systemctl &>/dev/null; then
    echo "  服务管理（systemctl）："
    echo "    查看状态: sudo systemctl status vconfig"
    echo "    停止服务: sudo systemctl stop vconfig"
    echo "    启动服务: sudo systemctl start vconfig"
    echo "    重启服务: sudo systemctl restart vconfig"
else
    echo "  若需绑定 443 端口，请使用: sudo ./deploy.sh"
    echo "  停止服务: 在当前终端按 Ctrl+C"
fi
echo "=============================================="
echo ""

if command -v systemctl &>/dev/null && [ -d /etc/systemd/system ]; then
    echo "vConfig 已作为系统服务运行。"
    exit 0
else
    exec ./venv/bin/python app.py
fi

