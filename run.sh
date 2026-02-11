#!/bin/bash
# vConfig 本机开发/调试启动脚本
cd "$(dirname "$0")"
if [ ! -d venv ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi
# 默认数据库在项目目录；若使用 DATA_ROOT 则数据库可能在 data/ 下
if [ ! -f config_backup.db ] && [ ! -f data/config_backup.db ]; then
    echo "初始化数据库..."
    ./venv/bin/flask --app app init-db
fi
echo "=========================================="
echo "  vConfig 配置备份管理系统"
echo "  请在浏览器中打开（默认 HTTPS 自签名证书）："
echo "  https://127.0.0.1 或 https://localhost  (端口 443)"
echo "=========================================="
echo "  绑定 443 端口 Linux/Mac 可能需要: sudo ./venv/bin/python app.py"
echo "  禁用 HTTPS: FLASK_HTTPS=0 ./venv/bin/python app.py"
echo "=========================================="
exec ./venv/bin/python app.py
