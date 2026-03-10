# -*- coding: utf-8 -*-
"""应用配置"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据目录 (可环境变量覆盖，生产环境建议 /opt/vconfig/data)
DATA_ROOT = os.environ.get('CONFIG_BACKUP_ROOT', os.path.join(BASE_DIR, 'data'))
CONFIGS_DIR = os.path.join(DATA_ROOT, 'configs')
LOG_DIR = os.path.join(DATA_ROOT, 'log')
CERTS_DIR = os.path.join(DATA_ROOT, 'certs')  # HTTPS 自签名证书目录

# 数据库：优先 DATABASE_URL；若未设置则看 MARIADB_*，有则用 MariaDB，否则 SQLite
def _database_uri():
    url = os.environ.get('DATABASE_URL', '').strip()
    if url:
        return url
    # 仅当设置了 MARIADB_PASSWORD 或 MARIADB_USER 等时才用 MariaDB，避免影响未配置用户
    use_mariadb = os.environ.get('MARIADB_PASSWORD') is not None or os.environ.get('MARIADB_USER', '').strip()
    if not use_mariadb:
        return f'sqlite:///{os.path.join(BASE_DIR, "vconfig.db")}'
    host = os.environ.get('MARIADB_HOST', 'localhost').strip()
    port = os.environ.get('MARIADB_PORT', '3306').strip()
    user = (os.environ.get('MARIADB_USER', '') or 'vconfig').strip()
    password = os.environ.get('MARIADB_PASSWORD', '')
    database = (os.environ.get('MARIADB_DATABASE', '') or 'vconfig').strip()
    if not host or not user or not database:
        return f'sqlite:///{os.path.join(BASE_DIR, "vconfig.db")}'
    from urllib.parse import quote_plus
    pw_enc = quote_plus(password) if password else ''
    auth = f'{user}:{pw_enc}' if pw_enc else user
    return f'mysql+pymysql://{auth}@{host}:{port}/{database}'


SQLALCHEMY_DATABASE_URI = _database_uri()
SQLALCHEMY_TRACK_MODIFICATIONS = False

# Flask 会话密钥（用于登录 Session 等）
# 生产环境请通过环境变量 SECRET_KEY 覆盖为随机且足够复杂的值
SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production-please')

# 默认登录账号 (可在 Web 界面修改，Telnet/SSH 共用)，默认留空，仅通过 Web 或环境变量配置
DEFAULT_USERNAME = os.environ.get('BACKUP_USERNAME', '')
DEFAULT_PASSWORD = os.environ.get('BACKUP_PASSWORD', '')

# 默认连接方式：TELNET / SSH（默认 SSH，更安全）
DEFAULT_CONNECTION_TYPE = os.environ.get('BACKUP_CONNECTION_TYPE', 'SSH').upper()

# SSH 端口
SSH_PORT = int(os.environ.get('SSH_PORT', '22'))

# 备份并发线程数（默认 5，更稳妥）
BACKUP_THREAD_NUM = int(os.environ.get('BACKUP_THREAD_NUM', '5'))

# 排除设备名称模式 (正则)
EXCLUDE_PATTERNS = r".*OOB.*|.*4G.*|.*LTM.*|.*NTA.*|.*SSL.*"

# 历史备份保留天数（超过自动删除，0 表示不自动删除）
# 默认 365 天，可通过环境变量 BACKUP_RETENTION_DAYS 覆盖
BACKUP_RETENTION_DAYS = int(os.environ.get('BACKUP_RETENTION_DAYS', '365'))

# 默认系统时区（用于界面时间展示）
DEFAULT_TIMEZONE = os.environ.get('BACKUP_TIMEZONE', 'Asia/Shanghai')
