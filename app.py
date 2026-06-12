# -*- coding: utf-8 -*-
"""配置备份 Web 管理"""
import os
import queue
import uuid
import base64
import telnetlib
import threading
import re
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False  # Windows 无 fcntl，仅进程内锁
from datetime import datetime, timedelta
import json
from flask import Flask, request, jsonify, Response, session, redirect, url_for
from config import (
    CONFIGS_DIR, LOG_DIR, DEFAULT_USERNAME, DEFAULT_PASSWORD,
    BACKUP_THREAD_NUM, EXCLUDE_PATTERNS, DEFAULT_CONNECTION_TYPE, SSH_PORT,
    BACKUP_RETENTION_DAYS, DEFAULT_TIMEZONE, DATA_ROOT, CERTS_DIR,
)
from models import db, Device, BackupLog, AppSetting, BackupJobRun, LoginLog, AuditLog, ConfigPushLog, User, DeviceTypeConfig, AutoDiscoveryRule, AutoDiscoveryRunLog, AutoDiscoveryJob, AlertLog, _isoformat_utc, normalize_user_role
from device_drivers import register_driver, load_custom_drivers
from device_drivers.builtin import register_builtin_drivers
from backup_service import run_single_backup, test_connection, run_backup_task
from blueprints.auth import create_auth_blueprint
from blueprints.backup_logs import create_backup_logs_blueprint
from blueprints.config_files import ConfigFilesService, create_config_files_blueprint
from blueprints.device_groups import create_device_groups_blueprint
from blueprints.device_inventory import create_device_inventory_blueprint
from blueprints.device_types import create_device_types_blueprint
from blueprints.pages import create_pages_blueprint
from blueprints.reports import create_reports_blueprint
from blueprints.settings_core import create_settings_core_blueprint
from blueprints.settings_ops import create_settings_ops_blueprint
from blueprints.settings_assets import create_settings_assets_blueprint
from blueprints.users import create_users_blueprint

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'))
app.config.from_object('config')
db.init_app(app)


_connection_type_column_ensured = False
_device_group_column_ensured = False
_device_maintenance_columns_ensured = False
_device_ssh_port_column_ensured = False
_device_telnet_port_column_ensured = False
_user_allowed_groups_column_ensured = False
_tables_ensured = False
_user_password_column_ensured = False
_user_email_phone_columns_ensured = False
_backup_job_run_type_column_ensured = False
_backup_job_executor_column_ensured = False
_device_type_configs_initialized = False

LOGO_DIR = os.path.join(DATA_ROOT, 'logo')


def _table_has_column(conn, table_name, column_name):
    """判断表是否包含某列，兼容 MySQL/MariaDB。"""
    from sqlalchemy import text
    dialect = db.engine.dialect.name
    if dialect in ('mysql', 'mariadb'):
        r = conn.execute(
            text("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"),
            {"t": table_name}
        )
        cols = [row[0] for row in r]
        return column_name in cols
    return False


LOGO_MAX_SIZE = (64, 64)

SUPER_ADMIN_USERNAME = 'admin'
SUPER_ADMIN_DEFAULT_PASSWORD = os.environ.get('SUPER_ADMIN_DEFAULT_PASSWORD', 'admin123')


def _normalize_device_type(dev_type: str) -> str:
    """规范化设备类型存储：
    - 优先从数据库 DeviceTypeConfig 查找
    - 兼容旧代码的硬编码映射
    - 其他保持原样
    """
    if not dev_type:
        return ''
    raw = str(dev_type).strip()
    upper = raw.upper()
    
    # 先尝试从数据库查找（如果已初始化）
    try:
        with app.app_context():
            config = DeviceTypeConfig.query.filter_by(type_code=raw).first()
            if config:
                return config.type_code
            # 尝试大写匹配
            config = DeviceTypeConfig.query.filter_by(type_code=upper).first()
            if config:
                return config.type_code
    except Exception:
        pass
    
    # 兼容旧代码的映射
    mapping = {
        'CISCO': 'Cisco',
        'JUNIPER': 'Juniper',
        'HUAWEI': 'Huawei',
        'H3C': 'H3C',
        'ROS': 'RouterOS',
    }
    return mapping.get(upper, raw or upper)


# 内置设备类型默认配置（与设备类型管理表单同步，列表 API 会据此返回 backup_config/connection_config）
BUILTIN_DEVICE_TYPES = [
                {
                    'type_code': 'Cisco',
                    'display_name': 'Cisco交换机/路由器',
                    'driver_type': 'generic',
                    'backup_config': {
                        'init_commands': ["\x03", "terminal length 0"],
                        'backup_command': 'show run',
                        'prompt': '\r\nend\r\n'
                    },
                    'connection_config': {
                        'login_prompt': 'sername',
                        'password_prompt': 'assword'
                    },
                    'sort_order': 1
                },
                {
                    'type_code': 'Juniper',
                    'display_name': 'Juniper交换机/路由器',
                    'driver_type': 'generic',
                    'backup_config': {
                        'init_commands': ['set cli screen-length 0'],
                        'backup_command': 'show configuration | display set | no-more',
                        'prompt': '> \r\n'
                    },
                    'connection_config': {
                        'login_prompt': 'sername',
                        'password_prompt': 'assword'
                    },
                    'sort_order': 2
                },
                {
                    'type_code': 'Huawei',
                    'display_name': 'Huawei交换机/路由器',
                    'driver_type': 'generic',
                    'backup_config': {
                        'init_commands': ['screen-length 0 temporary'],
                        'backup_command': 'disp cur',
                        'prompt': ']'
                    },
                    'connection_config': {
                        'login_prompt': 'sername',
                        'password_prompt': 'assword'
                    },
                    'sort_order': 3
                },
                {
                    'type_code': 'H3C',
                    'display_name': 'H3C交换机/路由器',
                    'driver_type': 'generic',
                    'backup_config': {
                        'init_commands': ['screen-length disable'],
                        'backup_command': 'display current-configuration',
                        'prompt': 'return\r\n'
                    },
                    'connection_config': {
                        'login_prompt': 'sername',
                        'password_prompt': 'assword'
                    },
                    'sort_order': 4
                },
                {
                    'type_code': 'RouterOS',
                    'display_name': 'RouterOS路由器',
                    'driver_type': 'generic',
                    'backup_config': {
                        'init_commands': [],
                        # 含 "success\n"，结尾 output_success
                        'backup_command': ':foreach i in=(:put[/export]&:put[:put (("output_").("success\n"))]) do={$i}',
                        'prompt': 'output_success'
                    },
                    'connection_config': {
                        'login_prompt': 'ogin',
                        'password_prompt': 'assword',
                        'prompts': [r'.*\]\s*>\s*']
                    },
                    'sort_order': 5
                },
]


def _get_builtin_type_config(type_code):
    """返回内置类型的 backup_config 与 connection_config，供设备类型列表 API 同步到前端输入框。"""
    code = (type_code or '').strip()
    for t in BUILTIN_DEVICE_TYPES:
        if (t.get('type_code') or '').strip() == code:
            return {'backup_config': t.get('backup_config') or {}, 'connection_config': t.get('connection_config') or {}}
    return {}


def _ensure_device_type_configs():
    """确保设备类型配置表存在并初始化默认设备类型"""
    global _device_type_configs_initialized
    if _device_type_configs_initialized:
        return
    try:
        with app.app_context():
            # 注册内置驱动（仅执行一次）
            register_builtin_drivers()
            load_custom_drivers()

            for dt in BUILTIN_DEVICE_TYPES:
                existing = DeviceTypeConfig.query.filter_by(type_code=dt['type_code']).first()
                if not existing:
                    # 新建：使用通用驱动 + 默认命令配置
                    config = DeviceTypeConfig(
                        type_code=dt['type_code'],
                        display_name=dt['display_name'],
                        driver_type='generic',
                        enabled=True,
                        sort_order=dt['sort_order']
                    )
                    config.set_backup_config(dt['backup_config'])
                    config.set_connection_config(dt['connection_config'])
                    db.session.add(config)
                else:
                    # 已存在：若之前是 builtin，则迁移为 generic 并补全默认配置，方便在界面中直接查看与修改命令
                    if (existing.driver_type or '').strip() == 'builtin':
                        existing.driver_type = 'generic'
                    # 根据解析后的配置是否为空来判断是否需要写入默认值
                    try:
                        bc = existing.get_backup_config()
                    except Exception:
                        bc = {}
                    if not bc:
                        existing.set_backup_config(dt['backup_config'])
                    try:
                        cc = existing.get_connection_config()
                    except Exception:
                        cc = {}
                    if not cc:
                        existing.set_connection_config(dt['connection_config'])
                    # RouterOS：强制使用正确配置（旧配置会导致备份文件为空），通用驱动下命令/提示符必须正确
                    if (existing.type_code or '').strip() in ('RouterOS', 'ROS'):
                        existing.set_backup_config(dt['backup_config'])
                        existing.set_connection_config(dt['connection_config'])
                    # Juniper/H3C：将旧版 prompt 同步为当前内置默认（Juniper "> \r\n"，H3C "return\r\n"）
                    if (existing.type_code or '').strip() == 'Juniper' and bc and (bc.get('prompt') or '').strip() in ('>', '>\n'):
                        existing.set_backup_config(dt['backup_config'])
                    if (existing.type_code or '').strip() == 'H3C' and bc and (bc.get('prompt') or '').strip() in ('>', '>\n', '> \r\n', '>   \r\n'):
                        existing.set_backup_config(dt['backup_config'])

            db.session.commit()
            _device_type_configs_initialized = True
    except Exception as e:
        import logging
        logging.warning(f"Failed to initialize device type configs: {e}")
        _device_type_configs_initialized = True  # 避免重复尝试


def _ensure_user_password_column():
    """为 users 表添加 password_hash 列（兼容旧 MariaDB 库）"""
    global _user_password_column_ensured
    if _user_password_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                if not _table_has_column(conn, 'users', 'password_hash'):
                    conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
                    conn.commit()
            _user_password_column_ensured = True
    except Exception:
        pass


def _ensure_user_email_phone_columns():
    """为 users 表添加 email、phone 列（兼容旧 MariaDB 库）"""
    global _user_email_phone_columns_ensured
    if _user_email_phone_columns_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                if not _table_has_column(conn, 'users', 'email'):
                    conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(128)"))
                    conn.commit()
                if not _table_has_column(conn, 'users', 'phone'):
                    conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(32)"))
                    conn.commit()
            _user_email_phone_columns_ensured = True
    except Exception:
        pass


def _ensure_super_admin():
    """确保存在内置超级管理员 admin（本地账号），默认密码 admin123，仅在不存在时创建。

    - 若已存在 admin 账号，则只确保其角色为 admin、处于启用状态，不修改其既有密码。
    - 必须在 app.app_context() 内调用。
    """
    try:
        u = User.query.filter_by(username=SUPER_ADMIN_USERNAME).first()
        if u is None:
            u = User(
                username=SUPER_ADMIN_USERNAME,
                display_name='内置超级管理员',
                source='local',
                role='admin',
                is_active=True,
            )
            # 仅在创建时设置默认密码，后续用户可在用户管理中修改
            u.set_password(SUPER_ADMIN_DEFAULT_PASSWORD)
            db.session.add(u)
        else:
            changed = False
            if (u.role or '').strip() != 'admin':
                u.role = 'admin'
                changed = True
            if not u.is_active:
                u.is_active = True
                changed = True
            # 确保 source='local'，否则登录时 filter_by(username, source='local') 会查不到
            if (u.source or '').strip() != 'local':
                u.source = 'local'
                changed = True
            # 若之前未设置密码哈希，则补充默认密码
            if not (u.password_hash or ''):
                u.set_password(SUPER_ADMIN_DEFAULT_PASSWORD)
                changed = True
            if not changed:
                return
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _normalize_existing_user_roles():
    """将旧角色名 operator/readonly 迁移到 ops/viewer。"""
    try:
        changed = False
        for u in User.query.all():
            normalized = normalize_user_role(u.role)
            if u.role != normalized:
                u.role = normalized
                changed = True
        if changed:
            db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _ensure_tables():
    """确保所有表存在（包括后续新增的表，如自动发现运行日志）。"""
    global _tables_ensured
    try:
        with app.app_context():
            db.create_all()
            # 保障 users.password_hash 列与内置超级管理员存在
            _ensure_user_password_column()
            _ensure_user_email_phone_columns()
            _ensure_super_admin()
            _normalize_existing_user_roles()
            _ensure_backup_job_run_type_column()
            _ensure_backup_job_executor_column()
            # 初始化设备类型配置
            _ensure_device_type_configs()
    except Exception:
        pass


def _ensure_connection_type_column():
    """为已有数据库添加 connection_type 列（兼容旧 MariaDB 库）"""
    global _connection_type_column_ensured
    if _connection_type_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                if not _table_has_column(conn, 'devices', 'connection_type'):
                    conn.execute(text("ALTER TABLE devices ADD COLUMN connection_type VARCHAR(16)"))
                    conn.commit()
            _connection_type_column_ensured = True
    except Exception:
        pass


def _ensure_device_group_column():
    """为 devices 表添加 device_group 列（兼容旧 MariaDB 库）"""
    global _device_group_column_ensured
    if _device_group_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                if not _table_has_column(conn, 'devices', 'device_group'):
                    conn.execute(text("ALTER TABLE devices ADD COLUMN device_group VARCHAR(64)"))
                    conn.commit()
            _device_group_column_ensured = True
    except Exception:
        pass


def _ensure_device_maintenance_columns():
    """为 devices 表添加 maintenance_start、maintenance_end 列（兼容旧 MariaDB 库）"""
    global _device_maintenance_columns_ensured
    if _device_maintenance_columns_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                if not _table_has_column(conn, 'devices', 'maintenance_start'):
                    conn.execute(text("ALTER TABLE devices ADD COLUMN maintenance_start VARCHAR(8)"))
                    conn.commit()
                if not _table_has_column(conn, 'devices', 'maintenance_end'):
                    conn.execute(text("ALTER TABLE devices ADD COLUMN maintenance_end VARCHAR(8)"))
                    conn.commit()
            _device_maintenance_columns_ensured = True
    except Exception:
        pass


def _ensure_device_ssh_port_column():
    """为 devices 表添加 ssh_port 列（兼容旧 MariaDB 库）"""
    global _device_ssh_port_column_ensured
    if _device_ssh_port_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                if not _table_has_column(conn, 'devices', 'ssh_port'):
                    conn.execute(text("ALTER TABLE devices ADD COLUMN ssh_port INTEGER"))
                    conn.commit()
            _device_ssh_port_column_ensured = True
    except Exception:
        pass


def _ensure_device_telnet_port_column():
    """为 devices 表添加 telnet_port 列（兼容旧 MariaDB 库）"""
    global _device_telnet_port_column_ensured
    if _device_telnet_port_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                if not _table_has_column(conn, 'devices', 'telnet_port'):
                    conn.execute(text("ALTER TABLE devices ADD COLUMN telnet_port INTEGER"))
                    conn.commit()
            _device_telnet_port_column_ensured = True
    except Exception:
        pass


def _ensure_user_allowed_groups_column():
    """为 users 表添加 allowed_groups 列（兼容旧 MariaDB 库）"""
    global _user_allowed_groups_column_ensured
    if _user_allowed_groups_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                if not _table_has_column(conn, 'users', 'allowed_groups'):
                    conn.execute(text("ALTER TABLE users ADD COLUMN allowed_groups VARCHAR(512)"))
                    conn.commit()
            _user_allowed_groups_column_ensured = True
    except Exception:
        pass


def _ensure_backup_job_run_type_column():
    """为 backup_job_runs 表添加 run_type 列（兼容旧 MariaDB 库）"""
    global _backup_job_run_type_column_ensured
    if _backup_job_run_type_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                if not _table_has_column(conn, 'backup_job_runs', 'run_type'):
                    conn.execute(text("ALTER TABLE backup_job_runs ADD COLUMN run_type VARCHAR(16) DEFAULT 'manual'"))
                    conn.commit()
            _backup_job_run_type_column_ensured = True
    except Exception:
        pass


