# -*- coding: utf-8 -*-
"""应用配置"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据目录 (可环境变量覆盖，生产环境建议 /opt/vconfig/data)
DATA_ROOT = os.environ.get('CONFIG_BACKUP_ROOT', os.path.join(BASE_DIR, 'data'))
CONFIGS_DIR = os.path.join(DATA_ROOT, 'configs')
LOG_DIR = os.path.join(DATA_ROOT, 'log')
CERTS_DIR = os.path.join(DATA_ROOT, 'certs')  # HTTPS 自签名证书目录

# 数据库
SQLALCHEMY_DATABASE_URI = os.environ.get(
    'DATABASE_URL',
    f'sqlite:///{os.path.join(BASE_DIR, "vconfig.db")}'
)
SQLALCHEMY_TRACK_MODIFICATIONS = False

# Flask 会话密钥（用于登录 Session 等）
# 生产环境请通过环境变量 SECRET_KEY 覆盖为随机且足够复杂的值
SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production-please')

# 默认登录账号 (可在 Web 界面修改，Telnet/SSH 共用)
DEFAULT_USERNAME = os.environ.get('BACKUP_USERNAME', 'coniadmin')
DEFAULT_PASSWORD = os.environ.get('BACKUP_PASSWORD', 'C0niC1Oud@auth')

# 默认连接方式：TELNET / SSH
DEFAULT_CONNECTION_TYPE = os.environ.get('BACKUP_CONNECTION_TYPE', 'TELNET').upper()

# SSH 端口
SSH_PORT = int(os.environ.get('SSH_PORT', '22'))

# 备份并发线程数
BACKUP_THREAD_NUM = int(os.environ.get('BACKUP_THREAD_NUM', '10'))

# 排除设备名称模式 (正则)
EXCLUDE_PATTERNS = r".*OOB.*|.*4G.*|.*LTM.*|.*NTA.*|.*SSL.*"

# 历史备份保留天数（超过自动删除，0 表示不自动删除）
BACKUP_RETENTION_DAYS = int(os.environ.get('BACKUP_RETENTION_DAYS', '30'))

# 默认系统时区（用于界面时间展示）
DEFAULT_TIMEZONE = os.environ.get('BACKUP_TIMEZONE', 'Asia/Shanghai')
