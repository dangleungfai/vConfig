#!/bin/bash
# 本机调试启动脚本
cd "$(dirname "$0")"
if [ ! -d venv ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi
if [ ! -f config_backup.db ]; then
    echo "初始化数据库..."
    ./venv/bin/flask --app app init-db
    [ -f ip_list ] && ./venv/bin/flask --app app import-ip-list
fi
echo "=========================================="
echo "  请在【系统自带浏览器】中打开以下地址："
echo "  http://127.0.0.1:5001"
echo "  或 http://localhost:5001"
echo "=========================================="
echo "  勿在 Cursor 内置浏览器中打开，否则可能 403。"
echo "  (端口 5001 避免与系统占用 5000 冲突)"
echo "=========================================="
exec ./venv/bin/python app.py