def _ensure_backup_job_executor_column():
    """为 backup_job_runs 表添加 executor 列（兼容旧 MariaDB 库）"""
    global _backup_job_executor_column_ensured
    if _backup_job_executor_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            with db.engine.connect() as conn:
                if not _table_has_column(conn, 'backup_job_runs', 'executor'):
                    conn.execute(text("ALTER TABLE backup_job_runs ADD COLUMN executor VARCHAR(128) DEFAULT ''"))
                    conn.commit()
            _backup_job_executor_column_ensured = True
    except Exception:
        pass


# 确保目录存在
for d in [CONFIGS_DIR, LOG_DIR]:
    os.makedirs(d, exist_ok=True)

# 全局备份任务状态与任务列表（最近 N 次）
_backup_running = False
_backup_lock = threading.Lock()
_backup_jobs = []  # 最近任务，每项: id, start_time, total, done, ok, fail, status, end_time
_current_job = None
_MAX_BACKUP_JOBS = 200
# 跨进程备份锁（gunicorn 多 worker 时防止调度器与手动同时启动，确保 run_type 正确）
_backup_lock_fd = None
_BACKUP_LOCK_PATH = os.path.join(os.path.dirname(CONFIGS_DIR), 'backup.lock')

# 内置调度器（APScheduler）
_backup_scheduler = None
_backup_scheduler_lock = threading.Lock()



def _scheduled_backup_job():
    """调度器回调：在应用上下文中触发全量备份"""
    with app.app_context():
        try:
            with app.test_request_context():
                _ensure_tables()
                ok, _ = _start_full_backup(run_type='scheduled', executor='System')
                if ok:
                    app.logger.info('定时备份任务已启动')
        except Exception as e:
            app.logger.warning('定时备份任务启动失败: %s', e)


def _setup_backup_scheduler():
    """根据 backup_frequency 设置调度任务"""
    global _backup_scheduler
    with _backup_scheduler_lock:
        if _backup_scheduler is not None:
            try:
                _backup_scheduler.remove_all_jobs()
            except Exception:
                pass
        else:
            try:
                from apscheduler.schedulers.background import BackgroundScheduler
                from apscheduler.triggers.cron import CronTrigger
                _backup_scheduler = BackgroundScheduler()
                _backup_scheduler.start()
            except Exception as e:
                app.logger.warning('备份调度器启动失败: %s', e)
                return

        tz_name = _get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            try:
                import pytz
                tz = pytz.timezone(tz_name)
            except Exception:
                tz = None

        freq = (_get_setting('backup_frequency', 'none') or 'none').strip()
        if freq and freq != 'none':
            job_id = 'scheduled_backup'
            try:
                if freq == 'hourly':
                    _backup_scheduler.add_job(_scheduled_backup_job, CronTrigger(minute=0, timezone=tz), id=job_id)
                elif freq == 'daily':
                    _backup_scheduler.add_job(_scheduled_backup_job, CronTrigger(hour=2, minute=0, timezone=tz), id=job_id)
                elif freq == 'weekly':
                    _backup_scheduler.add_job(_scheduled_backup_job, CronTrigger(day_of_week='sun', hour=2, minute=0, timezone=tz), id=job_id)
                elif freq == 'twice_daily':
                    _backup_scheduler.add_job(_scheduled_backup_job, CronTrigger(hour='0,12', minute=0, timezone=tz), id=job_id)
                else:
                    parts = freq.split()
                    if len(parts) >= 5:
                        _backup_scheduler.add_job(_scheduled_backup_job, CronTrigger.from_crontab(freq, timezone=tz), id=job_id)
            except Exception as e:
                app.logger.warning('备份调度任务配置失败: %s', e)

        disc_freq = (_get_setting('discovery_frequency', 'none') or 'none').strip()
        if disc_freq == 'every_8_hours':
            disc_freq = 'twice_daily'
        if disc_freq and disc_freq != 'none':
            try:
                job_id = 'scheduled_discovery'
                if disc_freq == 'hourly':
                    _backup_scheduler.add_job(_scheduled_discovery_job, CronTrigger(minute=0, timezone=tz), id=job_id)
                elif disc_freq == 'twice_daily':
                    _backup_scheduler.add_job(_scheduled_discovery_job, CronTrigger(hour='0,12', minute=0, timezone=tz), id=job_id)
                elif disc_freq == 'daily':
                    _backup_scheduler.add_job(_scheduled_discovery_job, CronTrigger(hour=2, minute=0, timezone=tz), id=job_id)
                elif disc_freq == 'weekly':
                    _backup_scheduler.add_job(_scheduled_discovery_job, CronTrigger(day_of_week='sun', hour=2, minute=0, timezone=tz), id=job_id)
                else:
                    parts = disc_freq.split()
                    if len(parts) >= 5:
                        _backup_scheduler.add_job(_scheduled_discovery_job, CronTrigger.from_crontab(disc_freq, timezone=tz), id=job_id)
            except Exception as e:
                app.logger.warning('自动发现调度任务配置失败: %s', e)


def _scheduled_discovery_job():
    """定时执行：遍历已启用的自动发现规则并运行"""
    with app.app_context():
        _ensure_tables()
        rules = AutoDiscoveryRule.query.filter_by(enabled=True).all()
        for rule in rules:
            try:
                _execute_discovery_rule(rule.id)
            except Exception as e:
                app.logger.warning('定时自动发现规则 %s 执行失败: %s', rule.id, e)


def _reload_backup_schedule():
    """保存设置后重新加载调度"""
    with app.app_context():
        _ensure_tables()
        _setup_backup_scheduler()


def _start_scheduler_delayed():
    """延迟启动调度器（避免导入时阻塞）"""
    def _run():
        import time
        time.sleep(2)
        try:
            _reload_backup_schedule()
        except Exception as e:
            app.logger.warning('调度器初始化失败: %s', e)
    t = threading.Thread(target=_run, daemon=True)
    t.start()

# 登录失败锁定：key=(username 或 ip), value=(失败次数, 锁定截止时间 datetime)
_login_failures = {}
_login_failures_lock = threading.Lock()


@app.before_request
def require_login():
    """所有页面与 API 需登录后才能访问（登录页与静态资源除外）；支持 API Token 与会话超时"""
    path = request.path or '/'
    if (
        path.startswith('/static/')
        or path in ('/login', '/api/login')
        or path.startswith('/auth/')
        or path.startswith('/favicon')
    ):
        return

    # API Token 鉴权：Authorization: Bearer <token>，token 在设置中的 api_tokens 列表内则视为管理员
    if 'user' not in session and path.startswith('/api/') and request.authorization:
        auth = request.authorization
        if getattr(auth, 'type', None) == 'Bearer' and getattr(auth, 'token', None):
            raw = (_get_setting('api_tokens', '') or '').strip()
            tokens = [t.strip() for t in raw.split(',') if t.strip()]
            if auth.token in tokens:
                session['user'] = 'api'
                session['auth_source'] = 'api'
                session['role'] = 'admin'

    if 'user' not in session:
        if path.startswith('/api/'):
            return jsonify({'error': 'unauthorized'}), 401
        return redirect(url_for('auth.login_view', next=path))

    # 会话超时：无操作 N 分钟后退出
    try:
        timeout_min = int(_get_setting('session_timeout_minutes', '0') or '0')
        if timeout_min > 0 and session.get('last_activity'):
            from datetime import datetime as dt
            try:
                raw = session['last_activity'].replace('Z', '').strip()
                last = dt.fromisoformat(raw) if raw else None
            except Exception:
                last = None
            if last:
                now = datetime.utcnow()
                if (now - last).total_seconds() > timeout_min * 60:
                    session.clear()
                    if path.startswith('/api/'):
                        return jsonify({'error': 'session_expired'}), 401
                    return redirect(url_for('auth.login_view', next=path))
    except Exception:
        pass


@app.after_request
def _bump_session_activity(response):
    """已登录用户每次请求后更新 last_activity，用于会话超时"""
    if session.get('user') and request.path and not request.path.startswith('/static/'):
        session['last_activity'] = datetime.utcnow().isoformat() + 'Z'
    return response


def _get_setting(key: str, default: str = '') -> str:
    s = AppSetting.query.filter_by(key=key).first()
    return s.value if s else default


def _set_setting(key: str, value: str):
    s = AppSetting.query.filter_by(key=key).first()
    if s:
        s.value = value
    else:
        s = AppSetting(key=key, value=value)
        db.session.add(s)
    db.session.commit()


def _setting_has_secret_value(key: str) -> bool:
    """用于接口脱敏：判断某项密钥类设置是否已保存非空值。"""
    return bool((_get_setting(key, '') or '').strip())


def _webhook_body_for_url(url: str, text: str, extra: dict = None) -> bytes:
    """根据 Webhook URL 识别平台并生成对应请求体。支持：企业微信、钉钉、飞书、Slack、Discord、Teams 及通用 JSON。"""
    import json as _json
    u = url.lower()
    # 企业微信：msgtype + text.content
    if 'qyapi.weixin.qq.com' in u:
        return _json.dumps({'msgtype': 'text', 'text': {'content': text}}, ensure_ascii=False).encode('utf-8')
    # 钉钉：msgtype + text.content + at
    if 'oapi.dingtalk.com' in u:
        return _json.dumps({
            'msgtype': 'text',
            'text': {'content': text},
            'at': {'isAtAll': False},
        }, ensure_ascii=False).encode('utf-8')
    # 飞书：msg_type + content.text
    if 'open.feishu.cn' in u and '/bot/' in u:
        return _json.dumps({'msg_type': 'text', 'content': {'text': text}}, ensure_ascii=False).encode('utf-8')
    # Slack：text
    if 'hooks.slack.com' in u:
        return _json.dumps({'text': text}, ensure_ascii=False).encode('utf-8')
    # Discord：content（2k 字符限制）
    if 'discord.com/api/webhooks' in u or 'discordapp.com/api/webhooks' in u:
        return _json.dumps({'content': text[:2000]}, ensure_ascii=False).encode('utf-8')
    # Microsoft Teams：text 或 MessageCard
    if 'webhook.office.com' in u or 'outlook.office.com' in u:
        return _json.dumps({'text': text}, ensure_ascii=False).encode('utf-8')
    # 通用：合并 extra 与 message/body
    payload = dict(extra) if extra else {}
    payload.setdefault('message', text)
    payload.setdefault('body', text)
    payload.setdefault('text', text)
    return _json.dumps(payload, ensure_ascii=False).encode('utf-8')


def _call_webhook_with_retry(url: str, body: bytes, max_retries: int = 3, timeout: int = 10):
    """调用 Webhook，失败时重试并写日志。成功返回 True，失败返回 False。HTTPS 请求跳过证书校验以兼容自签名/内网证书。"""
    import urllib.request
    import time
    import ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    last_err = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': 'application/json; charset=utf-8'})
            urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx)
            return True
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(2)
    app.logger.warning('备份失败 Webhook 请求失败（已重试 %d 次）: %s', max_retries, last_err)
    return False


def _log_alert(event_type: str, channel: str, recipient: str, subject: str, content_summary: str, status: str, error: str = None):
    """写入告警发送日志。"""
    try:
        with app.app_context():
            _ensure_tables()
            log = AlertLog(
                event_type=event_type,
                channel=channel,
                recipient=(recipient or '')[:256],
                subject=(subject or '')[:256],
                content_summary=(content_summary or '')[:1024],
                status=status,
                error=(error or '')[:1024] if error else None,
            )
            db.session.add(log)
            db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _send_alert_email(to_list: list, subject: str, body: str) -> tuple:
    """发送邮件告警。返回 (success: bool, error: str|None)。"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    to_list = [t.strip() for t in to_list if t and str(t).strip()]
    if not to_list:
        return False, '未配置收件人'
    host = (_get_setting('alert_smtp_host', '') or '').strip()
    if not host:
        return False, '未配置 SMTP 服务器'
    port = int(_get_setting('alert_smtp_port', '587') or '587')
    user = (_get_setting('alert_smtp_user', '') or '').strip()
    password = (_get_setting('alert_smtp_password', '') or '').strip()
    from_addr = (_get_setting('alert_smtp_from', '') or '').strip() or user
    use_tls = (_get_setting('alert_smtp_use_tls', '1') or '1') == '1'
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_addr
        msg['To'] = ', '.join(to_list)
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(from_addr, to_list, msg.as_string())
        return True, None
    except Exception as e:
        return False, str(e)


def _maybe_send_alerts(event_type: str, subject: str, body: str, extra: dict = None):
    """根据告警设置，向邮箱/Webhook 发送告警并记录日志。event_type: backup_failure | discovery_new。"""
    try:
        with app.app_context():
            _ensure_tables()
            # 备份失败
            if event_type == 'backup_failure':
                send_email = (_get_setting('alert_on_backup_fail_email', '0') or '0') == '1'
                send_webhook = (_get_setting('alert_on_backup_fail_webhook', '1') or '1') == '1'
            # 自动发现新增
            elif event_type == 'discovery_new':
                send_email = (_get_setting('alert_on_discovery_new_email', '0') or '0') == '1'
                send_webhook = (_get_setting('alert_on_discovery_new_webhook', '0') or '0') == '1'
            else:
                return
            # 邮箱
            if send_email:
                to_str = (_get_setting('alert_email_to', '') or '').strip()
                to_list = [x.strip() for x in to_str.split(',') if x.strip()]
                if to_list:
                    ok, err = _send_alert_email(to_list, subject, body)
                    _log_alert(event_type, 'email', ','.join(to_list), subject, body[:200], 'success' if ok else 'failed', err)
                    if not ok:
                        app.logger.warning('告警邮件发送失败: %s', err)
            # Webhook
            if send_webhook:
                url = (_get_setting('alert_webhook_url', '') or '').strip()
                if url and url.startswith(('http://', 'https://')):
                    webhook_body = _webhook_body_for_url(url, body, extra or {})
                    ok = _call_webhook_with_retry(url, webhook_body, max_retries=3)
                    _log_alert(event_type, 'webhook', url[:80], '', body[:200], 'success' if ok else 'failed', None if ok else '请求失败')
    except Exception as e:
        app.logger.warning('告警发送异常: %s', e)


def _get_default_settings():
    """返回系统默认参数（与 config 及界面默认一致）"""
    return {
        'username': '',
        'password': '',
        'system_name': '配置备份中心',
        # 自动备份频率：默认每天凌晨 02:00
        'backup_frequency': 'daily',
        # 默认连接方式：SSH
        'default_connection_type': (DEFAULT_CONNECTION_TYPE or 'SSH').upper(),
        'backup_retention_days': str(BACKUP_RETENTION_DAYS),
        'timezone': DEFAULT_TIMEZONE or 'Asia/Shanghai',
        'footer_text': '',
        'logo_file': '',
        'session_timeout_minutes': '0',
        'login_lockout_attempts': '0',
        'login_lockout_minutes': '15',
        'password_min_length': '6',
        'password_require_digit': '0',
        'password_require_upper': '0',
        'password_require_lower': '0',
        'password_require_special': '0',
        'device_per_page_default': '50',
        'log_per_page_default': '50',
        'backup_timeout_seconds': '30',
        # 配置输出等待时间（秒）：发备份命令后等待设备输出的最长时间，所有设备类型共用
        'backup_read_timeout_seconds': '30',
        # 备份并发线程数：默认 5
        'backup_thread_num': str(BACKUP_THREAD_NUM),
        # 连接失败是否自动尝试备用方式（如默认 SSH 失败时再尝试 Telnet）
        'backup_connection_fallback': '0',
        'ssh_port': str(SSH_PORT),
        'telnet_port': '23',
        'alert_smtp_host': '',
        'alert_smtp_port': '587',
        'alert_smtp_user': '',
        'alert_smtp_password': '',
        'alert_smtp_from': '',
        'alert_smtp_use_tls': '1',
        'alert_email_to': '',
        'alert_on_backup_fail_email': '0',
        'alert_on_backup_fail_webhook': '1',
        'alert_on_discovery_new_email': '0',
        'alert_on_discovery_new_webhook': '0',
        'alert_webhook_url': '',
        'api_tokens': '',
        'device_groups': '',  # 预定义分组名，逗号分隔
        'ldap_enabled': '0',
        'ldap_server': '',
        'ldap_base_dn': '',
        'ldap_bind_dn': '',
        'ldap_bind_password': '',
        'ldap_user_filter': '(uid={username})',
        # 自动发现 / SNMP
        'snmp_version': '2c',
        'snmp_community': 'public',
        'snmp_timeout_ms': '2000',
        'snmp_retries': '1',
        # 自动发现频率：默认每 12 小时（0 点和 12 点）
        'discovery_frequency': 'twice_daily',
        # 自动发现添加设备唯一性：hostname | ip，默认 hostname
        'discovery_unique_by': 'hostname',
        # 系统界面语言：zh | en，默认 zh
        'language': 'zh',
    }


def _write_audit(action: str, resource_type: str = '', resource_id: str = '', detail: str = ''):
    """写入一条操作审计日志（失败时静默回滚，不影响主流程）"""
    try:
        username = session.get('user') or 'anonymous'
        auth_source = session.get('auth_source') or 'local'
        log = AuditLog(
            username=str(username)[:128],
            auth_source=str(auth_source)[:32],
            action=str(action or '')[:64],
            resource_type=str(resource_type or '')[:64],
            resource_id=str(resource_id or '')[:128],
            detail=str(detail or '')[:512],
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

def _current_username() -> str:
    return session.get('user') or ''


def _current_auth_source() -> str:
    return session.get('auth_source') or 'local'


def _ensure_user_record(username: str, auth_source: str = 'local'):
    """确保登录成功的用户在 users 表中有一条记录，并根据情况自动赋予默认角色。

    - 本地全局用户名第一次登录时自动赋予 admin 角色
    - 其他用户默认为 viewer，后续由管理员在「用户管理」中调整为 ops / admin
    """
    if not username:
        return
    try:
        u = User.query.filter_by(username=username).first()
        admin_user = _get_setting('username', DEFAULT_USERNAME)
        is_admin_default = (auth_source == 'local' and username == admin_user)
        if u is None:
            role = 'admin' if is_admin_default else 'viewer'
            u = User(
                username=str(username)[:128],
                display_name=str(username)[:128],
                source=str(auth_source or 'local')[:32],
                role=role,
                is_active=True,
            )
            db.session.add(u)
        else:
            # 若之前未设置角色，则根据当前登录方式补全一个合理默认值；旧角色名在这里同步归一。
            normalized_role = normalize_user_role(u.role)
            if not (u.role or '').strip():
                u.role = 'admin' if is_admin_default else 'viewer'
            elif u.role != normalized_role:
                u.role = normalized_role
            # 更新来源与显示名（仅在为空时）
            if not (u.source or '').strip():
                u.source = str(auth_source or 'local')[:32]
            if not (u.display_name or '').strip():
                u.display_name = str(username)[:128]
            # 被禁用用户仍允许展示，但不在此处强行改为启用
        db.session.commit()
        session['role'] = normalize_user_role(u.role)
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _is_device_in_maintenance(device) -> bool:
    """设备是否处于维护窗口内（按系统时区当前时间与 device.maintenance_start/end 比较）"""
    start_s = (device.maintenance_start or '').strip()
    end_s = (device.maintenance_end or '').strip()
    if not start_s or not end_s:
        return False
    try:
        tz_name = _get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
        ZoneInfo = _get_zoneinfo()
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz).time()
        parts_s = start_s.split(':')
        parts_e = end_s.split(':')
        start_t = datetime.strptime(start_s[:5], '%H:%M').time() if len(parts_s) >= 2 else None
        end_t = datetime.strptime(end_s[:5], '%H:%M').time() if len(parts_e) >= 2 else None
        if start_t is None or end_t is None:
            return False
        if start_t <= end_t:
            return start_t <= now <= end_t
        return now >= start_t or now <= end_t  # 跨日窗口
    except Exception:
        return False


def _current_user_allowed_groups():
    """当前用户可管理的设备分组，None 表示不限制（全部），否则为分组名列表"""
    if _current_auth_source() == 'api':
        return None
    username = _current_username()
    if not username:
        return None
    try:
        u = User.query.filter_by(username=username).first()
        if not u or not (u.allowed_groups or '').strip():
            return None
        return [g.strip() for g in (u.allowed_groups or '').split(',') if g.strip()]
    except Exception:
        return None


def _current_role() -> str:
    """当前登录用户的角色：admin / ops / viewer"""
    raw_role = session.get('role')
    if raw_role:
        role = normalize_user_role(raw_role)
        session['role'] = role
        return role
    username = _current_username()
    if not username:
        return 'viewer'
    try:
        u = User.query.filter_by(username=username).first()
        if not u:
            return 'viewer'
        session['role'] = normalize_user_role(u.role)
        return session['role']
    except Exception:
        return 'viewer'


def _is_admin() -> bool:
    return _current_role() == 'admin'


def _can_run_backup() -> bool:
    """允许管理员与运维用户执行备份"""
    return _current_role() in ('admin', 'ops')


def _can_edit_settings() -> bool:
    """仅允许使用全局用户名且通过本地账号登录的用户（管理员）或 API Token 修改设置"""
    auth_source = _current_auth_source()
    if auth_source == 'api':
        return True
    username = _current_username()
    if not username or auth_source != 'local':
        return False
    # 优先依据角色判断
    if _is_admin():
        return True
    # 兼容旧数据：当 users 表中尚未初始化时，退回到「等于全局用户名」的判断
    admin_user = _get_setting('username', DEFAULT_USERNAME)
    return username == admin_user


def _cleanup_old_backups():
    """删除超过保留天数的备份文件及对应日志"""
    try:
        days = int(_get_setting('backup_retention_days', str(BACKUP_RETENTION_DAYS)) or '0')
        if days <= 0:
            return
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted_files = 0
        if os.path.exists(CONFIGS_DIR):
            for prefix in os.listdir(CONFIGS_DIR):
                pdir = os.path.join(CONFIGS_DIR, prefix)
                if not os.path.isdir(pdir):
                    continue
                for host in os.listdir(pdir):
                    hdir = os.path.join(pdir, host)
                    if not os.path.isdir(hdir):
                        continue
                    for f in os.listdir(hdir):
                        if not f.endswith('.txt'):
                            continue
                        path = os.path.join(hdir, f)
                        if os.path.getmtime(path) < cutoff.timestamp():
                            try:
                                os.unlink(path)
                                deleted_files += 1
                            except OSError:
                                pass
        with app.app_context():
            BackupLog.query.filter(BackupLog.created_at < cutoff).delete(synchronize_session=False)
            db.session.commit()
    except Exception:
        pass


def _push_config_via_ssh(ip: str, username: str, password: str, commands: str, ssh_port: int = SSH_PORT):
    """通过 SSH 登录设备并逐行下发配置命令，返回 (ok, message, output_snippet, duration_seconds)"""
    start = datetime.utcnow()
    try:
        import paramiko
    except ImportError:
        return False, '服务器未安装 paramiko，无法通过 SSH 下发配置。', '', None

    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=ip,
            port=ssh_port,
            username=username,
            password=password,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )
        channel = client.invoke_shell(width=256)
        # 丢弃初始 banner / 提示符
        try:
            channel.recv(65535)
        except Exception:
            pass

        def send_line(line: str):
            if not line:
                return
            channel.send(line + '\r\n')

        # 逐行下发命令（忽略空行）
        for raw in (commands or '').splitlines():
            line = (raw or '').rstrip('\r\n')
            if not line.strip():
                continue
            send_line(line)

        # 简单等待一段时间收集输出
        output = b''
        for _ in range(20):
            if channel.recv_ready():
                try:
                    chunk = channel.recv(65535)
                except Exception:
                    break
                if not chunk:
                    break
                output += chunk
            else:
                # 若已经有一定输出且短时间内无新数据，则认为结束
                if output:
                    break
                threading.Event().wait(0.4)

        end = datetime.utcnow()
        duration = int((end - start).total_seconds())
        text = output.decode('utf-8', errors='replace') if output else ''
        # 仅返回前若干行，避免过长
        lines = text.splitlines()
        snippet = '\n'.join(lines[:30])
        return True, '配置下发命令已通过 SSH 发送完成。', snippet, duration
    except Exception as e:
        end = datetime.utcnow()
        duration = int((end - start).total_seconds())
        msg = str(e) or 'SSH 下发配置失败'
        return False, msg, '', duration
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


def _login_lockout_key():
    """登录锁定 key：用户名 + IP，避免单 IP 锁死所有用户"""
    username = (request.get_json(force=True, silent=True) or {}).get('username') or ''
    return (str(username).strip() or '') + '|' + (request.remote_addr or '')


def _check_login_locked():
    """若当前 key 处于锁定期则返回 (True, 剩余分钟)，否则 (False, 0)"""
    try:
        attempts = int(_get_setting('login_lockout_attempts', '0') or '0')
        if attempts <= 0:
            return False, 0
    except (TypeError, ValueError):
        return False, 0
    key = _login_lockout_key()
    with _login_failures_lock:
        val = _login_failures.get(key)
        if not val:
            return False, 0
        count, lock_until = val
        now = datetime.utcnow()
        if lock_until and now < lock_until:
            remain = max(1, int((lock_until - now).total_seconds() / 60))
            return True, remain
        if lock_until and now >= lock_until:
            _login_failures.pop(key, None)
    return False, 0


def _login_fail_record():
    """记录一次登录失败，可能设置锁定期"""
    key = _login_lockout_key()
    with _login_failures_lock:
        val = _login_failures.get(key) or (0, None)
        count, lock_until = val
        if lock_until and datetime.utcnow() < lock_until:
            return
        count += 1
        try:
            attempts = int(_get_setting('login_lockout_attempts', '0') or '0')
            lock_min = int(_get_setting('login_lockout_minutes', '15') or '15')
            if attempts > 0 and count >= attempts and lock_min > 0:
                _login_failures[key] = (count, datetime.utcnow() + timedelta(minutes=lock_min))
            else:
                _login_failures[key] = (count, None)
        except (TypeError, ValueError):
            _login_failures[key] = (count, None)


def _login_fail_clear():
    """登录成功后清除该 key 的失败记录"""
    key = _login_lockout_key()
    with _login_failures_lock:
        _login_failures.pop(key, None)


def _check_password_policy(raw_password: str) -> tuple:
    """校验密码复杂度，返回 (True, '') 或 (False, 错误信息)"""
    if not raw_password:
        return False, '密码不能为空'
    pwd = str(raw_password)
    try:
        min_len = int(_get_setting('password_min_length', '6') or '0')
        if min_len > 0 and len(pwd) < min_len:
            return False, f'密码至少需要 {min_len} 个字符'
    except (TypeError, ValueError):
        pass
    if _get_setting('password_require_digit', '0') == '1' and not any(c.isdigit() for c in pwd):
        return False, '密码须包含数字'
    if _get_setting('password_require_upper', '0') == '1' and not any(c.isupper() for c in pwd):
        return False, '密码须包含大写字母'
    if _get_setting('password_require_lower', '0') == '1' and not any(c.islower() for c in pwd):
        return False, '密码须包含小写字母'
    if _get_setting('password_require_special', '0') == '1':
        if not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?/~`' for c in pwd):
            return False, '密码须包含特殊字符'
    return True, ''


# 备份失败原因规范化（行业通用英文），用于告警正文
def _normalize_backup_failure_reason(status, message):
    """将 status + message 映射为统一英文失败原因。"""
    status = (status or '').strip()
    msg = (message or '').strip().lower()
    if status == 'Fail_Network':
        return 'Network unreachable (ICMP/timeout)'
    if status == 'Fail_Login':
        return 'Authentication failed (invalid credentials)'
    if status == 'Fail' or status:
        if 'refused' in msg or 'connection refused' in msg:
            return 'Connection refused'
        if 'timeout' in msg or 'timed out' in msg:
            return 'Connection or operation timeout'
        if 'port' in msg and ('unreachable' in msg or 'refused' in msg or 'not open' in msg):
            return 'Port unreachable'
        if 'unreachable' in msg or 'no route' in msg or 'host is unreachable' in msg:
            return 'Network unreachable (ICMP/timeout)'
        if 'auth' in msg or 'password' in msg or 'login' in msg or 'credential' in msg or 'permission denied' in msg:
            return 'Authentication failed (invalid credentials)'
        return 'Other error (abnormal exit)'
    return 'Other error (abnormal exit)'


def _log_callback(ip, hostname, dev_type, status, message, duration, config_path=None):
    with app.app_context():
        rel_path = None
        if config_path and config_path.startswith(CONFIGS_DIR):
            rel_path = config_path[len(CONFIGS_DIR):].lstrip('/')
        log = BackupLog(
            ip=ip, hostname=hostname, device_type=dev_type,
            status=status, message=message or '', duration_seconds=duration,
            config_path=rel_path,
        )
        db.session.add(log)
        db.session.commit()
    global _current_job
    with _backup_lock:
        if _current_job is not None:
            _current_job['done'] = _current_job.get('done', 0) + 1
            if status == 'OK':
                _current_job['ok'] = _current_job.get('ok', 0) + 1
            else:
                _current_job['fail'] = _current_job.get('fail', 0) + 1
    with _backup_lock:
        pass  # 可在此广播进度


# ---------- 仪表盘 / 可观测 ----------
def _get_zoneinfo():
    """获取 ZoneInfo，优先标准库，否则 backports.zoneinfo"""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo
    except ImportError:
        try:
            from backports.zoneinfo import ZoneInfo
            return ZoneInfo
        except ImportError:
            return None


def _now_in_tz(tz_name=None):
    """返回当前时间在设定时区的 datetime（用于「今日」与趋势按用户时区计算）"""
    from datetime import timezone as dt_timezone
    ZoneInfo = _get_zoneinfo()
    if ZoneInfo is None:
        return datetime.utcnow()
    tz_name = tz_name or _get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.now(tz)


def _tz_today_utc_range(tz_name=None):
    """返回设定时区「今日」对应的 UTC 起止时间（naive UTC，用于与 DB 比较）"""
    from datetime import timezone as dt_timezone
    if _get_zoneinfo() is None:
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return today_start, today_start + timedelta(days=1)
    now_tz = _now_in_tz(tz_name)
    today_start_tz = now_tz.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end_tz = today_start_tz + timedelta(days=1)
    start_utc = today_start_tz.astimezone(dt_timezone.utc).replace(tzinfo=None)
    end_utc = today_end_tz.astimezone(dt_timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


@app.route('/api/dashboard')
def dashboard():
    """仪表盘数据：统计、趋势、最近活动、告警（今日与趋势按设置时区计算）"""
    try:
        _ensure_tables()
    except Exception:
        pass
    try:
        return _dashboard_data()
    except Exception as e:
        return jsonify({
            'total_devices': 0,
            'enabled_devices': 0,
            'backup_running': _backup_running,
            'today_ok': 0,
            'today_fail': 0,
            'no_backup_24h': 0,
            'trend': [],
            'recent_logs': [],
            'last_backup_time': None,
            'client_ip': request.remote_addr or '',
            'timezone': DEFAULT_TIMEZONE,
        }), 200


def _dashboard_data():
    from datetime import timezone as dt_timezone
    from sqlalchemy import func

    timezone = _get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    now_utc = datetime.utcnow()
    last_24h = now_utc - timedelta(hours=24)
    last_7d = now_utc - timedelta(days=7)
    today_start_utc, today_end_utc = _tz_today_utc_range(timezone)

    total_devices = Device.query.count()
    enabled_devices = Device.query.filter_by(enabled=True).count()

    # 今日备份成功/失败数（今日 = 设定时区的当天）
    today_ok = BackupLog.query.filter(
        BackupLog.created_at >= today_start_utc,
        BackupLog.created_at < today_end_utc,
        BackupLog.status == 'OK',
    ).count()
    today_fail = BackupLog.query.filter(
        BackupLog.created_at >= today_start_utc,
        BackupLog.created_at < today_end_utc,
        BackupLog.status != 'OK',
    ).count()

    # 最近 24h 有成功备份的 hostname 集合
    recent_ok_hosts = {
        r[0] for r in
        BackupLog.query.filter(
            BackupLog.created_at >= last_24h,
            BackupLog.status == 'OK',
        ).with_entities(BackupLog.hostname).distinct().all()
    }
    enabled_hosts = {
        r[0] for r in Device.query.filter_by(enabled=True).with_entities(Device.hostname).all()
    }
    no_backup_24h = len(enabled_hosts - recent_ok_hosts)  # 已启用但 24h 内无成功备份

    # 近 7 天无成功备份的设备（风险更高的一批）
    ok_7d_hosts = {
        r[0] for r in
        BackupLog.query.filter(
            BackupLog.created_at >= last_7d,
            BackupLog.status == 'OK',
        ).with_entities(BackupLog.hostname).distinct().all()
    }
    no_backup_7d_hosts = sorted(enabled_hosts - ok_7d_hosts)
    no_backup_7d = len(no_backup_7d_hosts)

    # 最近 7 天内备份失败的设备（每设备取最新一条失败记录）
    fail_logs = (
        BackupLog.query
        .filter(
            BackupLog.created_at >= last_7d,
            BackupLog.status != 'OK',
        )
        .order_by(BackupLog.created_at.desc())
        .all()
    )
    seen_hosts = set()
    recent_fail_devices = []
    for log in fail_logs:
        h = (log.hostname or '').strip()
        if not h or h in seen_hosts:
            continue
        seen_hosts.add(h)
        status_full = (log.status or 'Fail').replace('_', ' ')
        if log.message:
            status_full = status_full + ': ' + (log.message[:60] + '...' if len(log.message or '') > 60 else (log.message or ''))
        recent_fail_devices.append({
            'hostname': h,
            'ip': (log.ip or '').strip(),
            'status': 'Fail',  # 短显示
            'status_full': status_full,  # 鼠标悬停显示完整原因
            'created_at': _isoformat_utc(log.created_at) if log.created_at else None,
        })

    # 设备类型分布
    type_rows = (
        Device.query
        .with_entities(Device.device_type, func.count(Device.id))
        .group_by(Device.device_type)
        .all()
    )
    type_distribution = [
        {'device_type': (t or ''), 'count': int(c or 0)}
        for (t, c) in type_rows
    ]

    # 最近 7 天趋势（按设定时区的「天」汇总）
    trend = []
    ZoneInfo = _get_zoneinfo()
    if ZoneInfo:
        try:
            tz = ZoneInfo(timezone)
            now_tz = datetime.now(tz)
            today_start_tz = now_tz.replace(hour=0, minute=0, second=0, microsecond=0)
            for i in range(6, -1, -1):
                day_start_tz = today_start_tz - timedelta(days=i)
                day_end_tz = day_start_tz + timedelta(days=1)
                day_start_utc = day_start_tz.astimezone(dt_timezone.utc).replace(tzinfo=None)
                day_end_utc = day_end_tz.astimezone(dt_timezone.utc).replace(tzinfo=None)
                ok = BackupLog.query.filter(
                    BackupLog.created_at >= day_start_utc,
                    BackupLog.created_at < day_end_utc,
                    BackupLog.status == 'OK',
                ).count()
                fail = BackupLog.query.filter(
                    BackupLog.created_at >= day_start_utc,
                    BackupLog.created_at < day_end_utc,
                    BackupLog.status != 'OK',
                ).count()
                trend.append({
                    'date': day_start_tz.strftime('%m-%d'),
                    'ok': ok,
                    'fail': fail,
                })
        except Exception:
            pass
    if not trend:
        # 回退为 UTC 当日
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(6, -1, -1):
            day_start = today_start - timedelta(days=i)
            day_end = day_start + timedelta(days=1)
            ok = BackupLog.query.filter(
                BackupLog.created_at >= day_start,
                BackupLog.created_at < day_end,
                BackupLog.status == 'OK',
            ).count()
            fail = BackupLog.query.filter(
                BackupLog.created_at >= day_start,
                BackupLog.created_at < day_end,
                BackupLog.status != 'OK',
            ).count()
            trend.append({'date': day_start.strftime('%m-%d'), 'ok': ok, 'fail': fail})

    # 最近 15 条备份日志
    recent_logs = BackupLog.query.order_by(BackupLog.created_at.desc()).limit(15).all()

    # 最近 10 次用户登录
    recent_logins = LoginLog.query.order_by(LoginLog.created_at.desc()).limit(10).all()

    # 最近 10 条审计日志（敏感操作）
    recent_audits = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(10).all()

    # 最近一次全量备份时间（取最近一条 OK 的 created_at，仅作参考）
    last_ok = BackupLog.query.filter_by(status='OK').order_by(BackupLog.created_at.desc()).first()

    client_ip = request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr or ''))
    if isinstance(client_ip, str) and ',' in client_ip:
        client_ip = client_ip.split(',')[0].strip()

    # 按类型 24h 备份成功率（仅统计已启用设备；分权用户仅统计其分组）
    enabled_devs = Device.query.filter_by(enabled=True).all()
    dash_allowed = _current_user_allowed_groups()
    if dash_allowed is not None:
        enabled_devs = [d for d in enabled_devs if (d.device_group or '').strip() in dash_allowed or (not (d.device_group or '').strip() and '（未分组）' in dash_allowed)]
    success_rate_by_type = []
    for item in type_distribution:
        dev_type = item.get('device_type') or ''
        total = sum(1 for d in enabled_devs if (d.device_type or '') == dev_type)
        ok_24h = sum(1 for d in enabled_devs if (d.device_type or '') == dev_type and (d.hostname or '') in recent_ok_hosts)
        rate_pct = round(100.0 * ok_24h / total, 1) if total else 100.0
        success_rate_by_type.append({'device_type': dev_type, 'total': total, 'ok_24h': ok_24h, 'rate_pct': rate_pct})
    success_rate_by_type = [x for x in success_rate_by_type if x['total'] > 0]
    success_rate_by_type.sort(key=lambda x: (x['device_type'] or ''))

    # 按分组 24h 备份成功率
    groups_seen = set()
    for d in enabled_devs:
        g = (d.device_group or '').strip() or '（未分组）'
        groups_seen.add(g)
    success_rate_by_group = []
    for g in sorted(groups_seen):
        total = sum(1 for d in enabled_devs if ((d.device_group or '').strip() or '（未分组）') == g)
        ok_24h = sum(1 for d in enabled_devs if ((d.device_group or '').strip() or '（未分组）') == g and (d.hostname or '') in recent_ok_hosts)
        rate_pct = round(100.0 * ok_24h / total, 1) if total else 100.0
        success_rate_by_group.append({'group': g, 'total': total, 'ok_24h': ok_24h, 'rate_pct': rate_pct})

    # 24h SLA 达标率（已启用设备中有成功备份的比例）
    sla_24h_ratio = round(100.0 * len(recent_ok_hosts & enabled_hosts) / len(enabled_hosts), 1) if enabled_hosts else 100.0

    # 24h 未备份设备列表（用于导出）
    no_backup_24h_hosts_set = enabled_hosts - recent_ok_hosts
    no_backup_24h_list = [
        {'hostname': d.hostname, 'ip': d.ip, 'device_type': d.device_type or '', 'group': (d.device_group or '').strip() or ''}
        for d in enabled_devs if (d.hostname or '') in no_backup_24h_hosts_set
    ]
    no_backup_24h_list.sort(key=lambda x: (x['hostname'] or '', x['ip'] or ''))

    # 系统配置概览（给仪表盘展示用）
    settings_summary = {
        'timezone': timezone,
        'default_connection_type': _get_setting('default_connection_type', DEFAULT_CONNECTION_TYPE).upper() or DEFAULT_CONNECTION_TYPE,
        'backup_frequency': _get_setting('backup_frequency', 'none') or 'none',
        'ldap_enabled': _get_setting('ldap_enabled', '0') == '1',
        'device_per_page_default': _get_setting('device_per_page_default', '50') or '50',
        'log_per_page_default': _get_setting('log_per_page_default', '50') or '50',
    }

    return jsonify({
        'total_devices': total_devices,
        'enabled_devices': enabled_devices,
        'backup_running': _backup_running,
        'today_ok': today_ok,
        'today_fail': today_fail,
        'no_backup_24h': no_backup_24h,
        'no_backup_7d': no_backup_7d,
        'no_backup_7d_hosts': no_backup_7d_hosts[:20],  # 仅给前端展示前 20 个
        'sla_24h_ratio': sla_24h_ratio,
        'success_rate_by_type': success_rate_by_type,
        'success_rate_by_group': success_rate_by_group,
        'no_backup_24h_list': no_backup_24h_list,
        'trend': trend,
        'recent_logs': [x.to_dict() for x in recent_logs],
        'recent_logins': [x.to_dict() for x in recent_logins],
        'recent_audits': [x.to_dict() for x in recent_audits],
        'last_backup_time': _isoformat_utc(last_ok.created_at) if last_ok else None,
        'client_ip': client_ip or '',
        'timezone': timezone,
        'type_distribution': type_distribution,
        'recent_fail_devices': recent_fail_devices,
        'settings_summary': settings_summary,
        'can_run_backup': _can_run_backup(),
        'can_edit_settings': _can_edit_settings(),
    })


def _iter_ip_ranges(raw: str, limit: int = 256):
    """解析多行 IP/IP段/CIDR，yield 最多 limit 个 IP 字符串。"""
    import ipaddress
    ips = []
    for line in (raw or '').splitlines():
        r = line.strip()
        if not r:
            continue
        try:
            if '-' in r:
                a, b = r.split('-', 1)
                a, b = a.strip(), b.strip()
                start = int(ipaddress.ip_address(a))
                end = int(ipaddress.ip_address(b))
                for i in range(start, min(end + 1, start + limit)):
                    ips.append(str(ipaddress.ip_address(i)))
            else:
                n = ipaddress.ip_network(r, strict=False)
                for addr in list(n.hosts())[:limit]:
                    ips.append(str(addr))
        except Exception:
            # 忽略非法行
            continue
        if len(ips) >= limit:
            break
    # 去重
    for ip in dict.fromkeys(ips):
        yield ip


@app.route('/api/discovery/settings', methods=['GET', 'PUT'])
def discovery_settings():
    """自动发现 / SNMP 全局设置。GET 允许所有登录用户（只读用户可见）；PUT 仅管理员。"""
    if request.method == 'PUT' and not _can_edit_settings():
        return jsonify({'error': '当前账号无权修改设置，请使用管理员账号登录。'}), 403
    if request.method == 'GET':
        return jsonify({
            'snmp_version': _get_setting('snmp_version', '2c'),
            'snmp_community': _get_setting('snmp_community', 'public'),
            'snmp_timeout_ms': _get_setting('snmp_timeout_ms', '2000'),
            'snmp_retries': _get_setting('snmp_retries', '1'),
            'can_edit_settings': _can_edit_settings(),
        })
    data = request.get_json(force=True, silent=True) or {}
    version = (data.get('snmp_version') or '2c').strip()
    if version not in ('1', '2c', '3'):
        version = '2c'
    _set_setting('snmp_version', version)
    _set_setting('snmp_community', (data.get('snmp_community') or 'public').strip())
    _set_setting('snmp_timeout_ms', str(int(data.get('snmp_timeout_ms') or '2000')))
    _set_setting('snmp_retries', str(int(data.get('snmp_retries') or '1')))
    return jsonify({'ok': True})


@app.route('/api/discovery/rules', methods=['GET', 'POST'])
def discovery_rules():
    """自动发现规则列表 & 新建。GET 允许所有登录用户（只读用户可见）；POST 仅管理员。"""
    if request.method == 'POST' and not _can_edit_settings():
        return jsonify({'error': '当前账号无权操作，请使用管理员账号登录。'}), 403
    if request.method == 'GET':
        rules = AutoDiscoveryRule.query.order_by(AutoDiscoveryRule.id.desc()).all()
        # 若系统尚未配置任何自动发现规则，则自动创建一条默认规则：
        # 名称为 Network，IP 范围 100.64.0.0/24，无分组，使用默认主机名/设备类型 OID。
        if not rules:
            try:
                default_rule = AutoDiscoveryRule(
                    name='Network',
                    ip_range='100.64.0.0/24',
                    snmp_community=None,
                    hostname_oid='1.3.6.1.2.1.1.5.0',
                    device_type_oid='1.3.6.1.2.1.1.1.0',
                    device_group=None,
                    enabled=False,
                )
                db.session.add(default_rule)
                db.session.commit()
                rules = AutoDiscoveryRule.query.order_by(AutoDiscoveryRule.id.desc()).all()
            except Exception:
                db.session.rollback()
        items = []
        for r in rules:
            d = r.to_dict()
            # 若未单独配置设备类型 OID，则在列表展示中采用兜底默认值，方便用户查看规则实际使用的 OID
            if not (d.get('device_type_oid') or '').strip():
                d['device_type_oid'] = '1.3.6.1.2.1.1.1.0'  # sysDescr 作为默认设备类型 OID
            items.append(d)
        return jsonify({'rules': items, 'can_edit_settings': _can_edit_settings()})
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get('name') or '').strip()
    ip_range = (data.get('ip_range') or '').strip()
    if not name or not ip_range:
        return jsonify({'error': '规则名称和 IP 范围不能为空。'}), 400
    rule = AutoDiscoveryRule(
        name=name,
        ip_range=ip_range,
        snmp_community=(data.get('snmp_community') or '').strip() or None,
        hostname_oid=(data.get('hostname_oid') or '').strip() or '1.3.6.1.2.1.1.5.0',
        # 若未填写设备类型 OID，默认使用 sysDescr（1.3.6.1.2.1.1.1.0），便于根据描述字符串做关键字匹配
        device_type_oid=(data.get('device_type_oid') or '').strip() or '1.3.6.1.2.1.1.1.0',
        device_group=(data.get('device_group') or '').strip() or None,
        enabled=bool(data.get('enabled', True)),
    )
    db.session.add(rule)
    db.session.commit()
    return jsonify({'ok': True, 'rule': rule.to_dict()})


@app.route('/api/discovery/rules/<int:rule_id>', methods=['PUT', 'DELETE'])
def discovery_rule_detail(rule_id):
    """编辑 / 删除 自动发现规则。"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权操作，请使用管理员账号登录。'}), 403
    rule = AutoDiscoveryRule.query.get_or_404(rule_id)
    if request.method == 'DELETE':
        db.session.delete(rule)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get('name') or '').strip()
    ip_range = (data.get('ip_range') or '').strip()
    if name:
        rule.name = name
    if ip_range:
        rule.ip_range = ip_range
    rule.snmp_community = (data.get('snmp_community') or '').strip() or rule.snmp_community
    hostname_oid = (data.get('hostname_oid') or '').strip()
    if hostname_oid:
        rule.hostname_oid = hostname_oid
    device_type_oid = (data.get('device_type_oid') or '').strip()
    if device_type_oid:
        rule.device_type_oid = device_type_oid
    if 'device_group' in data:
        rule.device_group = (data.get('device_group') or '').strip() or None
    if 'enabled' in data:
        rule.enabled = bool(data.get('enabled'))
    db.session.commit()
    return jsonify({'ok': True, 'rule': rule.to_dict()})


def _snmp_get(ip: str, oid: str, community: str, timeout_ms: int, retries: int, snmp_version: str = '2c'):
    """简单 SNMP GET，返回字符串或 None。

    snmp_version: '1' 或 '2c'，用于选择 v1/v2c。

    为兼容 Python 3.12 及 pysnmp 7.x，这里优先使用 v3arch.asyncio 的 get_cmd，
    若导入失败则回退到旧版 pysnmp 4.x 的同步 getCmd。"""
    # 优先尝试 pysnmp 7.x v3arch.asyncio API
    try:
        import asyncio
        from pysnmp.hlapi.v3arch.asyncio import (
            SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
            ObjectType, ObjectIdentity, get_cmd,
        )

        async def _do():
            engine = SnmpEngine()
            try:
                ver = (snmp_version or '2c').strip()
                mp_model = 0 if ver == '1' else 1  # '2c' 及其他视为 v2c
                transport = await UdpTransportTarget.create((ip, 161), timeout=timeout_ms / 1000.0, retries=retries)
                errorIndication, errorStatus, errorIndex, varBinds = await get_cmd(
                    engine,
                    CommunityData(community or 'public', mpModel=mp_model),
                    transport,
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
                if errorIndication or errorStatus:
                    return None
                for name, val in varBinds:
                    return str(val)
                return None
            finally:
                # 关闭 dispatcher 以释放资源；不同版本签名略有差异，这里做兜底处理
                try:
                    close = getattr(engine, 'close_dispatcher', None)
                    if close is not None:
                        close()
                except Exception:
                    pass

        return asyncio.run(_do())
    except Exception:
        # 若 v3arch.asyncio 不可用，则回退到旧版同步 HLAPI（pysnmp 4.x）
        try:
            from pysnmp.hlapi import (
                SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
                ObjectType, ObjectIdentity, getCmd,
            )
        except Exception:
            return None
        try:
            ver = (snmp_version or '2c').strip()
            mp_model = 0 if ver == '1' else 1
            iterator = getCmd(
                SnmpEngine(),
                CommunityData(community or 'public', mpModel=mp_model),
                UdpTransportTarget((ip, 161), timeout=timeout_ms / 1000.0, retries=retries),
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            )
            errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
            if errorIndication or errorStatus:
                return None
            for name, val in varBinds:
                return str(val)
        except Exception:
            return None
        return None


def _execute_discovery_rule(rule_id, job_id=None):
    """内部：执行某条自动发现规则，不校验权限。

    若提供 job_id，则在 AutoDiscoveryJob 中实时更新 scanned/added_count 等进度信息。
    返回 dict(ok, scanned, added_count, added, skipped, log_id, error)。
    """
    _ensure_tables()
    rule = AutoDiscoveryRule.query.get_or_404(rule_id)
    if not rule.enabled:
        return {'ok': False, 'scanned': 0, 'added_count': 0, 'added': [], 'skipped': [], 'log_id': None, 'error': '规则未启用'}
    # 读取全局 SNMP 设置
    snmp_version = _get_setting('snmp_version', '2c') or '2c'
    if snmp_version not in ('1', '2c'):
        snmp_version = '2c'  # 暂仅支持 v1/v2c
    community = (rule.snmp_community or _get_setting('snmp_community', 'public') or 'public').strip()
    timeout_ms = int(_get_setting('snmp_timeout_ms', '2000') or '2000')
    retries = int(_get_setting('snmp_retries', '1') or '1')
    hostname_oid = rule.hostname_oid or '1.3.6.1.2.1.1.5.0'
    # 若规则未显式配置设备类型 OID，则运行时使用 sysDescr 作为兜底
    device_type_oid = rule.device_type_oid or '1.3.6.1.2.1.1.1.0'

    from datetime import datetime
    started_at = datetime.utcnow()
    added_devices = []
    skipped = []
    scanned_ips = []
    error_msg = ''
    success = True

    job = None
    if job_id is not None:
        try:
            job = AutoDiscoveryJob.query.get(job_id)
        except Exception:
            job = None

    try:
        # 强制使用最新设备表：结束可能存在的旧事务并清空会话缓存（定时任务线程可能沿用旧会话）
        db.session.rollback()
        db.session.expire_all()
        unique_by = (_get_setting('discovery_unique_by', 'hostname') or 'hostname').strip().lower()
        if unique_by not in ('hostname', 'ip'):
            unique_by = 'hostname'
        for ip in _iter_ip_ranges(rule.ip_range, limit=65536):
            scanned_ips.append(ip)
            # 唯一性按 IP 时：若设备列表中已有该 IP，跳过添加
            if unique_by == 'ip':
                existing = Device.query.filter_by(ip=ip).first()
                if existing:
                    skipped.append({'ip': ip, 'hostname': '', 'reason': 'exists'})
                    continue
            # 必须获取到主机名才能添加
            hostname = _snmp_get(ip, hostname_oid, community, timeout_ms, retries, snmp_version=snmp_version) or ''
            hostname = hostname.strip()
            if not hostname:
                skipped.append({'ip': ip, 'hostname': '', 'reason': 'no_hostname'})
                continue
            # 主机名过滤：取「前 N 段」拼成主机名
            split_char = (_get_setting('discovery_hostname_split_char', '') or '').strip()
            try:
                # 默认取前 1 段
                seg_one_based = int(_get_setting('discovery_hostname_segment_index', '1') or '1')
                seg_one_based = max(1, min(seg_one_based, 20))
            except (TypeError, ValueError):
                seg_one_based = 1
            if split_char and seg_one_based >= 1 and hostname:
                parts = hostname.split(split_char)
                taken = parts[:seg_one_based]
                hostname = split_char.join(taken).strip()
            # 唯一性按主机名时：若设备列表中已有该主机名，跳过添加
            if unique_by == 'hostname':
                existing = Device.query.filter_by(hostname=hostname).first()
                if existing:
                    skipped.append({'ip': ip, 'hostname': hostname, 'reason': 'exists'})
                    continue
            # 必须获取到系统类型才能添加
            dev_type_raw = ''
            if device_type_oid:
                dev_type_raw = (_snmp_get(ip, device_type_oid, community, timeout_ms, retries, snmp_version=snmp_version) or '').strip()
            dev_type = _detect_device_type_from_snmp(dev_type_raw)
            if not dev_type:
                skipped.append({'ip': ip, 'hostname': hostname, 'reason': 'no_device_type'})
                continue
            dev = Device(
                ip=ip,
                hostname=hostname,
                device_type=dev_type,
                enabled=True,
                device_group=rule.device_group.strip() if rule.device_group else None,
            )
            db.session.add(dev)
            added_devices.append({'ip': ip, 'hostname': hostname, 'device_type': dev_type})

            # 周期性更新任务进度（避免过于频繁地写数据库）
            if job is not None and len(scanned_ips) % 10 == 0:
                try:
                    job.scanned = len(scanned_ips)
                    job.added_count = len(added_devices)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        # 正常情况下先提交设备变更
        db.session.commit()
    except Exception as e:
        # 发生异常则回滚设备写入，但仍会记录一条运行日志
        db.session.rollback()
        success = False
        error_msg = str(e)
        app.logger.exception('run_discovery_rule failed for rule %s', rule_id)

    # 写入运行日志（无论成功与否都写一条）
    try:
        log_entry = AutoDiscoveryRunLog(
            rule_id=rule.id,
            started_at=started_at,
            finished_at=datetime.utcnow(),
            scanned=len(scanned_ips),
            added_count=len(added_devices) if success else 0,
            added_json=json.dumps(added_devices if success else [], ensure_ascii=False),
            skipped_json=json.dumps(
                skipped if success else (skipped + ([{'ip': '', 'reason': 'error', 'message': error_msg}] if error_msg else [])),
                ensure_ascii=False
            ),
        )
        db.session.add(log_entry)
        db.session.commit()
        log_id = log_entry.id
    except Exception:
        db.session.rollback()
        log_id = None

    resp = {
        'ok': success,
        'rule_id': rule.id,
        'scanned': len(scanned_ips),
        'added_count': len(added_devices) if success else 0,
        'added': added_devices if success else [],
        'skipped': skipped,
        'log_id': log_id,
        'error': error_msg or ('执行规则时发生异常' if not success else None),
    }
    return resp


@app.route('/api/discovery/rules/<int:rule_id>/run', methods=['POST'])
def run_discovery_rule(rule_id):
    """执行某条自动发现规则（API 入口，校验权限，异步后台任务）"""
    _ensure_tables()
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权操作，请使用管理员账号登录。'}), 403
    from datetime import datetime
    rule = AutoDiscoveryRule.query.get_or_404(rule_id)
    job = AutoDiscoveryJob(
        rule_id=rule.id,
        status='running',
        started_at=datetime.utcnow(),
    )
    db.session.add(job)
    db.session.commit()

    def _run_job_async(job_id: int, r_id: int):
        with app.app_context():
            job_obj = AutoDiscoveryJob.query.get(job_id)
            if not job_obj or job_obj.status != 'running':
                return
            from datetime import datetime as _dt
            try:
                resp = _execute_discovery_rule(r_id, job_id=job_id)
                job_obj.scanned = int(resp.get('scanned') or 0)
                job_obj.added_count = int(resp.get('added_count') or 0)
                job_obj.log_id = resp.get('log_id')
                job_obj.status = 'success' if resp.get('ok') else 'failed'
                job_obj.error = (resp.get('error') or '') if not resp.get('ok') else ''
            except Exception as e:
                job_obj.status = 'failed'
                job_obj.error = str(e)
            finally:
                job_obj.finished_at = _dt.utcnow()
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                # 自动发现新增设备告警
                if job_obj.status == 'success' and (job_obj.added_count or 0) > 0:
                    try:
                        log_entry = AutoDiscoveryRunLog.query.get(job_obj.log_id) if job_obj.log_id else None
                        added = []
                        if log_entry and log_entry.added_json:
                            added = json.loads(log_entry.added_json or '[]')
                        rule_name = (rule.name or '').strip() or ('规则 #%d' % rule.id)
                        lines = ['【vConfig 自动发现】规则「%s」完成：扫描 %d 个 IP，新增 %d 台设备。' % (
                            rule_name, job_obj.scanned or 0, job_obj.added_count or 0,
                        )]
                        for d in added[:20]:
                            lines.append('  - %s (%s) %s' % (
                                d.get('hostname', ''), d.get('ip', ''), d.get('device_type', ''),
                            ))
                        if len(added) > 20:
                            lines.append('  ... 等共 %d 台' % len(added))
                        msg = '\n'.join(lines)
                        _maybe_send_alerts('discovery_new', '【vConfig】自动发现新增设备', msg, {
                            'event': 'discovery_new',
                            'rule_id': rule.id,
                            'rule_name': rule_name,
                            'scanned': job_obj.scanned,
                            'added_count': job_obj.added_count,
                        })
                    except Exception as ae:
                        app.logger.warning('发现告警发送异常: %s', ae)

    t = threading.Thread(target=_run_job_async, args=(job.id, rule.id), daemon=True)
    t.start()

    return jsonify({'ok': True, 'job_id': job.id}), 200


@app.route('/api/discovery/rules/status', methods=['GET'])
def discovery_rule_statuses():
    """返回每条自动发现规则最近一次任务的状态，用于前端轮询按钮状态。"""
    _ensure_tables()
    from sqlalchemy import func
    subq = (
        db.session.query(
            AutoDiscoveryJob.rule_id,
            func.max(AutoDiscoveryJob.id).label('max_id'),
        )
        .group_by(AutoDiscoveryJob.rule_id)
        .subquery()
    )
    jobs = (
        db.session.query(AutoDiscoveryJob)
        .join(subq, (AutoDiscoveryJob.rule_id == subq.c.rule_id) & (AutoDiscoveryJob.id == subq.c.max_id))
        .all()
    )
    return jsonify({'items': [j.to_dict() for j in jobs]})


@app.route('/api/discovery/rules/<int:rule_id>/status', methods=['GET'])
def discovery_rule_status(rule_id):
    """返回指定规则最近一次任务的状态，用于运行日志弹窗实时刷新。"""
    _ensure_tables()
    job = (
        AutoDiscoveryJob.query
        .filter_by(rule_id=rule_id)
        .order_by(AutoDiscoveryJob.id.desc())
        .first()
    )
    if not job:
        return jsonify({}), 200
    return jsonify(job.to_dict())


@app.route('/api/discovery/rules/<int:rule_id>/logs', methods=['GET'])
def list_discovery_rule_logs(rule_id):
    """某条自动发现规则的最近运行日志列表。所有登录用户可查看（只读用户可见）。"""
    _ensure_tables()
    rule = AutoDiscoveryRule.query.get_or_404(rule_id)
    logs = (AutoDiscoveryRunLog.query
            .filter_by(rule_id=rule.id)
            .order_by(AutoDiscoveryRunLog.id.desc())
            .limit(20)
            .all())
    return jsonify({
        'rule': rule.to_dict(),
        'logs': [log.to_dict() for log in logs],
    })


def _detect_device_type_from_snmp(dev_type_text: str) -> str:
    """
    根据 SNMP 获取到的设备类型字符串做模糊匹配：
    - 优先在设备类型配置（DeviceTypeConfig）中查找：若 SNMP 字段包含任一类型代码或显示名称（不区分大小写），则认为匹配该类型；
    - 若未匹配到，再按常见厂商关键字（Cisco / Huawei / Juniper / H3C）做兜底匹配；
    - 若仍未匹配，则返回原始字符串（由前端或人工后续调整）。
    """
    text = (dev_type_text or '').strip()
    if not text:
        return ''
    lowered = text.lower()

    # 0. 先应用用户在「自动发现」模块中配置的设备类型关键字映射：
    #    每行格式类似：Huawei=Huawei,FutureMatrix,CloudEngine
    #    解析后，若 SNMP 返回值中包含任一右侧关键字，则归类为左侧的设备类型代码。
    try:
        raw_rules = _get_setting('discovery_type_keywords', '') or ''
    except Exception:
        raw_rules = ''
    if raw_rules:
        for line in raw_rules.splitlines():
            line = (line or '').strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                target, patterns = line.split('=', 1)
                target = (target or '').strip()
                if not target:
                    continue
                for part in patterns.split(','):
                    kw = (part or '').strip()
                    if not kw:
                        continue
                    if kw.lower() in lowered:
                        return target

    # 1. 从数据库中已启用的设备类型配置里做关键字匹配（type_code / display_name 任意一项命中即算）
    try:
        candidates = DeviceTypeConfig.query.filter_by(enabled=True).all()
    except Exception:
        candidates = []

    for cfg in candidates:
        for key in (cfg.type_code, cfg.display_name):
            key_str = (key or '').strip()
            if not key_str:
                continue
            if key_str.lower() in lowered:
                # 优先返回标准的类型代码，若无则退回到匹配到的关键字本身
                return cfg.type_code or key_str

    # 2. 常见厂商关键字兜底匹配
    vendor_keywords = [
        ('cisco', 'Cisco'),
        ('huawei', 'Huawei'),
        ('juniper', 'Juniper'),
        ('h3c', 'H3C'),
    ]
    for kw, code in vendor_keywords:
        if kw in lowered:
            return code

    # 3. 保留原始字符串，方便后续人工参考
    return text


def _start_full_backup(run_type='manual', executor=''):
    """内部：启动全量备份任务（不校验用户权限，由调用方负责）。run_type: manual | scheduled；executor: 执行者用户名，定时任务传 'System'。返回 (ok, message)。"""
    global _backup_running, _current_job, _backup_jobs, _backup_lock_fd
    # 跨进程文件锁（gunicorn 多 worker 时，防止调度器与手动同时启动，确保 run_type 正确）
    lock_fd = None
    if _HAS_FCNTL:
        try:
            os.makedirs(os.path.dirname(_BACKUP_LOCK_PATH), exist_ok=True)
            lock_fd = open(_BACKUP_LOCK_PATH, 'w')
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError, BlockingIOError) as e:
            if lock_fd:
                try:
                    lock_fd.close()
                except Exception:
                    pass
            # EAGAIN(11) / EWOULDBLOCK(35)：锁被占用
            err = getattr(e, 'errno', None)
            if err in (11, 35):
                return False, '备份任务正在执行中'
            app.logger.warning('备份锁获取失败，使用进程内锁: %s', e)
    with _backup_lock:
        if _backup_running:
            if lock_fd:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    lock_fd.close()
                except Exception:
                    pass
            return False, '备份任务正在执行中'
        _backup_running = True
        _backup_lock_fd = lock_fd

    username = _get_setting('username', DEFAULT_USERNAME)
    password = _get_setting('password', DEFAULT_PASSWORD)
    default_conn = _get_setting('default_connection_type', DEFAULT_CONNECTION_TYPE).upper() or 'TELNET'
    devices = Device.query.filter_by(enabled=True).all()
    allowed_grps = _current_user_allowed_groups()
    if allowed_grps is not None:
        from sqlalchemy import or_
        devices = [d for d in devices if (d.device_group or '').strip() in allowed_grps or ((not (d.device_group or '').strip()) and '（未分组）' in allowed_grps)]
    devices = [d for d in devices if not _is_device_in_maintenance(d)]
    try:
        global_ssh = int(_get_setting('ssh_port', str(SSH_PORT)) or str(SSH_PORT))
        global_ssh = max(1, min(65535, global_ssh))
    except (TypeError, ValueError):
        global_ssh = SSH_PORT
    try:
        global_telnet = int(_get_setting('telnet_port', '23') or '23')
        global_telnet = max(1, min(65535, global_telnet))
    except (TypeError, ValueError):
        global_telnet = 23
    device_list = [
        (
            d.ip,
            d.hostname,
            d.device_type,
            d.username or username,
            d.password or password,
            (d.connection_type or default_conn).upper(),
            d.ssh_port if d.ssh_port is not None else global_ssh,
            d.telnet_port if d.telnet_port is not None else global_telnet,
        )
        for d in devices
    ]
    if not device_list:
        with _backup_lock:
            _backup_running = False
        return False, '没有启用的设备'

    job_id = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    job = {
        'id': job_id,
        'start_time': datetime.utcnow().isoformat() + 'Z',
        'total': len(device_list),
        'done': 0,
        'ok': 0,
        'fail': 0,
        'status': 'running',
        'end_time': None,
        'run_type': run_type,
        'executor': (executor or '')[:128],
    }
    with _backup_lock:
        _current_job = job
        _backup_jobs.insert(0, job)
        _backup_jobs[:] = _backup_jobs[:_MAX_BACKUP_JOBS]

    # 写审计：全量备份启动
    _write_audit('run_backup_all', resource_type='backup', resource_id=job_id, detail=f'total={len(device_list)}')

    try:
        thread_num = int(_get_setting('backup_thread_num', str(BACKUP_THREAD_NUM)) or str(BACKUP_THREAD_NUM))
    except (TypeError, ValueError):
        thread_num = BACKUP_THREAD_NUM
    thread_num = max(1, min(50, thread_num))
    try:
        ssh_port = int(_get_setting('ssh_port', str(SSH_PORT)) or str(SSH_PORT))
    except (TypeError, ValueError):
        ssh_port = SSH_PORT
    ssh_port = max(1, min(65535, ssh_port))
    try:
        telnet_port = int(_get_setting('telnet_port', '23') or '23')
    except (TypeError, ValueError):
        telnet_port = 23
    telnet_port = max(1, min(65535, telnet_port))
    try:
        backup_timeout = int(_get_setting('backup_timeout_seconds', '30') or '30')
    except (TypeError, ValueError):
        backup_timeout = 30
    backup_timeout = max(5, min(300, backup_timeout))
    try:
        backup_read_timeout = int(_get_setting('backup_read_timeout_seconds', '30') or '30')
    except (TypeError, ValueError):
        backup_read_timeout = 30
    backup_read_timeout = max(10, min(300, backup_read_timeout))
    fallback_flag = (_get_setting('backup_connection_fallback', '0') or '0') == '1'

    def _build_type_configs():
        """构建设备类型 -> 配置 的显性映射，供备份线程使用"""
        cfgs = DeviceTypeConfig.query.all()
        mapping = {}
        for c in cfgs:
            cfg = {
                'backup_config': c.get_backup_config(),
                'connection_config': c.get_connection_config(),
                'driver_type': c.driver_type,
                'driver_module': c.driver_module,
            }
            mapping[c.type_code] = cfg
            mapping[c.type_code.upper()] = cfg
        return mapping

    def _finish():
        global _backup_running, _current_job, _backup_lock_fd
        error_msg = None
        try:
            # 在线程中使用数据库前，先进入应用上下文，避免 Flask-SQLAlchemy 报错
            with app.app_context():
                type_configs = _build_type_configs()
            run_backup_task(
                device_list,
                CONFIGS_DIR,
                username,
                password,
                EXCLUDE_PATTERNS,
                _log_callback,
                default_connection_type=default_conn,
                ssh_port=ssh_port,
                telnet_port=telnet_port,
                timeout_seconds=backup_timeout,
                read_timeout_seconds=backup_read_timeout,
                app_context=None,
                type_configs=type_configs,
                fallback_to_second=fallback_flag,
                max_workers=thread_num,
            )
        except Exception as e:
            # 兜底：如果备份线程在开始前就异常退出，至少记录一条失败日志，避免任务看起来“什么都没发生”
            error_msg = str(e) or '备份任务执行过程中发生未知错误'
            try:
                with app.app_context():
                    log = BackupLog(
                        ip='-',
                        hostname='[系统任务异常]',
                        device_type='-',
                        status='Fail',
                        message=error_msg,
                        duration_seconds=None,
                        config_path=None,
                    )
                    db.session.add(log)
                    db.session.commit()
            except Exception:
                # 避免影响后续状态收尾
                pass
        finally:
            job_to_save = None
            with _backup_lock:
                if _current_job is not None:
                    total = _current_job.get('total', 0)
                    done = _current_job.get('done', 0)
                    # 如果一次日志都没有写出来（done 仍为 0），无论是否抛出异常，都认为任务失败
                    if total > 0 and done == 0:
                        if not error_msg:
                            error_msg = '本次备份任务未对任何设备执行，请检查备份线程或过滤规则配置。'
                            try:
                                with app.app_context():
                                    log = BackupLog(
                                        ip='-',
                                        hostname='[系统任务异常]',
                                        device_type='-',
                                        status='Fail',
                                        message=error_msg,
                                        duration_seconds=None,
                                        config_path=None,
                                    )
                                    db.session.add(log)
                                    db.session.commit()
                            except Exception:
                                pass
                        _current_job['status'] = 'failed'
                    else:
                        _current_job['status'] = 'completed'
                    _current_job['end_time'] = datetime.utcnow().isoformat() + 'Z'
                    job_to_save = dict(_current_job)
                    _current_job = None
                _backup_running = False
            if _HAS_FCNTL and _backup_lock_fd is not None:
                try:
                    fcntl.flock(_backup_lock_fd.fileno(), fcntl.LOCK_UN)
                    _backup_lock_fd.close()
                except Exception:
                    pass
                _backup_lock_fd = None
            if job_to_save:
                try:
                    with app.app_context():
                        _ensure_tables()
                        r = BackupJobRun(
                            id=job_to_save.get('id', ''),
                            start_time=(job_to_save.get('start_time') or '')[:64],
                            end_time=(job_to_save.get('end_time') or '')[:64],
                            total=job_to_save.get('total', 0),
                            done=job_to_save.get('done', 0),
                            ok=job_to_save.get('ok', 0),
                            fail=job_to_save.get('fail', 0),
                            status=job_to_save.get('status', 'completed'),
                            run_type=(job_to_save.get('run_type') or 'manual')[:16],
                            executor=(job_to_save.get('executor') or '')[:128],
                        )
                        db.session.add(r)
                        db.session.commit()
                except Exception as e:
                    try:
                        db.session.rollback()
                    except Exception:
                        pass
                    app.logger.warning('备份任务记录保存失败: %s', e)
                # 备份完成后计算「配置变动」并写入数据库
                try:
                    config_files_service.save_config_changes_to_db()
                except Exception as e:
                    app.logger.warning('保存配置变动到数据库失败: %s', e)
                # 备份任务完成告警（不管有无失败都通知；有失败/未执行时附列表）
                total = job_to_save.get('total', 0)
                ok = job_to_save.get('ok', 0)
                fail = job_to_save.get('fail', 0)
                done = job_to_save.get('done', 0)
                failed_lines = []
                not_run_count = max(0, total - done)
                with app.app_context():
                    try:
                        from datetime import timezone as dt_timezone
                        st_raw = (job_to_save.get('start_time') or '').strip()
                        et_raw = (job_to_save.get('end_time') or '').strip()
                        st = None
                        et = None
                        if st_raw:
                            st = datetime.fromisoformat(st_raw.replace('Z', '+00:00'))
                        if et_raw:
                            et = datetime.fromisoformat(et_raw.replace('Z', '+00:00'))
                        if st and et:
                            if st.tzinfo:
                                st_utc = st.astimezone(dt_timezone.utc).replace(tzinfo=None)
                            else:
                                st_utc = st
                            if et.tzinfo:
                                et_utc = et.astimezone(dt_timezone.utc).replace(tzinfo=None)
                            else:
                                et_utc = et
                            logs = BackupLog.query.filter(
                                BackupLog.created_at >= st_utc,
                                BackupLog.created_at <= et_utc,
                                BackupLog.status != 'OK',
                            ).order_by(BackupLog.created_at.asc()).all()
                            for log in logs:
                                reason = _normalize_backup_failure_reason(log.status, log.message)
                                failed_lines.append(
                                    '- 设备名称: %s，管理 IP: %s，设备类型: %s，失败原因: %s' % (
                                        log.hostname or '',
                                        log.ip or '',
                                        log.device_type or '',
                                        reason,
                                    )
                                )
                    except Exception as e:
                        app.logger.warning('统计备份失败设备列表时出错: %s', e)
                    header = '【vConfig 备份告警】任务 %s 完成：共 %d 台，成功 %d 台，失败 %d 台。' % (
                        job_to_save.get('id', ''), total, ok, fail
                    )
                    parts = [header]
                    if failed_lines:
                        parts.append('\n备份失败设备列表：\n' + '\n'.join(failed_lines))
                    if not_run_count > 0:
                        parts.append('\n未执行或未记录: %d 台' % not_run_count)
                    msg = '\n'.join(parts)
                    _maybe_send_alerts('backup_failure', '【vConfig】备份任务完成告警', msg, {
                        'event': 'backup_failure',
                        'job_id': job_to_save.get('id'),
                        'total': total,
                        'ok': ok,
                        'fail': fail,
                        'end_time': job_to_save.get('end_time'),
                    })
            _cleanup_old_backups()

    t = threading.Thread(target=_finish)
    t.daemon = True
    t.start()
    return True, f'已启动备份任务，共 {len(device_list)} 台设备'


@app.route('/api/backup/run', methods=['POST'])
def run_backup():
    """执行备份 (异步)"""
    if not _can_run_backup():
        return jsonify({'error': '当前登录账号无权执行全量备份，请联系系统管理员分配「运维用户」或「管理员」角色。'}), 403
    ok, msg = _start_full_backup(executor=_current_username())
    if not ok:
        code = 409 if msg == '备份任务正在执行中' else 400
        return jsonify({'error': msg}), code
    return jsonify({'ok': True, 'message': msg})


# 备份任务最大允许「进行中」时长（秒），超过则自动标记为超时结束
_BACKUP_JOB_TIMEOUT_SECONDS = 10 * 60  # 10 分钟


@app.route('/api/backup/status')
def backup_status():
    _ensure_tables()
    with _backup_lock:
        memory_jobs = [dict(j) for j in _backup_jobs]
        current = dict(_current_job) if _current_job else None
    memory_ids = {j['id'] for j in memory_jobs}
    db_runs = BackupJobRun.query.order_by(BackupJobRun.id.desc()).limit(_MAX_BACKUP_JOBS).all()
    # 将超过规定时间仍为「进行中」的任务自动标记为超时结束，避免一直卡住
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    for r in db_runs:
        if (r.status or '').strip().lower() != 'running':
            continue
        try:
            st_str = (r.start_time or '').replace('Z', '+00:00')
            st = datetime.fromisoformat(st_str) if st_str else None
        except Exception:
            st = None
        if not st:
            continue
        elapsed = (now_utc - st).total_seconds() if st.tzinfo else (datetime.utcnow() - st).total_seconds()
        if elapsed < _BACKUP_JOB_TIMEOUT_SECONDS:
            continue
        r.status = 'completed'
        r.end_time = now_utc.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        r.done = r.total or 1
        r.fail = max(1, (r.total or 1) - (r.ok or 0))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    db_jobs = [r.to_dict() for r in db_runs if r.id not in memory_ids]

    # 为「单台备份」任务补充目标设备信息，便于前端展示
    single_device_ids = []
    for j in db_jobs:
        jid = j.get('id') or ''
        if jid.startswith('single_'):
            parts = jid.split('_')
            if len(parts) >= 3 and parts[-1].isdigit():
                try:
                    single_device_ids.append(int(parts[-1]))
                except Exception:
                    continue
    if single_device_ids:
        devs = Device.query.filter(Device.id.in_(single_device_ids)).all()
        dev_map = {d.id: d for d in devs}
        for j in db_jobs:
            jid = j.get('id') or ''
            if not jid.startswith('single_'):
                continue
            parts = jid.split('_')
            if len(parts) >= 3 and parts[-1].isdigit():
                did = int(parts[-1])
                d = dev_map.get(did)
                if d:
                    j['single_device'] = {
                        'id': d.id,
                        'hostname': d.hostname,
                        'ip': d.ip,
                    }
    jobs = sorted(
        memory_jobs + db_jobs,
        key=lambda j: (j.get('start_time') or ''),
        reverse=True,
    )[:_MAX_BACKUP_JOBS]
    timezone = _get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    return jsonify({
        'running': _backup_running,
        'jobs': jobs,
        'current': current,
        'timezone': timezone,
        'can_run_backup': _can_run_backup(),
    })


@app.route('/api/backup/run/device/<int:device_id>', methods=['POST'])
def run_backup_one(device_id):
    """单台设备立即备份（异步，立即返回）"""
    if not _can_run_backup():
        return jsonify({'error': '当前登录账号无权执行单台备份，请联系系统管理员分配「运维用户」或「管理员」角色。'}), 403
    dev = Device.query.get_or_404(device_id)
    username = dev.username or _get_setting('username', DEFAULT_USERNAME)
    password = dev.password or _get_setting('password', DEFAULT_PASSWORD)
    conn_type = (dev.connection_type or _get_setting('default_connection_type', DEFAULT_CONNECTION_TYPE)).upper() or 'TELNET'
    try:
        _ssh_port = int(_get_setting('ssh_port', str(SSH_PORT)) or str(SSH_PORT))
        _ssh_port = max(1, min(65535, _ssh_port))
    except (TypeError, ValueError):
        _ssh_port = SSH_PORT
    dev_ssh = dev.ssh_port if dev.ssh_port is not None else _ssh_port
    try:
        _telnet_port = int(_get_setting('telnet_port', '23') or '23')
        _telnet_port = max(1, min(65535, _telnet_port))
    except (TypeError, ValueError):
        _telnet_port = 23
    dev_telnet = dev.telnet_port if dev.telnet_port is not None else _telnet_port
    item = (dev.ip, dev.hostname, dev.device_type, username, password, conn_type, dev_ssh, dev_telnet)
    try:
        _timeout = int(_get_setting('backup_timeout_seconds', '30') or '30')
        _timeout = max(5, min(300, _timeout))
    except (TypeError, ValueError):
        _timeout = 30
    try:
        _read_timeout = int(_get_setting('backup_read_timeout_seconds', '30') or '30')
        _read_timeout = max(10, min(300, _read_timeout))
    except (TypeError, ValueError):
        _read_timeout = 30
    default_user = _get_setting('username', DEFAULT_USERNAME)
    default_pass = _get_setting('password', DEFAULT_PASSWORD)
    fallback_flag = (_get_setting('backup_connection_fallback', '0') or '0') == '1'

    job_id = 'single_' + datetime.utcnow().strftime('%Y%m%d%H%M%S') + '_' + str(device_id)
    start_time = datetime.utcnow().isoformat() + 'Z'
    try:
        _ensure_tables()
        r = BackupJobRun(
            id=job_id[:32],
            start_time=start_time[:64],
            end_time=None,
            total=1,
            done=0,
            ok=0,
            fail=0,
            status='running',
            run_type='manual',
            executor=(_current_username() or '')[:128],
        )
        db.session.add(r)
        db.session.commit()
    except Exception as e:
        app.logger.warning('单台备份任务记录创建失败: %s', e)
        try:
            db.session.rollback()
        except Exception:
            pass

    def _single_log_callback(ip, hostname, dev_type, status, message, duration, config_path=None):
        _log_callback(ip, hostname, dev_type, status, message, duration, config_path)
        try:
            with app.app_context():
                _ensure_tables()
                job = BackupJobRun.query.get(job_id[:32])
                if job:
                    job.done = 1
                    job.ok = 1 if status == 'OK' else 0
                    job.fail = 0 if status == 'OK' else 1
                    job.status = 'completed'
                    job.end_time = datetime.utcnow().isoformat() + 'Z'
                    db.session.commit()
                    # 单台备份失败时触发告警
                    if status != 'OK':
                        reason = _normalize_backup_failure_reason(status, message)
                        msg_lines = [
                            '【vConfig 备份告警】单台备份失败：',
                            '- 设备名称: %s' % (hostname or ip or ''),
                            '- 管理 IP: %s' % (ip or ''),
                            '- 设备类型: %s' % (dev_type or ''),
                            '- 失败原因: %s' % reason,
                        ]
                        msg = '\n'.join(msg_lines)
                        _maybe_send_alerts('backup_failure', '【vConfig】单台备份失败告警', msg, {
                            'event': 'single_backup_failure',
                            'job_id': job.id,
                            'hostname': hostname,
                            'ip': ip,
                            'device_type': dev_type,
                            'status': status,
                            'error': message,
                            'end_time': job.end_time,
                        })
                # 单台备份完成后计算「配置变动」并写入数据库
                try:
                    config_files_service.save_config_changes_to_db()
                except Exception as e:
                    app.logger.warning('保存配置变动到数据库失败: %s', e)
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            app.logger.warning('单台备份任务记录更新失败: %s', e)

    def _do():
        try:
            # 为当前设备构建设备类型配置映射（显性配置驱动）
            type_configs = {}
            try:
                cfg = DeviceTypeConfig.query.filter_by(type_code=dev.device_type).first()
                if cfg:
                    tc = {
                        'backup_config': cfg.get_backup_config(),
                        'connection_config': cfg.get_connection_config(),
                        'driver_type': cfg.driver_type,
                        'driver_module': cfg.driver_module,
                    }
                    type_configs[cfg.type_code] = tc
                    type_configs[cfg.type_code.upper()] = tc
            except Exception:
                type_configs = {}

            run_single_backup(
                item, CONFIGS_DIR,
                default_user,
                default_pass,
                _single_log_callback,
                default_connection_type=conn_type,
                ssh_port=dev_ssh,
                telnet_port=dev_telnet,
                timeout_seconds=_timeout,
                read_timeout_seconds=_read_timeout,
                app_context=app.app_context(),
                type_configs=type_configs,
                fallback_to_second=fallback_flag,
            )
        except Exception as e:
            app.logger.warning('单台设备备份失败: %s', e)
            try:
                with app.app_context():
                    job = BackupJobRun.query.get(job_id[:32])
                    if job and job.status == 'running':
                        job.done = 1
                        job.fail = 1
                        job.status = 'completed'
                        job.end_time = datetime.utcnow().isoformat() + 'Z'
                        db.session.commit()
                    try:
                        config_files_service.save_config_changes_to_db()
                    except Exception:
                        pass
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass

    t = threading.Thread(target=_do)
    t.daemon = True
    t.start()
    _write_audit('run_backup_one', resource_type='backup', resource_id=str(dev.id), detail=f'hostname={dev.hostname}, ip={dev.ip}')
    return jsonify({'ok': True, 'message': f'{dev.hostname} 已加入备份队列，请稍后在历史备份或日志中查看结果'})


@app.route('/api/backup/test/<int:device_id>', methods=['POST'])
def test_device_connection(device_id):
    """测试设备连接（使用设备独立账号或全局默认账号）"""
    dev = Device.query.get_or_404(device_id)
    username = (dev.username or _get_setting('username', DEFAULT_USERNAME) or '').strip() or DEFAULT_USERNAME
    password = dev.password if (dev.password is not None and dev.password != '') else _get_setting('password', DEFAULT_PASSWORD)
    if password is None:
        password = DEFAULT_PASSWORD
    password = str(password)
    conn_type = (dev.connection_type or _get_setting('default_connection_type', DEFAULT_CONNECTION_TYPE) or '').strip().upper() or 'TELNET'
    try:
        _port = int(_get_setting('ssh_port', str(SSH_PORT)) or str(SSH_PORT))
        _port = max(1, min(65535, _port))
    except (TypeError, ValueError):
        _port = SSH_PORT
    ssh_port = dev.ssh_port if dev.ssh_port is not None else _port
    _telnet = int(_get_setting('telnet_port', '23') or '23')
    _telnet = max(1, min(65535, _telnet))
    telnet_port = dev.telnet_port if dev.telnet_port is not None else _telnet
    ok, msg = test_connection(dev.ip, username, password, dev.device_type, connection_type=conn_type, ssh_port=ssh_port, telnet_port=telnet_port)
    return jsonify({'ok': ok, 'message': msg})


# ---------- 配置下发（手工，SSH） ----------
@app.route('/api/devices/<int:device_id>/push-config', methods=['POST'])
def push_device_config(device_id):
    """对单台设备下发配置命令（仅允许本地管理员，且通过 SSH）"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权下发配置，请使用全局用户名登录。'}), 403
    dev = Device.query.get_or_404(device_id)
    data = request.get_json(force=True, silent=True) or {}
    commands = (data.get('commands') or '').strip()
    host_confirm = (data.get('hostname_confirm') or '').strip()
    keyword_confirm = (data.get('keyword_confirm') or '').strip()

    if not commands:
        return jsonify({'error': '请输入要下发的配置命令。'}), 400
    if host_confirm != dev.hostname:
        return jsonify({'error': '主机名确认不匹配，请输入当前设备的完整主机名以确认。'}), 400
    if keyword_confirm != '下发':
        return jsonify({'error': '关键字确认错误，请输入“下发”二字以最终确认。'}), 400

    # 仅支持 SSH 下发配置
    conn_type = (dev.connection_type or _get_setting('default_connection_type', DEFAULT_CONNECTION_TYPE) or '').strip().upper() or 'TELNET'
    if conn_type != 'SSH':
        return jsonify({'error': f'当前设备连接方式为 {conn_type or "TELNET"}，仅支持通过 SSH 下发配置，请在设备管理中将连接方式调整为 SSH 后重试。'}), 400

    username = (dev.username or _get_setting('username', DEFAULT_USERNAME) or '').strip()
    password = dev.password if (dev.password is not None and dev.password != '') else _get_setting('password', DEFAULT_PASSWORD)
    if not username or not password:
        return jsonify({'error': '未配置可用的登录用户名/密码，无法下发配置，请先在全局设置或设备中配置账号。'}), 400

    try:
        _port = int(_get_setting('ssh_port', str(SSH_PORT)) or str(SSH_PORT))
        _port = max(1, min(65535, _port))
    except (TypeError, ValueError):
        _port = SSH_PORT
    ssh_port = dev.ssh_port if dev.ssh_port is not None else _port
    ok, msg, output_snippet, duration = _push_config_via_ssh(dev.ip, username, str(password), commands, ssh_port=ssh_port)

    status = 'OK' if ok else 'Fail_SSH'
    # 截断保存的命令与输出，避免过长
    cmds_to_save = (commands or '')[:4000]
    out_to_save = (output_snippet or '')[:4000]
    run_by = (session.get('user') or '')[:128]

    try:
        log = ConfigPushLog(
            device_id=dev.id,
            ip=dev.ip,
            hostname=dev.hostname,
            device_type=dev.device_type,
            commands=cmds_to_save,
            status=status,
            message=msg[:240] if msg else '',
            output_snippet=out_to_save,
            duration_seconds=duration,
            run_by=run_by,
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    _write_audit(
        'push_config',
        resource_type='device',
        resource_id=str(dev.id),
        detail=f'hostname={dev.hostname}, status={status}',
    )

    return jsonify({
        'ok': ok,
        'status': status,
        'message': msg,
        'duration_seconds': duration,
        'output_snippet': output_snippet,
    })


# ---------- 远程登录终端 ----------
_terminal_sessions = {}
_terminal_sessions_lock = threading.Lock()


def _terminal_connect_ssh(ip, port, username, password, out_queue, stop_event):
    """连接 SSH 并启动向 out_queue 推送输出的线程；返回 (channel, client) 供发送用，失败返回 (None, None)。"""
    try:
        import paramiko
    except ImportError:
        out_queue.put('[SSH] 未安装 paramiko\r\n'.encode('utf-8', errors='replace'))
        return None, None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=ip,
            port=port,
            username=username,
            password=password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
        channel = client.invoke_shell(width=160, height=40)
        import time
        time.sleep(0.3)
        if channel.recv_ready():
            try:
                banner = channel.recv(65535)
                if banner:
                    out_queue.put(banner)
            except Exception:
                pass

        def read_loop():
            try:
                while not stop_event.is_set():
                    if channel.recv_ready():
                        try:
                            data = channel.recv(4096)
                            if not data:
                                break
                            out_queue.put(data)
                        except Exception:
                            break
                    else:
                        stop_event.wait(0.05)
            except Exception:
                pass
        t = threading.Thread(target=read_loop, daemon=True)
        t.start()
        return channel, client
    except Exception as e:
        out_queue.put(('[SSH] 连接失败: %s\r\n' % str(e)).encode('utf-8', errors='replace'))
        return None, None


def _terminal_connect_telnet(ip, port, username, password, dev_type, type_config, out_queue, stop_event):
    """连接 Telnet，完成登录，并启动向 out_queue 推送输出的线程；返回 tn 供发送用，失败返回 None。"""
    import time
    import re
    from backup_service import _get_device_driver
    try:
        tn = telnetlib.Telnet(ip, port, timeout=15)
    except Exception as e:
        out_queue.put(('[Telnet] 连接失败: %s\r\n' % str(e)).encode('utf-8', errors='replace'))
        return None
    try:
        cfg = type_config or {}
        driver = _get_device_driver(dev_type, cfg)
        login_prompt = driver.get_login_prompt().encode() if driver else b'sername'
        password_prompt = driver.get_password_prompt().encode() if driver else b'assword'
        tn.read_until(login_prompt, timeout=15)
        tn.write(username.encode() + b'\r\n')
        tn.read_until(password_prompt, timeout=15)
        tn.write(password.encode() + b'\r\n')
        prompts = [re.compile(p.encode() if isinstance(p, str) else p) for p in (driver.get_prompts() if driver else [rb'.*#$', rb'>\s*$'])]
        tn.expect(prompts, timeout=15)
    except Exception as e:
        try:
            tn.close()
        except Exception:
            pass
        out_queue.put(('[Telnet] 登录失败: %s\r\n' % str(e)).encode('utf-8', errors='replace'))
        return None

    def read_loop():
        try:
            while not stop_event.is_set():
                try:
                    data = tn.read_very_eager()
                    if data:
                        out_queue.put(data)
                except Exception:
                    break
                stop_event.wait(0.05)
        except Exception:
            pass
    t = threading.Thread(target=read_loop, daemon=True)
    t.start()
    return tn


@app.route('/api/terminal-login-defaults')
def terminal_login_defaults():
    """返回用于终端登录预填的用户名和密码：当前登录用户 + 全局默认密码（仅用于预填，不保存）。"""
    if not _can_edit_settings():
        return jsonify({'error': '无权'}), 403
    username = (_current_username() or '').strip()
    password = _get_setting('password', DEFAULT_PASSWORD) or ''
    return jsonify({'username': username, 'password': password})


@app.route('/api/devices/<int:device_id>/terminal/start', methods=['POST'])
def terminal_start(device_id):
    """创建远程终端会话（SSH 或 Telnet），返回 session_id。请求体可传 username/password 用于本次登录，不保存。"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权使用远程终端。'}), 403
    dev = Device.query.get_or_404(device_id)
    body = request.get_json(force=True, silent=True) or {}
    if 'username' in body:
        username = (body.get('username') or '').strip() or (dev.username or _get_setting('username', DEFAULT_USERNAME) or '').strip()
    else:
        username = (dev.username or _get_setting('username', DEFAULT_USERNAME) or '').strip()
    if 'password' in body:
        password = body.get('password')
    else:
        password = dev.password if (dev.password is not None and dev.password != '') else _get_setting('password', DEFAULT_PASSWORD)
    if not username or password is None or (isinstance(password, str) and not str(password).strip()):
        return jsonify({'error': '请填写用户名和密码。'}), 400
    conn_type = (dev.connection_type or _get_setting('default_connection_type', DEFAULT_CONNECTION_TYPE) or '').strip().upper() or 'TELNET'
    ssh_port = dev.ssh_port if dev.ssh_port is not None else int(_get_setting('ssh_port', str(SSH_PORT)) or str(SSH_PORT))
    ssh_port = max(1, min(65535, ssh_port))
    telnet_port = dev.telnet_port if dev.telnet_port is not None else int(_get_setting('telnet_port', '23') or '23')
    telnet_port = max(1, min(65535, telnet_port))

    out_queue = queue.Queue()
    stop_event = threading.Event()
    session_id = uuid.uuid4().hex
    type_config = None
    try:
        with app.app_context():
            cfg = DeviceTypeConfig.query.filter_by(type_code=dev.device_type).first()
            if cfg and cfg.connection_config:
                import json as _json
                try:
                    type_config = _json.loads(cfg.connection_config) if isinstance(cfg.connection_config, str) else cfg.connection_config
                except Exception:
                    pass
    except Exception:
        pass

    if conn_type == 'SSH':
        channel, client = _terminal_connect_ssh(dev.ip, ssh_port, username, str(password), out_queue, stop_event)
        if channel is None:
            return jsonify({'error': 'SSH 连接失败，请查看终端输出。'}), 502
        with _terminal_sessions_lock:
            _terminal_sessions[session_id] = {
                'device_id': device_id,
                'queue': out_queue,
                'stop': stop_event,
                'channel': channel,
                'client': client,
                'tn': None,
            }
    else:
        tn = _terminal_connect_telnet(dev.ip, telnet_port, username, str(password), dev.device_type, type_config, out_queue, stop_event)
        if tn is None:
            return jsonify({'error': 'Telnet 连接失败，请查看终端输出。'}), 502
        with _terminal_sessions_lock:
            _terminal_sessions[session_id] = {
                'device_id': device_id,
                'queue': out_queue,
                'stop': stop_event,
                'channel': None,
                'client': None,
                'tn': tn,
            }

    return jsonify({'session_id': session_id})


@app.route('/api/devices/<int:device_id>/terminal/stream')
def terminal_stream(device_id):
    """SSE 流：持续推送终端输出。"""
    if not _can_edit_settings():
        return jsonify({'error': '无权使用远程终端。'}), 403
    session_id = (request.args.get('session_id') or '').strip()
    if not session_id:
        return jsonify({'error': '缺少 session_id'}), 400
    with _terminal_sessions_lock:
        sess = _terminal_sessions.get(session_id)
    if not sess:
        return jsonify({'error': '会话不存在或已关闭'}), 404

    def generate():
        q = sess['queue']
        welcome = '[Terminal] 已连接设备，正在接收输出...\r\n'.encode('utf-8')
        yield 'data: ' + base64.b64encode(welcome).decode('ascii') + '\n\n'
        while True:
            try:
                chunk = q.get(timeout=2)
                if isinstance(chunk, bytes):
                    yield 'data: ' + base64.b64encode(chunk).decode('ascii') + '\n\n'
                else:
                    yield 'data: ' + base64.b64encode(chunk.encode('utf-8', errors='replace')).decode('ascii') + '\n\n'
            except queue.Empty:
                yield ': keepalive\n\n'
            except Exception:
                break

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/devices/<int:device_id>/terminal/send', methods=['POST'])
def terminal_send(device_id):
    """向终端发送用户输入。"""
    if not _can_edit_settings():
        return jsonify({'error': '无权使用远程终端。'}), 403
    data = request.get_json(force=True, silent=True) or {}
    session_id = (data.get('session_id') or '').strip()
    payload = data.get('data') or data.get('input') or ''
    if not session_id:
        return jsonify({'error': '缺少 session_id'}), 400
    with _terminal_sessions_lock:
        sess = _terminal_sessions.get(session_id)
    if not sess:
        return jsonify({'error': '会话不存在或已关闭'}), 404
    if isinstance(payload, str):
        payload = payload.encode('utf-8', errors='replace')
    try:
        if sess.get('channel'):
            sess['channel'].send(payload)
        elif sess.get('tn'):
            sess['tn'].write(payload)
        else:
            return jsonify({'error': '会话已关闭'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True})


@app.route('/api/devices/<int:device_id>/terminal/close', methods=['POST'])
def terminal_close(device_id):
    """关闭终端会话。"""
    if not _can_edit_settings():
        return jsonify({'error': '无权使用远程终端。'}), 403
    data = request.get_json(force=True, silent=True) or {}
    session_id = (data.get('session_id') or '').strip()
    if not session_id:
        return jsonify({'error': '缺少 session_id'}), 400
    with _terminal_sessions_lock:
        sess = _terminal_sessions.pop(session_id, None)
    if not sess:
        return jsonify({'ok': True})
    sess['stop'].set()
    try:
        if sess.get('client'):
            sess['client'].close()
        if sess.get('tn'):
            try:
                sess['tn'].close()
            except Exception:
                pass
    except Exception:
        pass
    return jsonify({'ok': True})


# ---------- 页脚（全局）：渲染时注入，首屏即有数据 ----------
@app.context_processor
def inject_footer_vars():
    """所有使用 base 的页面在服务端渲染时即带页脚数据与当前登录用户，不依赖首屏请求 /api/footer-info"""
    try:
        _ensure_tables()
        raw = request.headers.get('X-Forwarded-For') or request.headers.get('X-Real-IP') or (getattr(request, 'remote_addr', None) or '')
        client_ip = (raw.split(',')[0].strip() if isinstance(raw, str) and ',' in raw else raw) or ''
        if not isinstance(client_ip, str):
            client_ip = str(client_ip)
        logo_file = _get_setting('logo_file', '') or ''
        logo_url = url_for('settings_assets.logo') if logo_file else ''
        return {
            # 系统名称：用于顶部品牌区与登录页展示
            'system_name': _get_setting('system_name', '配置备份中心') or '配置备份中心',
            'footer_text': _get_setting('footer_text', '') or '',
            'footer_timezone': _get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
            'footer_client_ip': client_ip,
            'current_user': session.get('user') or '',
            'user_role': _current_role(),
            'show_settings_nav': _current_role() != 'viewer',
            'static_version': 3,  # 静态资源版本，改版后递增以强制浏览器拉取最新 JS/CSS
            'logo_url': logo_url,
            'language': _get_setting('language', 'zh') or 'zh',
        }
    except Exception:
        return {
            'system_name': '配置备份中心',
            'footer_text': '',
            'footer_timezone': DEFAULT_TIMEZONE,
            'footer_client_ip': '',
            'current_user': session.get('user') or '',
            'user_role': _current_role(),
            'show_settings_nav': _current_role() != 'viewer',
            'static_version': 3,
            'logo_url': '',
            'language': 'zh',
        }


app.register_blueprint(create_settings_assets_blueprint({
    'logo_dir': LOGO_DIR,
    'logo_max_size': LOGO_MAX_SIZE,
    'can_edit_settings': _can_edit_settings,
    'ensure_tables': _ensure_tables,
    'get_setting': _get_setting,
    'set_setting': _set_setting,
    'write_audit': _write_audit,
}))

app.register_blueprint(create_settings_ops_blueprint({
    'can_edit_settings': _can_edit_settings,
    'get_setting': _get_setting,
    'ensure_tables': _ensure_tables,
    'write_audit': _write_audit,
    'webhook_body_for_url': _webhook_body_for_url,
    'send_alert_email': _send_alert_email,
    'log_alert': _log_alert,
    'certs_dir': CERTS_DIR,
}))

app.register_blueprint(create_settings_core_blueprint({
    'current_role': _current_role,
    'can_edit_settings': _can_edit_settings,
    'get_setting': _get_setting,
    'set_setting': _set_setting,
    'setting_has_secret_value': _setting_has_secret_value,
    'get_default_settings': _get_default_settings,
    'reload_backup_schedule': _reload_backup_schedule,
    'write_audit': _write_audit,
    'logo_dir': LOGO_DIR,
}))

config_files_service = ConfigFilesService({
    'configs_dir': CONFIGS_DIR,
    'can_edit_settings': _can_edit_settings,
    'is_admin': _is_admin,
    'write_audit': _write_audit,
    'ensure_tables': _ensure_tables,
    'app_context': app.app_context,
    'logger': app.logger,
})
app.register_blueprint(create_config_files_blueprint(config_files_service))

app.register_blueprint(create_users_blueprint({
    'can_edit_settings': _can_edit_settings,
    'check_password_policy': _check_password_policy,
    'super_admin_username': SUPER_ADMIN_USERNAME,
}))

app.register_blueprint(create_device_types_blueprint({
    'can_edit_settings': _can_edit_settings,
    'ensure_tables': _ensure_tables,
    'get_builtin_type_config': _get_builtin_type_config,
}))

app.register_blueprint(create_device_groups_blueprint({
    'can_edit_settings': _can_edit_settings,
    'ensure_device_group_column': _ensure_device_group_column,
    'get_setting': _get_setting,
    'set_setting': _set_setting,
}))

app.register_blueprint(create_device_inventory_blueprint({
    'can_edit_settings': _can_edit_settings,
    'current_user_allowed_groups': _current_user_allowed_groups,
    'get_setting': _get_setting,
    'normalize_device_type': _normalize_device_type,
    'write_audit': _write_audit,
}))

app.register_blueprint(create_backup_logs_blueprint({
    'get_setting': _get_setting,
    'get_zoneinfo': _get_zoneinfo,
}))

app.register_blueprint(create_reports_blueprint({
    'ensure_tables': _ensure_tables,
    'ensure_device_group_column': _ensure_device_group_column,
    'current_user_allowed_groups': _current_user_allowed_groups,
    'write_audit': _write_audit,
}))

app.register_blueprint(create_pages_blueprint({
    'ensure_tables': _ensure_tables,
    'ensure_connection_type_column': _ensure_connection_type_column,
    'ensure_device_group_column': _ensure_device_group_column,
    'ensure_device_maintenance_columns': _ensure_device_maintenance_columns,
    'ensure_device_ssh_port_column': _ensure_device_ssh_port_column,
    'ensure_device_telnet_port_column': _ensure_device_telnet_port_column,
    'ensure_user_allowed_groups_column': _ensure_user_allowed_groups_column,
    'get_setting': _get_setting,
}))

app.register_blueprint(create_auth_blueprint({
    'ensure_tables': _ensure_tables,
    'ensure_user_password_column': _ensure_user_password_column,
    'ensure_super_admin': _ensure_super_admin,
    'ensure_user_record': _ensure_user_record,
    'get_setting': _get_setting,
    'check_login_locked': _check_login_locked,
    'login_fail_record': _login_fail_record,
    'login_fail_clear': _login_fail_clear,
    'write_audit': _write_audit,
}))


# ---------- 初始化 ----------
@app.cli.command('init-db')
def init_db():
    db.create_all()
    print('Database initialized.')


@app.cli.command('import-ip-list')
def import_ip_list():
    """从 ip_list 文件导入设备"""
    path = os.path.join(os.path.dirname(__file__), 'ip_list')
    if not os.path.exists(path):
        path = '/opt/vconfig/ip_list'
    if not os.path.exists(path):
        print('ip_list not found.')
        return
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip()]
    with app.app_context():
        count = 0
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                hostname, ip, dev_type = parts[0], parts[1], _normalize_device_type(parts[2])
                if Device.query.filter_by(ip=ip, hostname=hostname).first():
                    continue
                db.session.add(Device(ip=ip, hostname=hostname, device_type=dev_type))
                count += 1
        db.session.commit()
        print(f'Imported {count} devices.')


@app.cli.command('reset-admin-password')
def reset_admin_password():
    """将内置 admin 账号密码重置为默认（admin123，或环境变量 SUPER_ADMIN_DEFAULT_PASSWORD）"""
    with app.app_context():
        _ensure_user_password_column()
        u = User.query.filter_by(username=SUPER_ADMIN_USERNAME).first()
        if u is None:
            _ensure_super_admin()
            print('已创建 admin 账号，默认密码: admin123')
        else:
            u.source = 'local'  # 确保可被本地登录匹配
            u.set_password(SUPER_ADMIN_DEFAULT_PASSWORD)
            db.session.commit()
            print('admin 密码已重置为默认（admin123）。请使用 用户名: admin  密码: admin123 登录。')


@app.cli.command('dedupe-devices')
def dedupe_devices():
    """设备去重：按 (ip, hostname) 保留一条，同 IP 多条的保留一条（保留 id 最小的），并清理关联日志的 device_id。"""
    from sqlalchemy import text
    with app.app_context():
        engine = db.engine
        conn = engine.connect()
        try:
            # 1) 按 (ip, hostname) 去重：每组保留 id 最小的
            rows = conn.execute(text("""
                SELECT ip, hostname, GROUP_CONCAT(id) as ids
                FROM devices
                GROUP BY ip, hostname
                HAVING COUNT(*) > 1
            """)).fetchall()
            to_delete = []
            for r in rows:
                ids = [int(x) for x in r[2].split(',')]
                keep = min(ids)
                to_delete.extend(i for i in ids if i != keep)
            # 2) 按 ip 去重：同一 IP 多条（不同 hostname）也只保留 id 最小的一条
            by_ip = conn.execute(text("""
                SELECT ip, GROUP_CONCAT(id) as ids
                FROM devices
                GROUP BY ip
                HAVING COUNT(*) > 1
            """)).fetchall()
            for r in by_ip:
                ids = [int(x) for x in r[1].split(',')]
                keep = min(ids)
                to_delete.extend(i for i in ids if i != keep)
            to_delete = list(dict.fromkeys(to_delete))  # 去重且保持顺序
            if not to_delete:
                print('没有需要去重的设备。')
                conn.close()
                return
            ids_place = ','.join(str(x) for x in to_delete)
            conn.execute(text(f"UPDATE backup_logs SET device_id = NULL WHERE device_id IN ({ids_place})"))
            conn.execute(text(f"UPDATE config_push_logs SET device_id = NULL WHERE device_id IN ({ids_place})"))
            conn.execute(text(f"DELETE FROM devices WHERE id IN ({ids_place})"))
            conn.commit()
            print(f'已去重并删除 {len(to_delete)} 条重复设备（id: {ids_place}）。')
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f'去重失败: {e}')
        finally:
            conn.close()


@app.cli.command('fix-device-types')
def fix_device_types():
    """批量修正历史数据中的设备类型大小写：CISCO/JUNIPER/HUAWEI -> Cisco/Juniper/Huawei"""
    from sqlalchemy import text
    with app.app_context():
        _ensure_tables()
        engine = db.engine
        conn = engine.connect()
        try:
            # devices 表
            conn.execute(text("UPDATE devices SET device_type = 'Cisco'   WHERE device_type = 'CISCO'"))
            conn.execute(text("UPDATE devices SET device_type = 'Juniper' WHERE device_type = 'JUNIPER'"))
            conn.execute(text("UPDATE devices SET device_type = 'Huawei'  WHERE device_type = 'HUAWEI'"))
            # backup_logs 表中也同步一下，保证日志里的展示一致
            conn.execute(text("UPDATE backup_logs SET device_type = 'Cisco'   WHERE device_type = 'CISCO'"))
            conn.execute(text("UPDATE backup_logs SET device_type = 'Juniper' WHERE device_type = 'JUNIPER'"))
            conn.execute(text("UPDATE backup_logs SET device_type = 'Huawei'  WHERE device_type = 'HUAWEI'"))
            conn.commit()
            print('设备类型字段已批量修正为 Cisco/Juniper/Huawei。')
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f'修正设备类型失败: {e}')
        finally:
            conn.close()


# 应用加载后延迟启动内置备份调度器
_start_scheduler_delayed()

def _ensure_ssl_certs():
    """若启用 HTTPS 且证书不存在，则生成自签名证书（有效期 100 年）"""
    cert_file = os.path.join(CERTS_DIR, 'cert.pem')
    key_file = os.path.join(CERTS_DIR, 'key.pem')
    if os.path.isfile(cert_file) and os.path.isfile(key_file):
        return (cert_file, key_file)
    os.makedirs(CERTS_DIR, mode=0o700, exist_ok=True)
    import subprocess
    # 有效期 36500 天 ≈ 100 年（自签名证书无公开 CA 限制）
    cmd = [
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
        '-keyout', key_file, '-out', cert_file,
        '-days', '36500', '-nodes',
        '-subj', '/CN=localhost/O=vConfig/C=CN',
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        return (cert_file, key_file)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        app.logger.warning('生成自签名证书失败（需安装 openssl）: %s，将使用 HTTP', e)
        return None


def _start_http_redirect_server(host: str, https_port: int = 443):
    """在端口 80 启动 HTTP 服务，将请求 301 重定向到 HTTPS"""
    import http.server
    import socketserver

    class RedirectHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            self._redirect()

        def do_POST(self):
            self._redirect()

        def do_HEAD(self):
            self._redirect()

        def do_OPTIONS(self):
            self._redirect()

        def _redirect(self):
            host_header = self.headers.get('Host', '').split(':')[0] or 'localhost'
            path = self.path or '/'
            if path.startswith('//'):
                path = '/' + path.lstrip('/')
            url = 'https://%s:%s%s' % (host_header, https_port, path)
            self.send_response(301)
            self.send_header('Location', url)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, format, *args):
            app.logger.debug('HTTP redirect: %s', args[0] if args else '')

    try:
        with socketserver.TCPServer((host, 80), RedirectHandler) as httpd:
            app.logger.info('HTTP 端口 80 已启动，将自动重定向到 HTTPS')
            httpd.serve_forever()
    except OSError as e:
        app.logger.warning('HTTP 80 端口监听失败（需 root 或端口被占用）: %s', e)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _ensure_connection_type_column()
        _ensure_device_group_column()
        _ensure_device_maintenance_columns()
        _ensure_device_ssh_port_column()
        _ensure_device_telnet_port_column()
        _ensure_user_allowed_groups_column()
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    use_https = os.environ.get('FLASK_HTTPS', '1') == '1'
    port = int(os.environ.get('FLASK_PORT', '443' if use_https else '80'))
    ssl_context = None
    if use_https:
        certs = _ensure_ssl_certs()
        if certs:
            ssl_context = certs
            app.logger.info('HTTPS 模式：使用证书 %s', certs[0])
            # 启用 HTTPS 时，默认在 80 端口启动 HTTP 重定向到 HTTPS
            t = threading.Thread(target=_start_http_redirect_server, args=(host, port), daemon=True)
            t.start()
        else:
            use_https = False
            port = 80 if port == 443 else port
    app.run(host=host, port=port, debug=debug, ssl_context=ssl_context)
