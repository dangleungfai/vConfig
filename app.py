# -*- coding: utf-8 -*-
"""配置备份 Web 管理"""
import os
import io
import csv
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
from flask import Flask, request, jsonify, send_from_directory, render_template, Response, session, redirect, url_for
from config import (
    CONFIGS_DIR, LOG_DIR, DEFAULT_USERNAME, DEFAULT_PASSWORD,
    BACKUP_THREAD_NUM, EXCLUDE_PATTERNS, DEFAULT_CONNECTION_TYPE, SSH_PORT,
    BACKUP_RETENTION_DAYS, DEFAULT_TIMEZONE, DATA_ROOT, CERTS_DIR,
)
from models import db, Device, BackupLog, AppSetting, BackupJobRun, LoginLog, AuditLog, ConfigPushLog, User, ConfigChangeRecord, DeviceTypeConfig, AutoDiscoveryRule, AutoDiscoveryRunLog, _isoformat_utc
from device_drivers import register_driver, load_custom_drivers
from device_drivers.builtin import register_builtin_drivers
from compliance import check_config
from backup_service import run_backup_async, run_single_backup, test_connection, run_backup_task

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

            # 初始化默认设备类型（如果不存在）
            default_types = [
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
                        'prompt': '\r\n{master}'
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
                        'prompt': ']'
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

            for dt in default_types:
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

            db.session.commit()
            _device_type_configs_initialized = True
    except Exception as e:
        import logging
        logging.warning(f"Failed to initialize device type configs: {e}")
        _device_type_configs_initialized = True  # 避免重复尝试


def _ensure_user_password_column():
    """为 users 表添加 password_hash 列（兼容旧库，仅针对 SQLite 简单处理）"""
    global _user_password_column_ensured
    if _user_password_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            if db.engine.dialect.name == 'sqlite':
                with db.engine.connect() as conn:
                    r = conn.execute(text("PRAGMA table_info(users)"))
                    cols = [row[1] for row in r]
                    if 'password_hash' not in cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
                        conn.commit()
            _user_password_column_ensured = True
    except Exception:
        pass


def _ensure_user_email_phone_columns():
    """为 users 表添加 email、phone 列（兼容旧库，仅 SQLite）"""
    global _user_email_phone_columns_ensured
    if _user_email_phone_columns_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            if db.engine.dialect.name == 'sqlite':
                with db.engine.connect() as conn:
                    r = conn.execute(text("PRAGMA table_info(users)"))
                    cols = [row[1] for row in r]
                    if 'email' not in cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(128)"))
                        conn.commit()
                    if 'phone' not in cols:
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
            _ensure_backup_job_run_type_column()
            _ensure_backup_job_executor_column()
            # 初始化设备类型配置
            _ensure_device_type_configs()
    except Exception:
        pass


def _ensure_connection_type_column():
    """为已有数据库添加 connection_type 列（兼容旧库）"""
    global _connection_type_column_ensured
    if _connection_type_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            if db.engine.dialect.name == 'sqlite':
                with db.engine.connect() as conn:
                    r = conn.execute(text("PRAGMA table_info(devices)"))
                    cols = [row[1] for row in r]
                    if 'connection_type' not in cols:
                        conn.execute(text("ALTER TABLE devices ADD COLUMN connection_type VARCHAR(16)"))
                        conn.commit()
            _connection_type_column_ensured = True
    except Exception:
        pass


def _ensure_device_group_column():
    """为 devices 表添加 group 列（兼容旧库，仅 SQLite）"""
    global _device_group_column_ensured
    if _device_group_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            if db.engine.dialect.name == 'sqlite':
                with db.engine.connect() as conn:
                    r = conn.execute(text("PRAGMA table_info(devices)"))
                    cols = [row[1] for row in r]
                    if 'device_group' not in cols:
                        conn.execute(text("ALTER TABLE devices ADD COLUMN device_group VARCHAR(64)"))
                        conn.commit()
            _device_group_column_ensured = True
    except Exception:
        pass


def _ensure_device_maintenance_columns():
    """为 devices 表添加 maintenance_start、maintenance_end 列（兼容旧库，仅 SQLite）"""
    global _device_maintenance_columns_ensured
    if _device_maintenance_columns_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            if db.engine.dialect.name == 'sqlite':
                with db.engine.connect() as conn:
                    r = conn.execute(text("PRAGMA table_info(devices)"))
                    cols = [row[1] for row in r]
                    if 'maintenance_start' not in cols:
                        conn.execute(text("ALTER TABLE devices ADD COLUMN maintenance_start VARCHAR(8)"))
                        conn.commit()
                    if 'maintenance_end' not in cols:
                        conn.execute(text("ALTER TABLE devices ADD COLUMN maintenance_end VARCHAR(8)"))
                        conn.commit()
            _device_maintenance_columns_ensured = True
    except Exception:
        pass


def _ensure_device_ssh_port_column():
    """为 devices 表添加 ssh_port 列（兼容旧库，仅 SQLite）"""
    global _device_ssh_port_column_ensured
    if _device_ssh_port_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            if db.engine.dialect.name == 'sqlite':
                with db.engine.connect() as conn:
                    r = conn.execute(text("PRAGMA table_info(devices)"))
                    cols = [row[1] for row in r]
                    if 'ssh_port' not in cols:
                        conn.execute(text("ALTER TABLE devices ADD COLUMN ssh_port INTEGER"))
                        conn.commit()
            _device_ssh_port_column_ensured = True
    except Exception:
        pass


def _ensure_device_telnet_port_column():
    """为 devices 表添加 telnet_port 列（兼容旧库，仅 SQLite）"""
    global _device_telnet_port_column_ensured
    if _device_telnet_port_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            if db.engine.dialect.name == 'sqlite':
                with db.engine.connect() as conn:
                    r = conn.execute(text("PRAGMA table_info(devices)"))
                    cols = [row[1] for row in r]
                    if 'telnet_port' not in cols:
                        conn.execute(text("ALTER TABLE devices ADD COLUMN telnet_port INTEGER"))
                        conn.commit()
            _device_telnet_port_column_ensured = True
    except Exception:
        pass


def _ensure_user_allowed_groups_column():
    """为 users 表添加 allowed_groups 列（兼容旧库，仅 SQLite）"""
    global _user_allowed_groups_column_ensured
    if _user_allowed_groups_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            if db.engine.dialect.name == 'sqlite':
                with db.engine.connect() as conn:
                    r = conn.execute(text("PRAGMA table_info(users)"))
                    cols = [row[1] for row in r]
                    if 'allowed_groups' not in cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN allowed_groups VARCHAR(512)"))
                        conn.commit()
            _user_allowed_groups_column_ensured = True
    except Exception:
        pass


def _ensure_backup_job_run_type_column():
    """为 backup_job_runs 表添加 run_type 列（兼容旧库）"""
    global _backup_job_run_type_column_ensured
    if _backup_job_run_type_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            if db.engine.dialect.name == 'sqlite':
                with db.engine.connect() as conn:
                    r = conn.execute(text("PRAGMA table_info(backup_job_runs)"))
                    cols = [row[1] for row in r]
                    if 'run_type' not in cols:
                        conn.execute(text("ALTER TABLE backup_job_runs ADD COLUMN run_type VARCHAR(16) DEFAULT 'manual'"))
                        conn.commit()
            _backup_job_run_type_column_ensured = True
    except Exception:
        pass


def _ensure_backup_job_executor_column():
    """为 backup_job_runs 表添加 executor 列（兼容旧库）"""
    global _backup_job_executor_column_ensured
    if _backup_job_executor_column_ensured:
        return
    try:
        from sqlalchemy import text
        with app.app_context():
            if db.engine.dialect.name == 'sqlite':
                with db.engine.connect() as conn:
                    r = conn.execute(text("PRAGMA table_info(backup_job_runs)"))
                    cols = [row[1] for row in r]
                    if 'executor' not in cols:
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
        return redirect(url_for('login_view', next=path))

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
                    return redirect(url_for('login_view', next=path))
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
        # 备份并发线程数：默认 5
        'backup_thread_num': str(BACKUP_THREAD_NUM),
        'ssh_port': str(SSH_PORT),
        'telnet_port': '23',
        'backup_failure_webhook': '',
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
            # 若之前未设置角色，则根据当前登录方式补全一个合理默认值
            if not (u.role or '').strip():
                u.role = 'admin' if is_admin_default else 'viewer'
            # 更新来源与显示名（仅在为空时）
            if not (u.source or '').strip():
                u.source = str(auth_source or 'local')[:32]
            if not (u.display_name or '').strip():
                u.display_name = str(username)[:128]
            # 被禁用用户仍允许展示，但不在此处强行改为启用
        db.session.commit()
        session['role'] = u.role or 'viewer'
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
    role = session.get('role')
    if role:
        return role
    username = _current_username()
    if not username:
        return 'viewer'
    try:
        u = User.query.filter_by(username=username).first()
        if not u:
            return 'viewer'
        session['role'] = u.role or 'viewer'
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


@app.route('/login', methods=['GET'])
def login_view():
    """登录页"""
    # 已登录直接跳首页
    if session.get('user'):
        return redirect(url_for('index'))
    return render_template('login.html')


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


@app.route('/api/login', methods=['POST'])
def api_login():
    """用户名/密码登录（本地账号 + 可选 LDAP）"""
    _ensure_tables()
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    locked, remain = _check_login_locked()
    if locked:
        return jsonify({'error': f'登录失败次数过多，请 {remain} 分钟后再试。'}), 403

    # 若该用户已在用户表中被禁用，则拒绝登录
    try:
        _ensure_user_password_column()
        _ensure_super_admin()
        u = User.query.filter_by(username=username).first()
        if u is not None and u.is_active is False:
            return jsonify({'error': '该用户已被禁用，请联系系统管理员。'}), 403
    except Exception:
        # 查询异常时不影响后续登录流程
        pass

    authed = False
    auth_source = None

    # 1. 本地账号优先：使用 users 表中的本地账号（支持多本地账号 + 独立密码）
    try:
        u = User.query.filter_by(username=username, source='local').first()
    except Exception:
        u = None
    if u is not None and u.check_password(password):
        authed = True
        auth_source = 'local'

    # 2. 兼容旧逻辑：使用设置中的默认用户名/密码（首次成功后会把密码写入 users 表）
    if not authed:
        local_user = _get_setting('username', DEFAULT_USERNAME)
        local_pass = _get_setting('password', DEFAULT_PASSWORD)
        if username == local_user and password == local_pass:
            authed = True
            auth_source = 'local'
            # 确保在 users 表中创建/更新对应记录，并补充密码哈希
            try:
                _ensure_user_password_column()
                u2 = User.query.filter_by(username=username).first()
                if u2 is None:
                    u2 = User(
                        username=username[:128],
                        display_name=username[:128],
                        source='local',
                        role='admin',
                        is_active=True,
                    )
                    db.session.add(u2)
                u2.set_password(password)
                db.session.commit()
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass

    # 3. LDAP 校验（开启且本地失败时）
    if not authed and _get_setting('ldap_enabled', '0') == '1':
        try:
            from ldap3 import Server, Connection, ALL
        except ImportError:
            return jsonify({'error': '服务器未安装 ldap3，无法使用 LDAP 登录'}), 500
        ldap_server = _get_setting('ldap_server', '').strip()
        ldap_base_dn = _get_setting('ldap_base_dn', '').strip()
        ldap_bind_dn = _get_setting('ldap_bind_dn', '').strip()
        ldap_bind_password = _get_setting('ldap_bind_password', '')
        ldap_user_filter = (_get_setting('ldap_user_filter', '(uid={username})') or '(uid={username})').strip()
        if not ldap_server or not ldap_base_dn:
            return jsonify({'error': 'LDAP 配置不完整（服务器或 Base DN 未设置）'}), 500
        try:
            server = Server(ldap_server, get_info=ALL)
            # 管理员 Bind（如需匿名可根据实际情况调整）
            if ldap_bind_dn:
                conn = Connection(server, user=ldap_bind_dn, password=ldap_bind_password, auto_bind=True)
            else:
                conn = Connection(server, auto_bind=True)
            search_filter = ldap_user_filter.replace('{username}', username)
            # 不请求虚构的 dn 属性，只用 DN 本身（entry_dn）
            if not conn.search(ldap_base_dn, search_filter):
                return jsonify({'error': 'LDAP 登录失败：未找到该用户'}), 401
            user_dn = conn.entries[0].entry_dn
            # 用用户 DN + 密码重新 Bind 校验密码
            user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
            user_conn.unbind()
            conn.unbind()
            authed = True
            auth_source = 'ldap'
        except Exception as e:
            return jsonify({'error': f'LDAP 登录失败：{e}'}), 401

    if not authed:
        _login_fail_record()
        return jsonify({'error': '用户名或密码错误'}), 401

    _login_fail_clear()
    # 登录成功，写入 Session
    session['user'] = username
    session['auth_source'] = auth_source or 'local'

    # 记录登录日志（用于仪表盘展示最近登录）
    try:
        raw = request.headers.get('X-Forwarded-For') or request.headers.get('X-Real-IP') or (getattr(request, 'remote_addr', None) or '')
        src_ip = (raw.split(',')[0].strip() if isinstance(raw, str) and ',' in raw else raw) or ''
        if not isinstance(src_ip, str):
            src_ip = str(src_ip)
        log = LoginLog(
            username=username,
            source_ip=src_ip,
            auth_source=auth_source or 'local',
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    # 确保在用户表中有一条记录，并自动赋予默认角色
    try:
        _ensure_user_record(username, auth_source or 'local')
    except Exception:
        # 出现异常时不影响登录流程
        pass

    # 写入审计日志
    _write_audit('login_success', resource_type='auth', resource_id=username)

    return jsonify({'ok': True})


@app.route('/api/ldap/test', methods=['POST'])
def api_ldap_test():
    """测试 LDAP 登录：使用当前配置，尝试绑定并返回详细错误信息（不创建会话）"""
    _ensure_tables()
    if _get_setting('ldap_enabled', '0') != '1':
        return jsonify({'ok': False, 'message': 'LDAP 未启用，请先在设置中勾选「启用 LDAP 登录」。'}), 400
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({'ok': False, 'message': '请输入测试用户名和密码。'}), 400
    try:
        from ldap3 import Server, Connection, ALL
    except ImportError:
        return jsonify({'ok': False, 'message': '服务器未安装 ldap3，无法使用 LDAP 登录。'}), 500

    ldap_server = _get_setting('ldap_server', '').strip()
    ldap_base_dn = _get_setting('ldap_base_dn', '').strip()
    ldap_bind_dn = _get_setting('ldap_bind_dn', '').strip()
    ldap_bind_password = _get_setting('ldap_bind_password', '')
    ldap_user_filter = (_get_setting('ldap_user_filter', '(uid={username})') or '(uid={username})').strip()
    if not ldap_server or not ldap_base_dn:
        return jsonify({'ok': False, 'message': 'LDAP 配置不完整（服务器地址或 Base DN 未设置）。'}), 500

    search_filter = ldap_user_filter.replace('{username}', username)
    try:
        server = Server(ldap_server, get_info=ALL)
        # 管理员 Bind（如需匿名可根据实际情况调整）
        if ldap_bind_dn:
            conn = Connection(server, user=ldap_bind_dn, password=ldap_bind_password, auto_bind=True)
        else:
            conn = Connection(server, auto_bind=True)
        # 搜索用户（不请求虚构的 dn 属性，只使用 entry_dn）
        found = conn.search(ldap_base_dn, search_filter)
        if not found or not conn.entries:
            conn.unbind()
            return jsonify({'ok': False, 'message': f'未找到该用户（filter={search_filter}, base_dn={ldap_base_dn}）'}), 200
        user_dn = conn.entries[0].entry_dn
        # 用用户 DN + 密码重新 Bind 校验密码
        try:
            user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
            user_conn.unbind()
            conn.unbind()
            return jsonify({'ok': True, 'message': f'LDAP 登录测试成功，用户 DN: {user_dn}（filter={search_filter}）'}), 200
        except Exception as e2:
            conn.unbind()
            return jsonify({'ok': False, 'message': f'用户密码校验失败：{e2}（user_dn={user_dn}, filter={search_filter}）'}), 200
    except Exception as e:
        return jsonify({'ok': False, 'message': f'LDAP 连接或搜索失败：{e}（filter={search_filter}, base_dn={ldap_base_dn}）'}), 200


@app.route('/logout')
def logout_view():
    """登出并返回登录页"""
    user = session.get('user') or ''
    _write_audit('logout', resource_type='auth', resource_id=user)
    session.clear()
    return redirect(url_for('login_view'))


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


@app.route('/')
def index():
    try:
        _ensure_tables()
        _ensure_connection_type_column()
        _ensure_device_group_column()
        _ensure_device_maintenance_columns()
        _ensure_device_ssh_port_column()
        _ensure_device_telnet_port_column()
        _ensure_user_allowed_groups_column()
    except Exception:
        pass
    return render_template('index.html')


@app.route('/configs/device/<prefix>/<path:hostname>')
def config_device_page(prefix, hostname):
    """重定向到主应用单设备配置面板 #config-device/prefix/hostname"""
    if '..' in prefix or '..' in hostname or '/' in prefix:
        return 'Invalid path', 400
    from urllib.parse import quote
    frag = 'config-device/' + quote(prefix, safe='') + '/' + quote(hostname, safe='')
    return redirect(url_for('index') + '#' + frag)


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


@app.route('/api/dashboard/export-no-backup-24h')
def export_no_backup_24h_csv():
    """导出 24 小时内未成功备份的已启用设备列表（CSV）"""
    try:
        _ensure_tables()
        _ensure_device_group_column()
    except Exception:
        pass
    last_24h = datetime.utcnow() - timedelta(hours=24)
    recent_ok_hosts = {
        r[0] for r in
        BackupLog.query.filter(
            BackupLog.created_at >= last_24h,
            BackupLog.status == 'OK',
        ).with_entities(BackupLog.hostname).distinct().all()
    }
    enabled_devs = Device.query.filter_by(enabled=True).order_by(Device.hostname).all()
    allowed_grps = _current_user_allowed_groups()
    if allowed_grps is not None:
        enabled_devs = [d for d in enabled_devs if (d.device_group or '').strip() in allowed_grps or (not (d.device_group or '').strip() and '（未分组）' in allowed_grps)]
    no_backup = [d for d in enabled_devs if (d.hostname or '') not in recent_ok_hosts]
    buf = io.StringIO()
    buf.write('\ufeff')
    w = csv.writer(buf)
    w.writerow(['主机名', '管理IP', '设备类型', '分组'])
    for d in no_backup:
        w.writerow([d.hostname, d.ip, d.device_type or '', (d.device_group or '').strip() or ''])
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='text/csv; charset=utf-8-sig',
        headers={'Content-Disposition': 'attachment; filename=no_backup_24h.csv'},
    )


@app.route('/api/devices/export')
def export_devices_csv():
    """导出设备列表 CSV（运维报表）"""
    enabled = request.args.get('enabled')
    q = Device.query.order_by(Device.hostname)
    if enabled is not None:
        q = q.filter(Device.enabled == (enabled.lower() == 'true'))
    devices = q.all()
    buf = io.StringIO()
    # 写入 UTF-8 BOM，确保在 Excel 中按 UTF-8 正常显示中文
    buf.write('\ufeff')
    w = csv.writer(buf)
    w.writerow(['主机名', '管理IP', '设备类型', '分组', '连接方式', '启用', '备注'])
    for d in devices:
        conn = (d.connection_type or '').upper() or '默认'
        grp = (d.device_group or '').strip() or ''
        w.writerow([d.hostname, d.ip, d.device_type, grp, conn, '是' if d.enabled else '否', ''])
    buf.seek(0)
    # 写入导出审计
    try:
        _write_audit('export_devices_csv', resource_type='device', resource_id='', detail=f'count={len(devices)}')
    except Exception:
        pass

    return Response(
        buf.getvalue(),
        mimetype='text/csv; charset=utf-8-sig',
        headers={'Content-Disposition': 'attachment; filename=devices.csv'},
    )


# ---------- 设备管理 ----------
@app.route('/api/devices', methods=['GET'])
def list_devices():
    """设备列表（支持分页、站点、类型、搜索、排序）"""
    enabled = request.args.get('enabled')
    page = request.args.get('page', 1, type=int)
    default_pp = int(_get_setting('device_per_page_default', '50') or '50')
    per_page = request.args.get('per_page', default_pp, type=int)
    per_page = max(1, min(per_page, 200))
    site = request.args.get('site', '').strip()   # 站点前缀，如 sha1、szx1
    dev_type = request.args.get('device_type', '').strip()
    group = request.args.get('group', '').strip()
    search = request.args.get('search', '').strip()
    sort_by = (request.args.get('sort_by') or 'hostname').strip()
    sort_dir = (request.args.get('sort_dir') or 'asc').strip().lower()

    q = Device.query
    allowed_grps = _current_user_allowed_groups()
    if allowed_grps is not None:
        from sqlalchemy import or_
        grp_cond = Device.device_group.in_(allowed_grps)
        if '（未分组）' in allowed_grps:
            grp_cond = or_(grp_cond, Device.device_group.is_(None), Device.device_group == '')
        q = q.filter(grp_cond)
    if enabled is not None:
        q = q.filter(Device.enabled == (enabled.lower() == 'true'))
    if site:
        q = q.filter(Device.hostname.like(f'{site}.%'))
    if group:
        q = q.filter(Device.device_group == group)
    if dev_type:
        # 使用规范化后的类型进行精确匹配，避免大小写带来的筛选不一致
        q = q.filter(Device.device_type == _normalize_device_type(dev_type))
    if search:
        q = q.filter(
            (Device.hostname.ilike(f'%{search}%')) | (Device.ip.ilike(f'%{search}%'))
        )
    # 排序：支持按主机名 / 管理 IP / 设备类型 / 分组 排序
    order_col = Device.hostname
    if sort_by == 'ip':
        order_col = Device.ip
    elif sort_by == 'device_type':
        order_col = Device.device_type
    elif sort_by == 'group':
        order_col = Device.device_group
    if sort_dir == 'desc':
        order_col = order_col.desc()
    else:
        sort_dir = 'asc'
    q = q.order_by(order_col, Device.id.asc())

    pagination = q.paginate(page=page, per_page=per_page)
    default_conn = _get_setting('default_connection_type', DEFAULT_CONNECTION_TYPE).upper() or 'TELNET'
    from_devices = {r[0] for r in Device.query.with_entities(Device.device_group).distinct().all() if r[0]}
    predefined = [g.strip() for g in (_get_setting('device_groups', '') or '').split(',') if g.strip()]
    groups = sorted(set(predefined) | from_devices)
    return jsonify({
        'items': [d.to_dict() for d in pagination.items],
        'total': pagination.total,
        'page': page,
        'per_page': per_page,
        'groups': groups,
        'default_connection_type': default_conn,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
        'can_manage_devices': _can_edit_settings(),
    })


@app.route('/api/devices', methods=['POST'])
def add_device():
    """新增设备"""
    data = request.get_json()
    if not data or not data.get('ip') or not data.get('hostname') or not data.get('device_type'):
        return jsonify({'error': '缺少 ip/hostname/device_type'}), 400
    conn_type = (data.get('connection_type') or '').strip().upper()
    if conn_type and conn_type not in ('TELNET', 'SSH'):
        conn_type = None
    grp = (str(data.get('group') or '').strip())[:64] or None
    m_start = (str(data.get('maintenance_start') or '').strip())[:8] or None
    m_end = (str(data.get('maintenance_end') or '').strip())[:8] or None
    try:
        sp = data.get('ssh_port')
        ssh_port_val = int(sp) if sp is not None and str(sp).strip() != '' else None
        if ssh_port_val is not None:
            ssh_port_val = max(1, min(65535, ssh_port_val))
    except (TypeError, ValueError):
        ssh_port_val = None
    try:
        tp = data.get('telnet_port')
        telnet_port_val = int(tp) if tp is not None and str(tp).strip() != '' else None
        if telnet_port_val is not None:
            telnet_port_val = max(1, min(65535, telnet_port_val))
    except (TypeError, ValueError):
        telnet_port_val = None
    dev = Device(
        ip=data['ip'].strip(),
        hostname=data['hostname'].strip(),
        device_type=_normalize_device_type(data['device_type']),
        enabled=data.get('enabled', True),
        device_group=grp,
        maintenance_start=m_start,
        maintenance_end=m_end,
        username=data.get('username'),
        password=data.get('password'),
        connection_type=conn_type or None,
        ssh_port=ssh_port_val,
        telnet_port=telnet_port_val,
    )
    db.session.add(dev)
    db.session.commit()
    _write_audit('add_device', resource_type='device', resource_id=str(dev.id), detail=f'hostname={dev.hostname}, ip={dev.ip}')
    return jsonify(dev.to_dict())


@app.route('/api/devices/<int:pk>', methods=['GET', 'PUT', 'DELETE'])
def device_detail(pk):
    dev = Device.query.get_or_404(pk)
    if request.method == 'GET':
        return jsonify(dev.to_dict())
    if request.method == 'DELETE':
        if not _can_edit_settings():
            return jsonify({'error': '当前账号无权删除设备，请使用管理员账号登录。'}), 403
        _write_audit('delete_device', resource_type='device', resource_id=str(dev.id), detail=f'hostname={dev.hostname}, ip={dev.ip}')
        db.session.delete(dev)
        db.session.commit()
        return jsonify({'ok': True})
    # PUT
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权修改设备，请使用管理员账号登录。'}), 403
    data = request.get_json(force=True, silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({'error': '请求体无效或非 JSON'}), 400
    ip_raw = (data.get('ip') or '').strip()
    hostname_raw = (data.get('hostname') or '').strip()
    if not ip_raw or not hostname_raw:
        return jsonify({'error': '缺少 IP 或主机名'}), 400
    dev.ip = ip_raw
    dev.hostname = hostname_raw
    if data.get('device_type'):
        dev.device_type = _normalize_device_type(data['device_type'])
    if 'enabled' in data:
        dev.enabled = data['enabled']
    if 'username' in data:
        dev.username = data['username'] or None
    if 'password' in data:
        dev.password = data['password'] if data['password'] else None
    if 'connection_type' in data:
        ct_raw = data.get('connection_type')
        if ct_raw is None or (isinstance(ct_raw, str) and not ct_raw.strip()):
            dev.connection_type = None
        else:
            ct = str(ct_raw).strip().upper()
            dev.connection_type = ct if ct in ('TELNET', 'SSH') else None
    if 'group' in data:
        dev.device_group = (str(data.get('group') or '').strip())[:64] or None
    if 'maintenance_start' in data:
        dev.maintenance_start = (str(data.get('maintenance_start') or '').strip())[:8] or None
    if 'maintenance_end' in data:
        dev.maintenance_end = (str(data.get('maintenance_end') or '').strip())[:8] or None
    if 'ssh_port' in data:
        try:
            sp = data.get('ssh_port')
            dev.ssh_port = int(sp) if sp is not None and str(sp).strip() != '' else None
            if dev.ssh_port is not None:
                dev.ssh_port = max(1, min(65535, dev.ssh_port))
        except (TypeError, ValueError):
            dev.ssh_port = None
    if 'telnet_port' in data:
        try:
            tp = data.get('telnet_port')
            dev.telnet_port = int(tp) if tp is not None and str(tp).strip() != '' else None
            if dev.telnet_port is not None:
                dev.telnet_port = max(1, min(65535, dev.telnet_port))
        except (TypeError, ValueError):
            dev.telnet_port = None
    # 兼容部分环境下 ORM 未正确刷新 connection_type 的情况，直接执行一次 UPDATE
    try:
        from sqlalchemy import text as _sql_text
        db.session.execute(
            _sql_text("UPDATE devices SET connection_type = :ct WHERE id = :id"),
            {"ct": dev.connection_type, "id": dev.id},
        )
    except Exception:
        pass
    db.session.commit()
    _write_audit('update_device', resource_type='device', resource_id=str(dev.id), detail=f'hostname={dev.hostname}, ip={dev.ip}')
    return jsonify(dev.to_dict())


@app.route('/api/devices/batch-delete', methods=['POST'])
def batch_delete_devices():
    """批量删除设备"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权批量删除设备，请使用全局用户名登录。'}), 403
    data = request.get_json()
    ids = data.get('ids') if isinstance(data, dict) else []
    if not ids or not isinstance(ids, list):
        return jsonify({'error': '请提供 ids 数组'}), 400
    count = Device.query.filter(Device.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    _write_audit('batch_delete_devices', resource_type='device', resource_id='', detail=f'ids={len(ids)}, deleted={count}')
    return jsonify({'ok': True, 'deleted': count})


@app.route('/api/devices/delete-all', methods=['POST'])
def delete_all_devices():
    """清空全部设备（用于真正清空列表后再做自动发现等）"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权操作，请使用管理员账号登录。'}), 403
    # 与列表接口一致：若存在分组权限则只删除可见范围内的设备
    q = Device.query
    allowed_grps = _current_user_allowed_groups()
    if allowed_grps is not None:
        from sqlalchemy import or_
        grp_cond = Device.device_group.in_(allowed_grps)
        if '（未分组）' in allowed_grps:
            grp_cond = or_(grp_cond, Device.device_group.is_(None), Device.device_group == '')
        q = q.filter(grp_cond)
    count = q.delete(synchronize_session=False)
    db.session.commit()
    _write_audit('delete_all_devices', resource_type='device', resource_id='', detail=f'deleted={count}')
    return jsonify({'ok': True, 'deleted': count})


@app.route('/api/devices/batch-update', methods=['POST'])
def batch_update_devices():
    """批量更新设备：类型、分组、连接方式、端口等。当 ids 仅一个时支持完整字段（ip、hostname、enabled 等）。"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权批量修改设备，请使用管理员账号登录。'}), 403
    data = request.get_json(force=True, silent=True) or {}
    ids = data.get('ids') if isinstance(data.get('ids'), list) else []
    if not ids:
        return jsonify({'error': '请提供设备 id 列表 ids'}), 400
    devices = Device.query.filter(Device.id.in_(ids)).all()
    if not devices:
        return jsonify({'ok': True, 'updated': 0})
    single_full = (len(ids) == 1 and isinstance(data.get('ip'), str) and isinstance(data.get('hostname'), str))
    updated = 0
    for dev in devices:
        changed = False
        if single_full:
            ip_raw = (data.get('ip') or '').strip()
            hostname_raw = (data.get('hostname') or '').strip()
            if ip_raw and hostname_raw:
                if dev.ip != ip_raw:
                    dev.ip = ip_raw
                    changed = True
                if dev.hostname != hostname_raw:
                    dev.hostname = hostname_raw
                    changed = True
            if 'device_type' in data and data['device_type']:
                v = _normalize_device_type(str(data['device_type']))
                if dev.device_type != v:
                    dev.device_type = v
                    changed = True
            if 'enabled' in data:
                v = bool(data['enabled'])
                if dev.enabled != v:
                    dev.enabled = v
                    changed = True
            if 'username' in data:
                v = (data.get('username') or '').strip() or None
                if dev.username != v:
                    dev.username = v
                    changed = True
            if 'password' in data:
                v = data.get('password')
                dev.password = (v.strip() if v and str(v).strip() else None) if v else None
                changed = True  # 视为已修改（留空即清空密码）
            if 'maintenance_start' in data:
                v = (str(data.get('maintenance_start') or '').strip())[:8] or None
                if dev.maintenance_start != v:
                    dev.maintenance_start = v
                    changed = True
            if 'maintenance_end' in data:
                v = (str(data.get('maintenance_end') or '').strip())[:8] or None
                if dev.maintenance_end != v:
                    dev.maintenance_end = v
                    changed = True
        if 'device_type' in data and data['device_type'] is not None and not single_full:
            v = _normalize_device_type(str(data['device_type']))
            if dev.device_type != v:
                dev.device_type = v
                changed = True
        if 'group' in data:
            v = (str(data.get('group') or '').strip())[:64] or None
            if dev.device_group != v:
                dev.device_group = v
                changed = True
        if 'connection_type' in data:
            ct = (str(data.get('connection_type') or '').strip()).upper()
            v = ct if ct in ('TELNET', 'SSH') else None
            if dev.connection_type != v:
                dev.connection_type = v
                changed = True
        if 'ssh_port' in data:
            try:
                sp = data['ssh_port']
                if sp is None or (isinstance(sp, str) and str(sp).strip() == ''):
                    v = None
                else:
                    v = max(1, min(65535, int(sp)))
                if dev.ssh_port != v:
                    dev.ssh_port = v
                    changed = True
            except (TypeError, ValueError):
                pass
        if 'telnet_port' in data:
            try:
                tp = data['telnet_port']
                if tp is None or (isinstance(tp, str) and str(tp).strip() == ''):
                    v = None
                else:
                    v = max(1, min(65535, int(tp)))
                if dev.telnet_port != v:
                    dev.telnet_port = v
                    changed = True
            except (TypeError, ValueError):
                pass
        if 'maintenance_start' in data:
            v = (str(data.get('maintenance_start') or '').strip())[:8] or None
            if dev.maintenance_start != v:
                dev.maintenance_start = v
                changed = True
        if 'maintenance_end' in data:
            v = (str(data.get('maintenance_end') or '').strip())[:8] or None
            if dev.maintenance_end != v:
                dev.maintenance_end = v
                changed = True
        if changed:
            updated += 1
    db.session.commit()
    _write_audit('batch_update_devices', resource_type='device', resource_id='', detail=f'ids={len(ids)}, updated={updated}')
    return jsonify({'ok': True, 'updated': updated})


@app.route('/api/devices/sites')
def list_sites():
    """设备站点列表（从 hostname 前缀提取，用于筛选）"""
    from sqlalchemy import func
    # hostname 形如 sha1.pe1 -> 取 sha1
    rows = Device.query.with_entities(Device.hostname).distinct().all()
    prefixes = set()
    for (h,) in rows:
        if h and '.' in h:
            prefixes.add(h.split('.', 1)[0])
        elif h:
            prefixes.add(h)
    return jsonify({'sites': sorted(prefixes)})


@app.route('/api/devices/import', methods=['POST'])
def import_devices():
    """从 ip_list 格式文本导入，每行: 主机名 管理IP 类型"""
    text = request.get_data(as_text=True) or request.form.get('text', '')
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    count = 0
    for line in lines:
        parts = line.split()
        if len(parts) >= 3:
            hostname, ip, dev_type = parts[0], parts[1], _normalize_device_type(parts[2])
            grp = (parts[3].strip())[:64] if len(parts) > 3 and parts[3].strip() else None
            if Device.query.filter_by(ip=ip, hostname=hostname).first():
                continue
            db.session.add(Device(ip=ip, hostname=hostname, device_type=dev_type, device_group=grp))
            count += 1
    db.session.commit()
    if count > 0:
        _write_audit('import_devices', resource_type='device', resource_id='', detail=f'imported={count}')
    return jsonify({'imported': count})


@app.route('/api/device-groups', methods=['GET'])
def list_device_groups():
    """预定义设备分组列表；?from_devices=1 时合并设备表中实际存在的分组名（含「未分组」）"""
    raw = (_get_setting('device_groups', '') or '').strip()
    groups = [g.strip() for g in raw.split(',') if g.strip()]
    if request.args.get('from_devices'):
        try:
            _ensure_device_group_column()
        except Exception:
            pass
        from_devices = {r[0] for r in Device.query.with_entities(Device.device_group).distinct().all() if r[0]}
        for g in from_devices:
            if g and g.strip() and g.strip() not in groups:
                groups.append(g.strip())
        if '（未分组）' not in groups:
            groups.append('（未分组）')
        groups = sorted(groups)
    return jsonify({'groups': groups})


@app.route('/api/device-groups', methods=['POST'])
def create_device_group():
    """创建设备分组：添加一个预定义分组名"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权操作，请使用管理员账号登录。'}), 403
    data = request.get_json(force=True, silent=True) or {}
    name = (str(data.get('name') or '').strip())[:64]
    if not name:
        return jsonify({'error': '分组名称不能为空。'}), 400
    raw = (_get_setting('device_groups', '') or '').strip()
    current = [g.strip() for g in raw.split(',') if g.strip()]
    if name in current:
        return jsonify({'error': '该分组已存在。', 'groups': current}), 400
    current.append(name)
    _set_setting('device_groups', ','.join(sorted(current)))
    return jsonify({'ok': True, 'groups': sorted(current)})


@app.route('/api/device-groups/<path:name>', methods=['DELETE'])
def delete_device_group(name):
    """删除预定义分组名（仅从列表中移除，不修改已属该分组的设备）"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权操作，请使用管理员账号登录。'}), 403
    name = name.strip()
    raw = (_get_setting('device_groups', '') or '').strip()
    current = [g.strip() for g in raw.split(',') if g.strip()]
    if name not in current:
        return jsonify({'ok': True, 'groups': [g for g in current]})
    current = [g for g in current if g != name]
    _set_setting('device_groups', ','.join(current))
    return jsonify({'ok': True, 'groups': current})


def _check_port_open(ip: str, port: int, timeout: float = 1.0) -> bool:
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        r = s.connect_ex((ip, port))
        s.close()
        return r == 0
    except Exception:
        return False


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


@app.route('/api/devices/discover', methods=['POST'])
def discover_devices():
    """设备发现：扫描 IP 列表或 IP 段，检测 22/23 端口是否开放，返回可达结果供批量添加"""
    data = request.get_json(force=True, silent=True) or {}
    ips = []
    if data.get('ip_range'):
        r = (data.get('ip_range') or '').strip()
        if '-' in r:
            a, b = r.split('-', 1)
            a, b = a.strip(), b.strip()
            try:
                import ipaddress
                start = int(ipaddress.ip_address(a))
                end = int(ipaddress.ip_address(b))
                for i in range(start, min(end + 1, start + 256)):
                    ips.append(str(ipaddress.ip_address(i)))
            except Exception:
                pass
        else:
            try:
                import ipaddress
                n = ipaddress.ip_network(r, strict=False)
                for addr in list(n.hosts())[:256]:
                    ips.append(str(addr))
            except Exception:
                pass
    if data.get('ips'):
        ips.extend([x.strip() for x in (data.get('ips') or '').splitlines() if x.strip()])
    ips = list(dict.fromkeys(ips))[:128]
    if not ips:
        return jsonify({'error': '请提供 ip_range（如 192.168.1.1-192.168.1.20 或 192.168.1.0/24）或 ips（每行一个 IP）'}), 400
    results = []
    for ip in ips:
        ssh_open = _check_port_open(ip, 22)
        telnet_open = _check_port_open(ip, 23)
        if ssh_open or telnet_open:
            results.append({'ip': ip, 'ssh_open': ssh_open, 'telnet_open': telnet_open})
    return jsonify({'results': results, 'scanned': len(ips)})


@app.route('/api/discovery/settings', methods=['GET', 'PUT'])
def discovery_settings():
    """自动发现 / SNMP 全局设置。"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权修改设置，请使用管理员账号登录。'}), 403
    if request.method == 'GET':
        return jsonify({
            'snmp_version': _get_setting('snmp_version', '2c'),
            'snmp_community': _get_setting('snmp_community', 'public'),
            'snmp_timeout_ms': _get_setting('snmp_timeout_ms', '2000'),
            'snmp_retries': _get_setting('snmp_retries', '1'),
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
    """自动发现规则列表 & 新建。"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权操作，请使用管理员账号登录。'}), 403
    if request.method == 'GET':
        rules = AutoDiscoveryRule.query.order_by(AutoDiscoveryRule.id.desc()).all()
        items = []
        for r in rules:
            d = r.to_dict()
            # 若未单独配置设备类型 OID，则在列表展示中采用兜底默认值，方便用户查看规则实际使用的 OID
            if not (d.get('device_type_oid') or '').strip():
                d['device_type_oid'] = '1.3.6.1.2.1.1.1.0'  # sysDescr 作为默认设备类型 OID
            items.append(d)
        return jsonify({'rules': items})
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


def _snmp_get(ip: str, oid: str, community: str, timeout_ms: int, retries: int):
    """简单 SNMP GET，返回字符串或 None。"""
    try:
        from pysnmp.hlapi import (
            SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
            ObjectType, ObjectIdentity, getCmd,
        )
    except Exception:
        return None
    try:
        iterator = getCmd(
            SnmpEngine(),
            CommunityData(community or 'public'),
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


def _execute_discovery_rule(rule_id):
    """内部：执行某条自动发现规则，不校验权限。返回 dict(ok, scanned, added_count, added, skipped, log_id, error)。"""
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

    try:
        # 强制使用最新设备表：结束可能存在的旧事务并清空会话缓存（定时任务线程可能沿用旧会话）
        db.session.rollback()
        db.session.expire_all()
        for ip in _iter_ip_ranges(rule.ip_range, limit=256):
            scanned_ips.append(ip)
            hostname = _snmp_get(ip, hostname_oid, community, timeout_ms, retries) or ''
            hostname = hostname.strip()
            # 如果 SNMP 未返回主机名，则回退为 IP，避免因为 no_hostname 直接跳过可达设备
            if not hostname:
                hostname = ip
            # 主机名过滤：取「前 N 段」拼成主机名（域名格式如 SHA1.PE1 或 SHA1.PE2.example.com，取第2段即取前2段得 SHA1.PE1 / SHA1.PE2）
            split_char = (_get_setting('discovery_hostname_split_char', '') or '').strip()
            try:
                # 默认取前 2 段拼成主机名（如 sha1.pe1.example.com -> sha1.pe1）
                seg_one_based = int(_get_setting('discovery_hostname_segment_index', '2') or '2')
                seg_one_based = max(1, min(seg_one_based, 20))
            except (TypeError, ValueError):
                seg_one_based = 1
            if split_char and seg_one_based >= 1 and hostname:
                parts = hostname.split(split_char)
                taken = parts[:seg_one_based]
                hostname = split_char.join(taken).strip()
            dev_type_raw = ''
            if device_type_oid:
                dev_type_raw = (_snmp_get(ip, device_type_oid, community, timeout_ms, retries) or '').strip()
            # 根据 SNMP 返回内容与设备类型配置做模糊匹配，例如包含 "Cisco" / "Huawei" / "Juniper" 等关键字则自动归类
            dev_type = _detect_device_type_from_snmp(dev_type_raw)
            if not dev_type:
                # 若未配置 OID 或获取失败，或无法匹配到任何已知类型，则默认成 Cisco，后续由人工编辑修正
                dev_type = 'Cisco'
            # 检查是否已存在：仅按 IP 判重，同一 IP 不重复添加；不同 IP 允许相同主机名（多台设备 SNMP 可能返回相同 hostname）
            existing = Device.query.filter_by(ip=ip).first()
            if existing:
                skipped.append({'ip': ip, 'hostname': hostname, 'reason': 'exists'})
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
    """执行某条自动发现规则（API 入口，校验权限）"""
    _ensure_tables()
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权操作，请使用管理员账号登录。'}), 403
    resp = _execute_discovery_rule(rule_id)
    return jsonify(resp), (200 if resp.get('ok') else 500)


@app.route('/api/discovery/rules/<int:rule_id>/logs', methods=['GET'])
def list_discovery_rule_logs(rule_id):
    """某条自动发现规则的最近运行日志列表。"""
    _ensure_tables()
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权查看自动发现日志，请使用管理员账号登录。'}), 403
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
            # 为了行为与「单台立即备份」保持一致，并避免多线程可能带来的问题，
            # 这里改为在当前后台线程中串行执行所有设备备份（对用户仍然是异步的）。
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
                app_context=None,
                type_configs=type_configs,
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
                    _save_config_changes_to_db()
                except Exception as e:
                    app.logger.warning('保存配置变动到数据库失败: %s', e)
                # 备份失败告警 Webhook（带重试与失败日志）
                if job_to_save.get('fail', 0) > 0:
                    webhook = (_get_setting('backup_failure_webhook', '') or '').strip()
                    if webhook and webhook.startswith(('http://', 'https://')):
                        total = job_to_save.get('total', 0)
                        ok = job_to_save.get('ok', 0)
                        fail = job_to_save.get('fail', 0)
                        msg = '【vConfig 备份告警】任务 %s 完成：共 %d 台，成功 %d 台，失败 %d 台。' % (
                            job_to_save.get('id', ''), total, ok, fail
                        )
                        body = _webhook_body_for_url(webhook, msg, {
                            'event': 'backup_failure',
                            'job_id': job_to_save.get('id'),
                            'total': total,
                            'ok': ok,
                            'fail': fail,
                            'end_time': job_to_save.get('end_time'),
                        })
                        _call_webhook_with_retry(webhook, body, max_retries=3)
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


@app.route('/api/backup/status')
def backup_status():
    _ensure_tables()
    with _backup_lock:
        memory_jobs = [dict(j) for j in _backup_jobs]
        current = dict(_current_job) if _current_job else None
    memory_ids = {j['id'] for j in memory_jobs}
    db_runs = BackupJobRun.query.order_by(BackupJobRun.id.desc()).limit(_MAX_BACKUP_JOBS).all()
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
    default_user = _get_setting('username', DEFAULT_USERNAME)
    default_pass = _get_setting('password', DEFAULT_PASSWORD)

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
                # 单台备份完成后计算「配置变动」并写入数据库
                try:
                    _save_config_changes_to_db()
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
                app_context=app.app_context(),
                type_configs=type_configs,
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
                        _save_config_changes_to_db()
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


# ---------- 日志 ----------
@app.route('/api/logs', methods=['GET'])
def list_logs():
    """备份日志列表"""
    page = request.args.get('page', 1, type=int)
    default_pp = int(_get_setting('log_per_page_default', '50') or '50')
    per_page = min(request.args.get('per_page', default_pp, type=int), 100)
    hostname = request.args.get('hostname', '').strip()
    status = request.args.get('status', '').strip()
    device_id = request.args.get('device_id', type=int)
    sort_by = (request.args.get('sort_by') or 'created_at').strip()
    sort_dir = (request.args.get('sort_dir') or 'desc').strip().lower()
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'

    order_cols = {
        'created_at': BackupLog.created_at,
        'hostname': BackupLog.hostname,
        'ip': BackupLog.ip,
        'device_type': BackupLog.device_type,
        'status': BackupLog.status,
        'duration_seconds': BackupLog.duration_seconds,
    }
    order_col = order_cols.get(sort_by, BackupLog.created_at)
    if sort_dir == 'desc':
        q = BackupLog.query.order_by(order_col.desc())
    else:
        q = BackupLog.query.order_by(order_col.asc())

    if hostname:
        q = q.filter(BackupLog.hostname.ilike(f'%{hostname}%'))
    if device_id:
        dev = Device.query.get(device_id)
        if dev:
            q = q.filter(BackupLog.hostname == dev.hostname)
    if status:
        q = q.filter(BackupLog.status == status)
    pagination = q.paginate(page=page, per_page=per_page)
    timezone = _get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    return jsonify({
        'items': [x.to_dict() for x in pagination.items],
        'total': pagination.total,
        'page': page,
        'per_page': per_page,
        'timezone': timezone,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
    })


# ---------- 设备历史备份 ----------
@app.route('/api/devices/<int:pk>/history')
def device_backup_history(pk):
    """某设备的备份历史（按 hostname 查日志）"""
    dev = Device.query.get_or_404(pk)
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 50)
    q = BackupLog.query.filter(BackupLog.hostname == dev.hostname).order_by(BackupLog.created_at.desc())
    pagination = q.paginate(page=page, per_page=per_page)
    timezone = _get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    return jsonify({
        'hostname': dev.hostname,
        'items': [x.to_dict() for x in pagination.items],
        'total': pagination.total,
        'page': page,
        'per_page': per_page,
        'timezone': timezone,
    })


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
        logo_url = url_for('logo') if logo_file else ''
        return {
            # 系统名称：用于顶部品牌区与登录页展示
            'system_name': _get_setting('system_name', '配置备份中心') or '配置备份中心',
            'footer_text': _get_setting('footer_text', '') or '',
            'footer_timezone': _get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
            'footer_client_ip': client_ip,
            'current_user': session.get('user') or '',
            'static_version': 2,  # 静态资源版本，改版后递增以强制浏览器拉取最新 JS/CSS
            'logo_url': logo_url,
        }
    except Exception:
        return {
            'system_name': '配置备份中心',
            'footer_text': '',
            'footer_timezone': DEFAULT_TIMEZONE,
            'footer_client_ip': '',
            'current_user': session.get('user') or '',
            'static_version': 2,
            'logo_url': '',
        }


@app.route('/api/footer-info')
def footer_info():
    """页脚所需：来访者 IP、时区、自定义文案（版权/备案号等）"""
    _ensure_tables()
    raw = request.headers.get('X-Forwarded-For') or request.headers.get('X-Real-IP') or (getattr(request, 'remote_addr', None) or '')
    client_ip = (raw.split(',')[0].strip() if isinstance(raw, str) and ',' in raw else raw) or ''
    if not isinstance(client_ip, str):
        client_ip = str(client_ip)
    return jsonify({
        'client_ip': client_ip,
        'timezone': _get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
        'footer_text': _get_setting('footer_text', '') or '',
    })


@app.route('/api/settings/logo', methods=['POST', 'DELETE'])
def settings_logo():
    """上传或删除系统 Logo（仅允许有全局设置权限的用户操作）。"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权修改全局设置，请使用全局用户名登录。'}), 403
    _ensure_tables()
    # 删除 Logo
    if request.method == 'DELETE':
        filename = _get_setting('logo_file', '') or ''
        if filename:
            safe_name = os.path.basename(filename)
            file_path = os.path.join(LOGO_DIR, safe_name)
            if os.path.isfile(file_path):
                try:
                    os.unlink(file_path)
                except OSError:
                    pass
        _set_setting('logo_file', '')
        _write_audit('update_settings', resource_type='settings', resource_id='', detail='logo reset to default')
        return jsonify({'ok': True})

    # 上传 Logo
    file = request.files.get('file')
    if not file or file.filename == '':
        return jsonify({'error': '未选择文件。'}), 400
    # 简单校验扩展名
    name = file.filename or ''
    ext = os.path.splitext(name)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico'):
        return jsonify({'error': '仅支持 PNG/JPG/GIF/WebP/ICO 格式的图片。'}), 400
    # 限制大小（约 512KB）
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > 512 * 1024:
        return jsonify({'error': 'Logo 文件过大，请控制在 512KB 以内。'}), 400
    os.makedirs(LOGO_DIR, exist_ok=True)
    # 超过 64×64 时按比例缩小到 64×64 以内（不拉伸）；在 64×64 以内则保持原尺寸
    try:
        from PIL import Image
        file.stream.seek(0)
        img = Image.open(file.stream).convert('RGBA')
        w, h = img.size
        if w > LOGO_MAX_SIZE[0] or h > LOGO_MAX_SIZE[1]:
            img.thumbnail(LOGO_MAX_SIZE, Image.Resampling.LANCZOS)
            save_ext = '.png'
        else:
            save_ext = ext
        new_name = datetime.utcnow().strftime('%Y%m%d%H%M%S') + save_ext
        dest_path = os.path.join(LOGO_DIR, new_name)
        old = _get_setting('logo_file', '') or ''
        if old:
            old_path = os.path.join(LOGO_DIR, os.path.basename(old))
            if os.path.isfile(old_path):
                try:
                    os.unlink(old_path)
                except OSError:
                    pass
        fmt_map = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.gif': 'GIF', '.webp': 'WEBP', '.ico': 'PNG'}
        fmt = fmt_map.get(save_ext, 'PNG')
        if fmt == 'JPEG':
            img.convert('RGB').save(dest_path, 'JPEG', quality=90)
        else:
            img.save(dest_path, fmt)
    except ImportError:
        return jsonify({'error': 'Logo 尺寸处理需要安装 Pillow，请执行 pip install Pillow。'}), 500
    except Exception:
        file.stream.seek(0)
        new_name = datetime.utcnow().strftime('%Y%m%d%H%M%S') + ext
        dest_path = os.path.join(LOGO_DIR, new_name)
        old = _get_setting('logo_file', '') or ''
        if old:
            old_path = os.path.join(LOGO_DIR, os.path.basename(old))
            if os.path.isfile(old_path):
                try:
                    os.unlink(old_path)
                except OSError:
                    pass
        file.save(dest_path)
    _set_setting('logo_file', new_name)
    _write_audit('update_settings', resource_type='settings', resource_id='', detail='logo updated')
    return jsonify({'ok': True, 'logo_url': url_for('logo')})

@app.route('/logo')
def logo():
    """返回当前自定义 Logo 文件（若存在）。"""
    _ensure_tables()
    filename = _get_setting('logo_file', '') or ''
    if not filename:
        return ('', 404)
    safe_name = os.path.basename(filename)
    dir_path = LOGO_DIR
    file_path = os.path.join(dir_path, safe_name)
    if not os.path.isfile(file_path):
        return ('', 404)
    return send_from_directory(dir_path, safe_name)


# ---------- 设置 ----------
@app.route('/api/settings', methods=['GET'])
def get_settings():
    # 自动发现类型关键字：若用户尚未自定义，则使用一份内置规则展示给前端
    discovery_type_keywords = _get_setting('discovery_type_keywords', '') or ''
    if not discovery_type_keywords:
        # 使用你提供的内置默认规则
        discovery_type_keywords = "\n".join([
            "Cisco=Cisco,IOS,ISR,ASR,NCS",
            "Juniper=Juniper,JUNOS",
            "Huawei=Huawei,FutureMatrix,VRP",
            "H3C=H3C",
            "RouterOS=RouterOS,MikroTik,CHR",
        ])

    return jsonify({
        # 全局 Telnet/SSH 账号默认留空，仅从设置中读取
        'username': _get_setting('username', ''),
        'password': _get_setting('password', ''),
        'system_name': _get_setting('system_name', '配置备份中心'),
        'backup_frequency': _get_setting('backup_frequency', 'daily') or 'daily',
        'default_connection_type': _get_setting('default_connection_type', DEFAULT_CONNECTION_TYPE).upper() or 'SSH',
        'backup_retention_days': _get_setting('backup_retention_days', str(BACKUP_RETENTION_DAYS)),
        'timezone': _get_setting('timezone', DEFAULT_TIMEZONE),
        'footer_text': _get_setting('footer_text', ''),
        'logo_enabled': '1' if _get_setting('logo_file', '') else '0',
        'logo_url': url_for('logo') if _get_setting('logo_file', '') else '',
        'logo_enabled': bool(_get_setting('logo_file', '') or ''),
        # 通用：会话与安全
        'session_timeout_minutes': _get_setting('session_timeout_minutes', '0'),
        'login_lockout_attempts': _get_setting('login_lockout_attempts', '0'),
        'login_lockout_minutes': _get_setting('login_lockout_minutes', '15'),
        'password_min_length': _get_setting('password_min_length', '6'),
        'password_require_digit': _get_setting('password_require_digit', '0'),
        'password_require_upper': _get_setting('password_require_upper', '0'),
        'password_require_lower': _get_setting('password_require_lower', '0'),
        'password_require_special': _get_setting('password_require_special', '0'),
        'device_per_page_default': _get_setting('device_per_page_default', '50'),
        'log_per_page_default': _get_setting('log_per_page_default', '50'),
        # 自动发现 / SNMP 全局设置
        'snmp_version': _get_setting('snmp_version', '2c'),
        'snmp_community': _get_setting('snmp_community', 'public'),
        'snmp_timeout_ms': _get_setting('snmp_timeout_ms', '2000'),
        'snmp_retries': _get_setting('snmp_retries', '1'),
        # 备份：超时/线程/端口/告警
        'backup_timeout_seconds': _get_setting('backup_timeout_seconds', '30'),
        'backup_thread_num': _get_setting('backup_thread_num', str(BACKUP_THREAD_NUM)),
        'ssh_port': _get_setting('ssh_port', str(SSH_PORT)),
        'telnet_port': _get_setting('telnet_port', '23'),
        'backup_failure_webhook': _get_setting('backup_failure_webhook', ''),
        'api_tokens': _get_setting('api_tokens', ''),
        # 自动发现设置
        'discovery_frequency': _get_setting('discovery_frequency', 'twice_daily'),
        'discovery_type_keywords': discovery_type_keywords,
        'discovery_hostname_split_char': _get_setting('discovery_hostname_split_char', '.'),
        'discovery_hostname_segment_index': _get_setting('discovery_hostname_segment_index', '2'),
        # LDAP
        'ldap_enabled': _get_setting('ldap_enabled', '0'),
        'ldap_server': _get_setting('ldap_server', ''),
        'ldap_base_dn': _get_setting('ldap_base_dn', ''),
        'ldap_bind_dn': _get_setting('ldap_bind_dn', ''),
        'ldap_bind_password': _get_setting('ldap_bind_password', ''),
        'ldap_user_filter': _get_setting('ldap_user_filter', '(uid={username})'),
        'can_edit_settings': _can_edit_settings(),
    })


# ---------- 用户管理（基础版：列表 + 更新角色/启用状态） ----------
@app.route('/api/users', methods=['GET'])
def list_users_api():
    """用户列表：仅管理员可查看；内置超级管理员永远排最前"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权查看用户列表，请使用管理员账号登录。'}), 403
    from sqlalchemy import case
    users = User.query.order_by(
        case((User.username == SUPER_ADMIN_USERNAME, 0), else_=1),
        User.created_at.desc(),
    ).all()
    return jsonify({
        'items': [u.to_dict() for u in users],
    })


@app.route('/api/users', methods=['POST'])
def create_user_api():
    """新建本地账号：仅管理员可操作"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权新建用户，请使用管理员账号登录。'}), 403
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get('username') or '').strip()
    if not username:
        return jsonify({'error': '用户名不能为空。'}), 400
    if username == SUPER_ADMIN_USERNAME:
        return jsonify({'error': '内置超级管理员账号已存在，无需重复创建。'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': '该用户名已存在，请换一个。'}), 400
    role = (data.get('role') or 'viewer').strip()
    if role not in ('admin', 'ops', 'viewer'):
        return jsonify({'error': '角色不合法，仅支持 admin / ops / viewer。'}), 400
    is_active = bool(data.get('is_active', True))
    password = (data.get('password') or '').strip()
    if not password:
        return jsonify({'error': '请为本地账号设置登录密码。'}), 400
    ok_pwd, msg_pwd = _check_password_policy(password)
    if not ok_pwd:
        return jsonify({'error': msg_pwd}), 400
    display_name = (str(data.get('display_name') or '')[:128]) or None
    email = (str(data.get('email') or '').strip())[:128] or None
    phone = (str(data.get('phone') or '').strip())[:32] or None
    allowed_grps = (str(data.get('allowed_groups') or '').strip())[:512] or None
    u = User(
        username=username[:128],
        display_name=display_name,
        email=email,
        phone=phone,
        source='local',
        role=role,
        is_active=is_active,
        allowed_groups=allowed_grps,
    )
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return jsonify(u.to_dict()), 201


@app.route('/api/users/<int:user_id>', methods=['PUT'])
def update_user_api(user_id):
    """更新用户角色与启用状态：仅管理员可操作"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权修改用户信息，请使用管理员账号登录。'}), 403
    u = User.query.get_or_404(user_id)
    data = request.get_json(force=True, silent=True) or {}

    # 目标角色与启用状态（若未提供则使用原值），用于判断是否会导致系统无管理员
    new_role_raw = (data.get('role') or '').strip()
    if new_role_raw and new_role_raw not in ('admin', 'ops', 'viewer'):
        return jsonify({'error': '角色不合法，仅支持 admin / ops / viewer。'}), 400
    target_role = new_role_raw or (u.role or 'viewer')
    target_active = bool(data['is_active']) if 'is_active' in data else bool(u.is_active)

    # 如果当前是「启用状态的管理员」且本次修改会使其不再是启用管理员，需要检查是否还有其他启用管理员
    if (u.role == 'admin' and u.is_active) and (target_role != 'admin' or not target_active):
        other_admins = (
            User.query
            .filter(User.id != u.id, User.role == 'admin', User.is_active == True)  # noqa: E712
            .count()
        )
        if other_admins == 0:
            return jsonify({'error': '系统中至少需要保留一个启用状态的管理员账号，此操作会导致没有任何管理员，请先为其他用户设置管理员角色。'}), 400

    # 通过校验后再真正写入
    if new_role_raw:
        u.role = new_role_raw

    if 'is_active' in data:
        u.is_active = target_active

    if 'display_name' in data:
        u.display_name = (str(data.get('display_name') or '')[:128]) or None
    if 'email' in data:
        u.email = (str(data.get('email') or '').strip())[:128] or None
    if 'phone' in data:
        u.phone = (str(data.get('phone') or '').strip())[:32] or None
    if 'allowed_groups' in data:
        u.allowed_groups = (str(data.get('allowed_groups') or '').strip())[:512] or None

    # 本地账号支持在用户管理中重置密码；LDAP 账号不允许设置本地密码
    if 'password' in data:
        raw = (data.get('password') or '').strip()
        if raw:
            if (u.source or 'local') != 'local':
                return jsonify({'error': '不能为 LDAP 用户设置本地密码。'}), 400
            ok_pwd, msg_pwd = _check_password_policy(raw)
            if not ok_pwd:
                return jsonify({'error': msg_pwd}), 400
            u.set_password(raw)

    db.session.commit()
    return jsonify(u.to_dict())


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def delete_user_api(user_id):
    """删除用户：仅管理员可操作，且必须保留至少一个启用的管理员"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权删除用户，请使用管理员账号登录。'}), 403
    u = User.query.get_or_404(user_id)
    # 内置超级管理员账号禁止删除
    if u.username == SUPER_ADMIN_USERNAME:
        return jsonify({'error': '内置超级管理员账号不能删除。'}), 400
    # 若要删除的是一个启用状态的管理员，需要检查是否还有其他启用管理员
    if u.role == 'admin' and u.is_active:
        other_admins = (
            User.query
            .filter(User.id != u.id, User.role == 'admin', User.is_active == True)  # noqa: E712
            .count()
        )
        if other_admins == 0:
            return jsonify({'error': '系统中至少需要保留一个启用状态的管理员账号，无法删除最后一个管理员。'}), 400
    db.session.delete(u)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/device-types', methods=['GET'])
def list_device_types_api():
    """设备类型列表（供前端下拉使用，仅返回启用的类型）"""
    _ensure_tables()
    # 若 query 参数 include_disabled=1，则返回全部，否则仅返回启用的类型
    include_disabled = request.args.get('include_disabled') in ('1', 'true', 'yes')
    q = DeviceTypeConfig.query
    if not include_disabled:
        q = q.filter_by(enabled=True)
    types = q.order_by(DeviceTypeConfig.sort_order.asc(), DeviceTypeConfig.type_code.asc()).all()
    items = [t.to_dict() for t in types]
    return jsonify({'items': items})


@app.route('/api/device-types', methods=['POST'])
def create_device_type_api():
    """新增设备类型，仅允许有设置权限的用户操作"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权管理设备类型，请使用管理员账号登录。'}), 403
    _ensure_tables()
    data = request.get_json(force=True, silent=True) or {}
    type_code = (data.get('type_code') or '').strip()
    display_name = (data.get('display_name') or '').strip()
    driver_type = (data.get('driver_type') or 'generic').strip() or 'generic'
    driver_module = (data.get('driver_module') or '').strip() or None
    enabled = bool(data.get('enabled', True))
    backup_config = data.get('backup_config') or {}
    connection_config = data.get('connection_config') or {}
    if not type_code:
        return jsonify({'error': '类型代码不能为空。'}), 400
    if not display_name:
        return jsonify({'error': '显示名称不能为空。'}), 400
    if DeviceTypeConfig.query.filter_by(type_code=type_code).first():
        return jsonify({'error': '该类型代码已存在，请勿重复创建。'}), 400
    if driver_type not in ('builtin', 'generic', 'custom'):
        return jsonify({'error': '驱动类型不合法，仅支持 builtin/generic/custom。'}), 400
    from sqlalchemy import func
    max_order = db.session.query(func.max(DeviceTypeConfig.sort_order)).scalar()
    sort_order = (max_order or 0) + 1
    cfg = DeviceTypeConfig(
        type_code=type_code,
        display_name=display_name,
        driver_type=driver_type,
        driver_module=driver_module,
        sort_order=sort_order,
        enabled=enabled,
    )
    try:
        if isinstance(backup_config, dict):
            cfg.set_backup_config(backup_config)
        if isinstance(connection_config, dict):
            cfg.set_connection_config(connection_config)
        db.session.add(cfg)
        db.session.commit()
        return jsonify(cfg.to_dict()), 201
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': '保存设备类型失败: %s' % e}), 500


@app.route('/api/device-types/<int:type_id>', methods=['PUT'])
def update_device_type_api(type_id):
    """更新设备类型配置，仅允许有设置权限的用户操作"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权管理设备类型，请使用管理员账号登录。'}), 403
    _ensure_tables()
    cfg = DeviceTypeConfig.query.get_or_404(type_id)
    data = request.get_json(force=True, silent=True) or {}
    # type_code 不允许随意修改，否则会导致已有设备记录关联出错
    display_name = (data.get('display_name') or cfg.display_name or '').strip()
    driver_type = (data.get('driver_type') or cfg.driver_type or 'generic').strip() or 'generic'
    driver_module = (data.get('driver_module') or cfg.driver_module or '').strip() or None
    sort_order = data.get('sort_order', cfg.sort_order)
    enabled = bool(data.get('enabled', cfg.enabled))
    backup_config = data.get('backup_config')
    connection_config = data.get('connection_config')
    if not display_name:
        return jsonify({'error': '显示名称不能为空。'}), 400
    if driver_type not in ('builtin', 'generic', 'custom'):
        return jsonify({'error': '驱动类型不合法，仅支持 builtin/generic/custom。'}), 400
    try:
        sort_order = int(sort_order)
    except (TypeError, ValueError):
        sort_order = cfg.sort_order or 0
    cfg.display_name = display_name
    cfg.driver_type = driver_type
    cfg.driver_module = driver_module
    cfg.sort_order = sort_order
    cfg.enabled = enabled
    if isinstance(backup_config, dict):
        cfg.set_backup_config(backup_config)
    if isinstance(connection_config, dict):
        cfg.set_connection_config(connection_config)
    try:
        db.session.commit()
        return jsonify(cfg.to_dict())
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': '更新设备类型失败: %s' % e}), 500


@app.route('/api/device-types/<int:type_id>', methods=['DELETE'])
def delete_device_type_api(type_id):
    """删除设备类型：仅当没有设备使用该类型时允许删除"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权管理设备类型，请使用管理员账号登录。'}), 403
    _ensure_tables()
    cfg = DeviceTypeConfig.query.get_or_404(type_id)
    # 内置类型（核心厂商）禁止删除，只允许禁用
    builtin_codes = {'Cisco', 'Juniper', 'Huawei', 'H3C', 'RouterOS'}
    if (cfg.type_code or '').strip() in builtin_codes:
        return jsonify({'error': '内置设备类型不可删除，如需隐藏可在界面中禁用。'}), 400
    # 检查是否有设备正在使用该类型
    used_count = Device.query.filter_by(device_type=cfg.type_code).count()
    if used_count > 0:
        return jsonify({'error': '当前仍有 %d 台设备使用该类型，无法删除。可先在设备列表中修改设备类型，或仅将该类型禁用。' % used_count}), 400
    try:
        db.session.delete(cfg)
        db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': '删除设备类型失败: %s' % e}), 500

@app.route('/api/settings', methods=['PUT'])
def update_settings():
    # 权限控制：只有通过本地全局用户名登录的用户才能修改设置
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权修改全局设置，请使用全局用户名登录。'}), 403

    data = request.get_json(force=True, silent=True) or {}
    if data.get('username') is not None:
        _set_setting('username', str(data['username']))
    if data.get('password') is not None:
        _set_setting('password', str(data['password']))
    if data.get('backup_frequency') is not None:
        _set_setting('backup_frequency', str(data['backup_frequency']))
    if data.get('default_connection_type') is not None:
        ct = str(data['default_connection_type']).strip().upper()
        _set_setting('default_connection_type', ct if ct in ('TELNET', 'SSH') else 'TELNET')
    if data.get('system_name') is not None:
        # 系统名称允许用户自定义，做长度限制并提供合理默认值
        name = (str(data.get('system_name') or '').strip())[:100]
        _set_setting('system_name', name or '配置备份中心')
    if data.get('backup_retention_days') is not None:
        try:
            v = int(data['backup_retention_days'])
            _set_setting('backup_retention_days', str(max(0, min(3650, v))))
        except (TypeError, ValueError):
            _set_setting('backup_retention_days', str(BACKUP_RETENTION_DAYS))
    if data.get('timezone') is not None:
        _set_setting('timezone', str(data['timezone']).strip() or DEFAULT_TIMEZONE)
    # 页脚文案：每次保存都写入（保证可重复保存）
    _set_setting('footer_text', (str(data.get('footer_text', '') or '').strip())[:500])
    # LDAP 参数
    if 'ldap_enabled' in data:
        enabled = str(data.get('ldap_enabled') or '0').strip()
        _set_setting('ldap_enabled', '1' if enabled in ('1', 'true', 'on', 'yes') else '0')
    if 'ldap_server' in data:
        _set_setting('ldap_server', str(data.get('ldap_server') or '').strip())
    if 'ldap_base_dn' in data:
        _set_setting('ldap_base_dn', str(data.get('ldap_base_dn') or '').strip())
    if 'ldap_bind_dn' in data:
        _set_setting('ldap_bind_dn', str(data.get('ldap_bind_dn') or '').strip())
    if 'ldap_bind_password' in data:
        # 不做特殊加密，后续可考虑集成密钥管理
        _set_setting('ldap_bind_password', str(data.get('ldap_bind_password') or ''))
    if 'ldap_user_filter' in data:
        _set_setting('ldap_user_filter', str(data.get('ldap_user_filter') or '(uid={username})').strip())
    # 通用：会话与安全
    if data.get('session_timeout_minutes') is not None:
        v = data['session_timeout_minutes']
        try:
            n = int(v) if v != '' else 0
            _set_setting('session_timeout_minutes', str(max(0, min(1440, n))))
        except (TypeError, ValueError):
            _set_setting('session_timeout_minutes', '0')
    if data.get('login_lockout_attempts') is not None:
        try:
            n = int(data['login_lockout_attempts'])
            _set_setting('login_lockout_attempts', str(max(0, min(20, n))))
        except (TypeError, ValueError):
            _set_setting('login_lockout_attempts', '0')
    if data.get('login_lockout_minutes') is not None:
        try:
            n = int(data['login_lockout_minutes'])
            _set_setting('login_lockout_minutes', str(max(0, min(120, n))))
        except (TypeError, ValueError):
            _set_setting('login_lockout_minutes', '15')
    if data.get('password_min_length') is not None:
        try:
            n = int(data['password_min_length'])
            _set_setting('password_min_length', str(max(6, min(32, n))) if n else '')
        except (TypeError, ValueError):
            _set_setting('password_min_length', '6')
    for key in ('password_require_digit', 'password_require_upper', 'password_require_lower', 'password_require_special'):
        if key in data:
            _set_setting(key, '1' if data.get(key) in (True, 1, '1', 'true', 'on') else '0')
    if data.get('device_per_page_default') is not None:
        v = str(data.get('device_per_page_default') or '50').strip()
        if v in ('20', '50', '100', '200'):
            _set_setting('device_per_page_default', v)
    if data.get('log_per_page_default') is not None:
        v = str(data.get('log_per_page_default') or '50').strip()
        if v in ('20', '50', '100'):
            _set_setting('log_per_page_default', v)
    # 备份：超时/线程/端口/Webhook
    if data.get('backup_timeout_seconds') is not None:
        try:
            n = int(data['backup_timeout_seconds'])
            _set_setting('backup_timeout_seconds', str(max(5, min(300, n))))
        except (TypeError, ValueError):
            _set_setting('backup_timeout_seconds', '30')
    if data.get('backup_thread_num') is not None:
        try:
            n = int(data['backup_thread_num'])
            _set_setting('backup_thread_num', str(max(1, min(50, n))))
        except (TypeError, ValueError):
            _set_setting('backup_thread_num', str(BACKUP_THREAD_NUM))
    if data.get('ssh_port') is not None:
        try:
            n = int(data['ssh_port'])
            _set_setting('ssh_port', str(max(1, min(65535, n))))
        except (TypeError, ValueError):
            _set_setting('ssh_port', str(SSH_PORT))
    if data.get('telnet_port') is not None:
        try:
            n = int(data['telnet_port'])
            _set_setting('telnet_port', str(max(1, min(65535, n))))
        except (TypeError, ValueError):
            _set_setting('telnet_port', '23')
    if 'backup_failure_webhook' in data:
        _set_setting('backup_failure_webhook', (str(data.get('backup_failure_webhook') or '').strip())[:512])
    if 'api_tokens' in data:
        _set_setting('api_tokens', (str(data.get('api_tokens') or '').strip())[:1024])

    # 自动发现 / SNMP 全局设置
    if 'snmp_version' in data:
        version = str(data.get('snmp_version') or '2c').strip()
        if version not in ('1', '2c', '3'):
            version = '2c'
        _set_setting('snmp_version', version)
    if 'snmp_community' in data:
        _set_setting('snmp_community', str(data.get('snmp_community') or 'public').strip())
    if 'snmp_timeout_ms' in data:
        try:
            n = int(data.get('snmp_timeout_ms') or '2000')
            # 前端限制 500–10000，这里再做一次兜底
            _set_setting('snmp_timeout_ms', str(max(500, min(10000, n))))
        except (TypeError, ValueError):
            _set_setting('snmp_timeout_ms', '2000')
    if 'snmp_retries' in data:
        try:
            n = int(data.get('snmp_retries') or '1')
            _set_setting('snmp_retries', str(max(0, min(5, n))))
        except (TypeError, ValueError):
            _set_setting('snmp_retries', '1')

    # 自动发现：设备类型关键字映射
    if 'discovery_type_keywords' in data:
        _set_setting('discovery_type_keywords', str(data.get('discovery_type_keywords') or '').strip())
    # 自动发现：主机名过滤
    if 'discovery_hostname_split_char' in data:
        _set_setting('discovery_hostname_split_char', str(data.get('discovery_hostname_split_char') or '').strip()[:4])
    if 'discovery_hostname_segment_index' in data:
        try:
            v = int(data.get('discovery_hostname_segment_index') or 2)
            _set_setting('discovery_hostname_segment_index', str(max(1, min(10, v))))
        except (TypeError, ValueError):
            _set_setting('discovery_hostname_segment_index', '2')

    # 自动发现：执行频率（只允许固定几种值）
    if 'discovery_frequency' in data:
        freq = str(data.get('discovery_frequency') or 'none').strip()
        allowed = {'none', 'hourly', 'twice_daily', 'daily', 'weekly', 'custom'}
        if freq not in allowed and len(freq.split()) < 5:
            freq = 'none'
        _set_setting('discovery_frequency', freq)

    if data.get('backup_frequency') is not None or 'discovery_frequency' in data:
        _reload_backup_schedule()
    _write_audit('update_settings', resource_type='settings', resource_id='', detail='global settings updated')
    return jsonify({'ok': True})


@app.route('/api/settings/reset-defaults', methods=['POST'])
def reset_settings_to_defaults():
    """将所有系统设置恢复为系统默认参数值"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权修改全局设置，请使用管理员账号登录。'}), 403
    defaults = _get_default_settings()
    for key, value in defaults.items():
        _set_setting(key, value if value is not None else '')
    if defaults.get('logo_file') == '':
        old = _get_setting('logo_file', '')
        if old:
            safe_name = os.path.basename(old)
            path = os.path.join(LOGO_DIR, safe_name)
            if os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
    _reload_backup_schedule()
    _write_audit('reset_settings_defaults', resource_type='settings', resource_id='', detail='all settings reset to defaults')
    return jsonify({'ok': True})


@app.route('/api/settings/test-webhook', methods=['POST'])
def test_webhook():
    """发送一条测试告警到备份失败 Webhook URL，用于验证是否可达"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权修改全局设置，请使用管理员账号登录。'}), 403
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip() or (_get_setting('backup_failure_webhook', '') or '').strip()
    if not url:
        return jsonify({'error': '请先填写 Webhook URL'}), 400
    if not url.startswith(('http://', 'https://')):
        return jsonify({'error': 'URL 须以 http:// 或 https:// 开头'}), 400
    import urllib.request
    import urllib.error
    body = _webhook_body_for_url(url, 'Hello!!!')
    # 测试请求使用不验证 SSL 证书的 context，兼容自签名/内网证书
    import ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': 'application/json; charset=utf-8'})
        resp = urllib.request.urlopen(req, timeout=10, context=ssl_ctx)
        status = resp.getcode() if hasattr(resp, 'getcode') else 200
        app.logger.info('Webhook 测试已发送: url=%s, status=%s', url, status)
        return jsonify({'ok': True, 'status': status, 'message': '已发送测试消息 Hello!!! 至 Webhook，HTTP %d' % status})
    except urllib.error.HTTPError as e:
        # 对方返回 4xx/5xx 仍视为 URL 可达、请求已送达
        app.logger.info('Webhook 测试已发送: url=%s, status=%s', url, e.code)
        return jsonify({'ok': True, 'status': e.code, 'message': '请求已送达，接收方返回 HTTP %d' % e.code})
    except urllib.error.URLError as e:
        reason = str(e.reason) if e.reason else str(e)
        app.logger.warning('Webhook 测试请求失败: url=%s, reason=%s', url, reason)
        if 'timed out' in reason.lower() or 'timeout' in reason.lower():
            return jsonify({'error': '连接超时。测试请求由 vConfig 所在服务器发出，请确保 Webhook URL 可从该服务器访问（勿填仅本机可用的地址如 localhost）。'}), 502
        if 'certificate' in reason.lower() or 'ssl' in reason.lower():
            return jsonify({'error': 'SSL 证书验证失败：%s' % reason}), 502
        if 'connection refused' in reason.lower() or 'refused' in reason.lower():
            return jsonify({'error': '连接被拒绝。测试请求由 vConfig 所在服务器发出，请确保 URL 可从服务器访问且服务已启动。'}), 502
        return jsonify({'error': '无法连接：%s。提示：测试由服务器发起，Webhook URL 须在服务器侧可访问。' % reason}), 502
    except Exception as e:
        app.logger.warning('Webhook 测试请求异常: url=%s, err=%s', url, e)
        return jsonify({'error': '请求失败：%s' % (str(e) or '未知错误')}), 502


def _validate_pem_cert(data: bytes) -> bool:
    """校验是否为有效 PEM 证书格式"""
    try:
        text = data.decode('utf-8', errors='ignore')
        return '-----BEGIN CERTIFICATE-----' in text and '-----END CERTIFICATE-----' in text
    except Exception:
        return False


def _validate_pem_key(data: bytes) -> bool:
    """校验是否为有效 PEM 私钥格式"""
    try:
        text = data.decode('utf-8', errors='ignore')
        if '-----BEGIN PRIVATE KEY-----' in text and '-----END PRIVATE KEY-----' in text:
            return True
        if '-----BEGIN RSA PRIVATE KEY-----' in text and '-----END RSA PRIVATE KEY-----' in text:
            return True
        if '-----BEGIN EC PRIVATE KEY-----' in text and '-----END EC PRIVATE KEY-----' in text:
            return True
        return False
    except Exception:
        return False


@app.route('/api/settings/upload-ssl-cert', methods=['POST'])
def upload_ssl_cert():
    """用户上传自有域名证书（cert.pem + key.pem）"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权修改全局设置。'}), 403
    cert_f = request.files.get('cert')
    key_f = request.files.get('key')
    if not cert_f or not cert_f.filename:
        return jsonify({'error': '请选择证书文件（.crt 或 .pem）'}), 400
    if not key_f or not key_f.filename:
        return jsonify({'error': '请选择私钥文件（.key 或 .pem）'}), 400
    try:
        cert_data = cert_f.read()
        key_data = key_f.read()
    except Exception as e:
        return jsonify({'error': '读取文件失败：%s' % str(e)}), 400
    if len(cert_data) < 50 or len(key_data) < 50:
        return jsonify({'error': '证书或私钥文件内容过短，请检查文件是否正确。'}), 400
    if not _validate_pem_cert(cert_data):
        return jsonify({'error': '证书格式无效，应为 PEM 格式（含 -----BEGIN CERTIFICATE-----）。'}), 400
    if not _validate_pem_key(key_data):
        return jsonify({'error': '私钥格式无效，应为 PEM 格式（含 -----BEGIN PRIVATE KEY----- 或 -----BEGIN RSA PRIVATE KEY-----）。'}), 400
    cert_file = os.path.join(CERTS_DIR, 'cert.pem')
    key_file = os.path.join(CERTS_DIR, 'key.pem')
    os.makedirs(CERTS_DIR, mode=0o700, exist_ok=True)
    try:
        with open(cert_file, 'wb') as f:
            f.write(cert_data)
        with open(key_file, 'wb') as f:
            f.write(key_data)
        _write_audit('upload_ssl_cert', resource_type='settings', resource_id='', detail='SSL cert uploaded by user')
        return jsonify({'ok': True, 'message': '证书已上传，请重启服务后生效。'})
    except Exception as e:
        return jsonify({'error': '保存证书失败：%s' % str(e)}), 500


@app.route('/api/settings/update-ssl-cert', methods=['POST'])
def update_ssl_cert():
    """用户自助更新 HTTPS 自签名证书，删除旧证书并重新生成（有效期 100 年）"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权修改全局设置，请使用管理员账号登录。'}), 403
    cert_file = os.path.join(CERTS_DIR, 'cert.pem')
    key_file = os.path.join(CERTS_DIR, 'key.pem')
    for f in (cert_file, key_file):
        if os.path.isfile(f):
            try:
                os.unlink(f)
            except OSError as e:
                return jsonify({'error': '删除旧证书失败：%s' % str(e)}), 500
    os.makedirs(CERTS_DIR, mode=0o700, exist_ok=True)
    import subprocess
    cmd = [
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
        '-keyout', key_file, '-out', cert_file,
        '-days', '36500', '-nodes',
        '-subj', '/CN=localhost/O=vConfig/C=CN',
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        _write_audit('update_ssl_cert', resource_type='settings', resource_id='', detail='SSL cert regenerated')
        return jsonify({'ok': True, 'message': 'SSL 证书已重新生成，请重启服务后生效。'})
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return jsonify({'error': '生成证书失败（需安装 openssl）：%s' % str(e)}), 500


@app.route('/api/settings/restart', methods=['POST'])
def restart_service():
    """重启服务（向 Gunicorn 主进程发送 SIGHUP 触发优雅重载）"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权重启服务，请使用管理员账号登录。'}), 403

    def _do_restart():
        import time
        time.sleep(1)
        try:
            import signal
            ppid = os.getppid()
            os.kill(ppid, signal.SIGHUP)
        except Exception as e:
            app.logger.warning('重启信号发送失败: %s', e)

    threading.Thread(target=_do_restart, daemon=True).start()
    _write_audit('restart_service', resource_type='settings', resource_id='', detail='user triggered restart')
    return jsonify({'ok': True})


def _get_sqlite_db_path():
    """从 SQLALCHEMY_DATABASE_URI 解析 SQLite 数据库文件路径，非 SQLite 返回 None"""
    try:
        from sqlalchemy.engine.url import make_url
    except ImportError:
        return None
    uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if not uri or 'sqlite' not in uri.lower():
        return None
    try:
        url = make_url(uri)
        db_path = getattr(url, 'database', None)
        if not db_path:
            return None
        if not os.path.isabs(db_path):
            base = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.normpath(os.path.join(base, db_path))
        return db_path
    except Exception:
        return None


@app.route('/api/settings/db/backup')
def db_backup():
    """备份数据库：下载当前 SQLite 数据库文件"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权备份数据库。'}), 403
    db_path = _get_sqlite_db_path()
    if not db_path or not os.path.isfile(db_path):
        return jsonify({'error': '当前使用非 SQLite 或数据库文件不存在，暂不支持备份。'}), 400
    dir_name = os.path.dirname(db_path)
    file_name = os.path.basename(db_path)
    fn = 'vconfig_' + datetime.utcnow().strftime('%Y%m%d') + '.db'
    return send_from_directory(dir_name, file_name, as_attachment=True, attachment_filename=fn)


@app.route('/api/settings/db/restore', methods=['POST'])
def db_restore():
    """恢复数据库：上传 SQLite 备份文件并替换当前数据库，完成后触发重启"""
    if not _can_edit_settings():
        return jsonify({'error': '当前账号无权恢复数据库。'}), 403
    db_path = _get_sqlite_db_path()
    if not db_path:
        return jsonify({'error': '当前使用非 SQLite，暂不支持恢复。'}), 400
    if 'file' not in request.files:
        return jsonify({'error': '请选择要恢复的数据库备份文件。'}), 400
    f = request.files['file']
    if not f or f.filename == '':
        return jsonify({'error': '未选择文件。'}), 400
    try:
        data = f.read()
    except Exception as e:
        return jsonify({'error': '读取文件失败: %s' % e}), 400
    if len(data) < 16:
        return jsonify({'error': '文件过小，非有效的 SQLite 数据库。'}), 400
    if not data[:16].startswith(b'SQLite format 3\x00'):
        return jsonify({'error': '文件格式不正确，请上传有效的 SQLite 备份文件。'}), 400
    dir_name = os.path.dirname(db_path)
    backup_fn = os.path.basename(db_path) + '.bak.' + datetime.utcnow().strftime('%Y%m%d%H%M%S')
    backup_path = os.path.join(dir_name, backup_fn)
    try:
        import shutil
        if os.path.isfile(db_path):
            shutil.copy2(db_path, backup_path)
        with open(db_path, 'wb') as out:
            out.write(data)
    except Exception as e:
        if os.path.isfile(backup_path):
            try:
                os.unlink(backup_path)
            except OSError:
                pass
        return jsonify({'error': '写入数据库失败: %s' % e}), 500
    _write_audit('db_restore', resource_type='settings', resource_id='', detail='database restored, backup=%s' % backup_fn)

    def _do_restart():
        import time
        time.sleep(1)
        try:
            import signal
            os.kill(os.getppid(), signal.SIGHUP)
        except Exception:
            pass
    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/settings/logo', methods=['POST'])
def upload_logo():
    """上传自定义 Logo（仅可由有权修改设置的用户调用）。"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权修改全局设置，请使用全局用户名登录。'}), 403
    if 'file' not in request.files:
        return jsonify({'error': '请选择要上传的文件。'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': '未选择文件。'}), 400
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    allowed_exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    if ext not in allowed_exts:
        return jsonify({'error': '仅支持 PNG/JPG/GIF/WebP 格式的图片。'}), 400
    os.makedirs(LOGO_DIR, exist_ok=True)
    # 先清理旧文件
    for name in os.listdir(LOGO_DIR):
        if name.startswith('logo.') and os.path.isfile(os.path.join(LOGO_DIR, name)):
            try:
                os.unlink(os.path.join(LOGO_DIR, name))
            except OSError:
                pass
    final_name = 'logo' + ext
    save_path = os.path.join(LOGO_DIR, final_name)
    try:
        file.save(save_path)
        with app.app_context():
            _set_setting('logo_file', final_name)
        _write_audit('upload_logo', resource_type='settings', resource_id='', detail=f'logo={final_name}')
        return jsonify({'ok': True, 'logo_url': url_for('logo')})
    except Exception as e:
        return jsonify({'error': 'Logo 上传失败: %s' % e}), 500


@app.route('/api/settings/logo', methods=['DELETE'])
def delete_logo():
    """删除自定义 Logo，恢复为默认图标。"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权修改全局设置，请使用全局用户名登录。'}), 403
    filename = _get_setting('logo_file', '') or ''
    if filename:
        path = os.path.join(LOGO_DIR, os.path.basename(filename))
        if os.path.isfile(path):
            try:
                os.unlink(path)
            except OSError:
                pass
    try:
        _set_setting('logo_file', '')
    except Exception:
        pass
    _write_audit('delete_logo', resource_type='settings', resource_id='', detail='logo reset to default')
    return jsonify({'ok': True})


# ---------- 配置文件浏览 ----------
@app.route('/api/configs')
def list_configs():
    """列出已备份的配置文件目录结构"""
    if not os.path.exists(CONFIGS_DIR):
        return jsonify({'tree': []})
    tree = []
    for prefix in sorted(os.listdir(CONFIGS_DIR)):
        p = os.path.join(CONFIGS_DIR, prefix)
        if not os.path.isdir(p):
            continue
        hosts = []
        for host in sorted(os.listdir(p)):
            hp = os.path.join(p, host)
            if not os.path.isdir(hp):
                continue
            files = sorted([f for f in os.listdir(hp) if f.endswith('.txt')], reverse=True)[:20]
            hosts.append({'name': host, 'files': files})
        tree.append({'prefix': prefix, 'hosts': hosts})
    return jsonify({'tree': tree})


def _config_prefix(hostname):
    """与 backup_service 一致：根据 hostname 得到配置目录的 prefix"""
    return hostname.split('.', 1)[0] if '.' in hostname else hostname


_DIFF_IGNORE_PATTERNS = [
    # 忽略「当前时间」等纯时间行，例如：Tue Feb 10 09:18:34.895 CST
    re.compile(
        r'^\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+'
        r'[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(\.\d+)?\s+[A-Z]+'
    ),
    # 忽略 Cisco 等设备的 ntp clock-period 行（频率估算值，每次可能不同）
    re.compile(r'^\s*ntp\s+clock-period\s+\d+\s*$', re.IGNORECASE),
]


def _diff_config_lines(content_old, content_new):
    """对比两份配置文本，按行集合差集得到新增行与删除行。返回 (added_list, removed_list)。

    会先过滤掉一些「噪声行」，例如设备当前时间等每次备份都会变化、但不算真实配置变更的内容。
    """

    def _norm_lines(text: str):
        lines = []
        for raw in (text or '').splitlines():
            ln = raw.strip()
            if not ln:
                continue
            ignore = False
            for pat in _DIFF_IGNORE_PATTERNS:
                if pat.match(ln):
                    ignore = True
                    break
            if ignore:
                continue
            lines.append(ln)
        return lines

    lines_old = _norm_lines(content_old)
    lines_new = _norm_lines(content_new)
    set_old = set(lines_old)
    set_new = set(lines_new)
    added = sorted(set_new - set_old)
    removed = sorted(set_old - set_new)
    return (added, removed)


def _resolve_config_dir(prefix, hostname):
    """解析配置目录实际路径，支持大小写不敏感回退。
    返回 (resolved_prefix, resolved_hostname, dir_path) 或 (None, None, None)。"""
    dir_path = os.path.join(CONFIGS_DIR, prefix, hostname)
    if os.path.isdir(dir_path):
        return (prefix, hostname, dir_path)
    if not os.path.isdir(CONFIGS_DIR):
        return (None, None, None)
    host_lower = (hostname or '').lower()
    prefix_lower = (prefix or '').lower()
    for p in os.listdir(CONFIGS_DIR):
        pp = os.path.join(CONFIGS_DIR, p)
        if not os.path.isdir(pp):
            continue
        if p.lower() != prefix_lower:
            continue
        for h in os.listdir(pp):
            hp = os.path.join(pp, h)
            if not os.path.isdir(hp):
                continue
            if h.lower() == host_lower:
                return (p, h, hp)
    return (None, None, None)


@app.route('/api/configs/devices')
def list_configs_by_devices():
    """列出已备份配置：以设备表为准，遍历设备表，用 _resolve_config_dir 解析配置目录并统计 .txt 文件数。"""
    page = max(1, request.args.get('page', 1, type=int))
    per_page = max(1, min(request.args.get('per_page', 50, type=int), 200))
    search = (request.args.get('search') or '').strip()
    sort_by = (request.args.get('sort_by') or 'hostname').strip()
    sort_dir = (request.args.get('sort_dir') or 'asc').strip().lower()
    devices = []
    for dev in Device.query.order_by(Device.hostname).all():
        prefix = _config_prefix(dev.hostname)
        rprefix, rhost, dir_path = _resolve_config_dir(prefix, dev.hostname)
        files = []
        path_host = dev.hostname
        if dir_path and rhost:
            path_host = rhost
            try:
                files = sorted([f for f in os.listdir(dir_path) if f.endswith('.txt')], reverse=True)
            except OSError:
                pass
        devices.append({
            'hostname': path_host,
            'display_hostname': dev.hostname,
            'ip': dev.ip or '',
            'device_type': dev.device_type or '',
            'prefix': rprefix if rprefix else prefix,
            'files': files,
            'file_count': len(files),
        })
    # 过滤
    if search:
        ql = search.lower()
        devices = [
            d for d in devices
            if ql in (d.get('display_hostname') or d.get('hostname') or '').lower()
            or ql in (d.get('ip') or '').lower()
            or ql in (d.get('device_type') or '').lower()
        ]
    # 排序
    order_cols = {'hostname': 'display_hostname', 'ip': 'ip', 'device_type': 'device_type'}
    key_field = order_cols.get(sort_by, 'display_hostname')
    reverse = sort_dir == 'desc'
    devices.sort(key=lambda d: ((d.get(key_field) or d.get('hostname')) or '').lower(), reverse=reverse)
    total = len(devices)
    start = (page - 1) * per_page
    end = start + per_page
    devices = devices[start:end]
    return jsonify({
        'devices': devices,
        'total': total,
        'page': page,
        'per_page': per_page,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
        'can_delete_backups': _is_admin(),
    })


@app.route('/api/configs/devices/<prefix>/<hostname>')
def list_config_files_for_device(prefix, hostname):
    """返回某设备（prefix/hostname）的配置文件列表，用于「查看历史备份」"""
    if '..' in prefix or '..' in hostname or '/' in prefix or '/' in hostname:
        return jsonify({'error': 'invalid'}), 400
    _, _, dir_path = _resolve_config_dir(prefix, hostname)
    if not dir_path:
        return jsonify({'hostname': hostname, 'prefix': prefix, 'files': []})
    entries = []
    for f in sorted(os.listdir(dir_path), reverse=True):
        if not f.endswith('.txt'):
            continue
        fp = os.path.join(dir_path, f)
        if not os.path.isfile(fp):
            continue
        try:
            size = os.path.getsize(fp)
        except OSError:
            size = None
        entries.append({'name': f, 'size': size})
    return jsonify({'hostname': hostname, 'prefix': prefix, 'files': entries})


@app.route('/api/configs/devices/<prefix>/<hostname>/diff-latest')
def device_config_diff_latest(prefix, hostname):
    """返回该设备「最新备份」与「上一份备份」的按行差异：新增行、删除行。用于首页配置变动弹窗。"""
    if '..' in prefix or '..' in hostname or '/' in prefix or '/' in hostname:
        return jsonify({'error': 'invalid'}), 400
    _, _, dir_path = _resolve_config_dir(prefix, hostname)
    if not dir_path:
        return jsonify({'added': [], 'removed': [], 'hostname': hostname})
    files = sorted([f for f in os.listdir(dir_path) if f.endswith('.txt')], reverse=True)
    if len(files) < 2:
        return jsonify({'added': [], 'removed': [], 'hostname': hostname})
    path_new = os.path.join(dir_path, files[0])
    path_old = os.path.join(dir_path, files[1])
    try:
        with open(path_new, 'r', encoding='utf-8', errors='replace') as f:
            content_new = f.read()
        with open(path_old, 'r', encoding='utf-8', errors='replace') as f:
            content_old = f.read()
    except OSError as e:
        app.logger.warning('读取配置 diff 失败: %s', e)
        return jsonify({'error': 'read failed', 'added': [], 'removed': []}), 500
    added, removed = _diff_config_lines(content_old, content_new)
    return jsonify({'hostname': hostname, 'added': added, 'removed': removed})


def _save_config_changes_to_db():
    """在备份任务完成后调用：计算配置变动并与上次差异，结果写入数据库。"""
    devices = _dashboard_config_change_devices(limit=30)
    try:
        with app.app_context():
            _ensure_tables()
            ConfigChangeRecord.query.delete()
            now = datetime.utcnow()
            for d in devices:
                r = ConfigChangeRecord(
                    hostname=d.get('hostname') or '',
                    prefix=d.get('prefix') or '',
                    ip=d.get('ip') or '',
                    device_type=d.get('device_type') or '',
                    added_count=d.get('added_count', 0),
                    removed_count=d.get('removed_count', 0),
                    change_count=d.get('change_count', 0),
                    computed_at=now,
                )
                db.session.add(r)
            db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        raise


def _dashboard_config_change_devices(limit=15):
    """按「最新 vs 上一份」配置变动行数（新增+删除）排序，返回变动最多的设备列表。"""
    result = []
    for dev in Device.query.order_by(Device.hostname).all():
        prefix = _config_prefix(dev.hostname)
        _, rhost, dir_path = _resolve_config_dir(prefix, dev.hostname)
        if not dir_path or not rhost:
            continue
        files = sorted([f for f in os.listdir(dir_path) if f.endswith('.txt')], reverse=True)
        if len(files) < 2:
            continue
        path_new = os.path.join(dir_path, files[0])
        path_old = os.path.join(dir_path, files[1])
        try:
            with open(path_new, 'r', encoding='utf-8', errors='replace') as f:
                content_new = f.read()
            with open(path_old, 'r', encoding='utf-8', errors='replace') as f:
                content_old = f.read()
        except OSError:
            continue
        added, removed = _diff_config_lines(content_old, content_new)
        change_count = len(added) + len(removed)
        if change_count == 0:
            continue
        result.append({
            'hostname': rhost,
            'prefix': prefix,
            'ip': (dev.ip or '').strip(),
            'device_type': (dev.device_type or '').strip(),
            'change_count': change_count,
            'added_count': len(added),
            'removed_count': len(removed),
        })
    result.sort(key=lambda x: x['change_count'], reverse=True)
    return result[:limit]


@app.route('/api/dashboard/config-changes')
def dashboard_config_changes():
    """首页「配置变动」卡片数据：从数据库读取，由每次备份任务完成后计算并写入。"""
    _ensure_tables()
    limit = max(1, min(30, request.args.get('limit', 15, type=int)))
    rows = (
        ConfigChangeRecord.query
        .order_by(ConfigChangeRecord.change_count.desc())
        .limit(limit)
        .all()
    )
    devices = [r.to_dict() for r in rows]
    return jsonify({'devices': devices})


@app.route('/config-changes')
def config_changes_page():
    """重定向到主应用 #config-changes 视图"""
    return redirect(url_for('index') + '#config-changes')


@app.route('/api/configs/devices/<prefix>/<hostname>/delete', methods=['POST'])
def delete_config_files_for_device(prefix, hostname):
    """删除某设备的所有备份配置文件（需前端再次确认）"""
    if not _can_edit_settings():
        return jsonify({'error': '当前登录账号无权删除备份文件，请使用全局用户名登录。'}), 403
    if '..' in prefix or '..' in hostname or '/' in prefix or '/' in hostname:
        return jsonify({'error': 'invalid'}), 400
    _, _, dir_path = _resolve_config_dir(prefix, hostname)
    if not dir_path:
        return jsonify({'ok': True, 'deleted': 0})
    deleted = 0
    if os.path.isdir(dir_path):
        for f in os.listdir(dir_path):
            if not f.endswith('.txt'):
                continue
            fp = os.path.join(dir_path, f)
            if os.path.isfile(fp):
                try:
                    os.unlink(fp)
                    deleted += 1
                except OSError:
                    pass
    _write_audit('delete_backups', resource_type='config', resource_id=f'{prefix}/{hostname}', detail=f'deleted={deleted}')
    return jsonify({'ok': True, 'deleted': deleted})

@app.route('/api/configs/<path:filepath>')
def get_config_file(filepath):
    """下载/查看配置文件；?download=1 时以附件形式下载为 .txt"""
    if '..' in filepath or filepath.startswith('/'):
        return jsonify({'error': 'invalid path'}), 400
    parts = filepath.split('/')
    if len(parts) < 3:
        full_path = os.path.normpath(os.path.join(CONFIGS_DIR, filepath))
    else:
        prefix, hostname, fname = parts[0], parts[1], '/'.join(parts[2:])
        _, _, dir_path = _resolve_config_dir(prefix, hostname)
        full_path = os.path.join(dir_path, fname) if dir_path else os.path.normpath(os.path.join(CONFIGS_DIR, filepath))
    abs_configs = os.path.abspath(CONFIGS_DIR)
    abs_full = os.path.abspath(full_path)
    if not (abs_full == abs_configs or abs_full.startswith(abs_configs + os.sep)):
        return jsonify({'error': 'invalid path'}), 400
    if not os.path.isfile(full_path):
        return jsonify({'error': 'file not found'}), 404
    rel_path = os.path.relpath(abs_full, abs_configs)
    as_attachment = request.args.get('download') == '1'
    if as_attachment:
        with open(full_path, 'rb') as f:
            body = f.read()
        name = os.path.basename(full_path)
        if not name.lower().endswith('.txt'):
            name = name + '.txt'
        return Response(
            body,
            mimetype='text/plain; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename="{name}"',
            },
        )
    return send_from_directory(CONFIGS_DIR, rel_path)


# ---------- 配置全文搜索（基础版） ----------
@app.route('/api/search/configs')
def search_configs():
    """在所有已备份配置中按关键字全文搜索（基础版，大小写不敏感，返回前 N 条匹配）"""
    q = (request.args.get('q') or '').strip()
    limit = request.args.get('limit', 50, type=int)
    limit = max(1, min(limit, 200))
    if not q:
        return jsonify({'error': '请输入搜索关键字 q'}), 400
    if not os.path.exists(CONFIGS_DIR):
        return jsonify({'items': []})
    q_lower = q.lower()

    # 一次性构建 hostname -> ip 映射，便于结果中带出 IP
    host_ip = {
        h: ip for h, ip in
        Device.query.with_entities(Device.hostname, Device.ip).all()
    }

    items = []
    for prefix in sorted(os.listdir(CONFIGS_DIR)):
        pdir = os.path.join(CONFIGS_DIR, prefix)
        if not os.path.isdir(pdir):
            continue
        for host in sorted(os.listdir(pdir)):
            hdir = os.path.join(pdir, host)
            if not os.path.isdir(hdir):
                continue
            for fname in sorted(os.listdir(hdir)):
                if not fname.endswith('.txt'):
                    continue
                fpath = os.path.join(hdir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        for idx, line in enumerate(f, start=1):
                            if q_lower in line.lower():
                                snippet = line.strip()
                                if len(snippet) > 200:
                                    snippet = snippet[:197] + '...'
                                items.append({
                                    'prefix': prefix,
                                    'hostname': host,
                                    'ip': host_ip.get(host, ''),
                                    'filename': fname,
                                    'line_no': idx,
                                    'line': snippet,
                                })
                                if len(items) >= limit:
                                    return jsonify({'items': items})
                except OSError:
                    continue
    return jsonify({'items': items})


# ---------- 配置合规检查（基础规则） ----------
@app.route('/api/compliance/<prefix>/<hostname>', methods=['GET'])
def check_compliance(prefix, hostname):
    """对某设备最新配置执行基础合规检查"""
    if '..' in prefix or '..' in hostname or '/' in prefix or '/' in hostname:
        return jsonify({'error': 'invalid'}), 400
    dir_path = os.path.join(CONFIGS_DIR, prefix, hostname)
    if not os.path.isdir(dir_path):
        return jsonify({'error': 'not_found', 'message': '未找到该设备的配置目录'}), 404
    # 找到最新的一个 .txt 配置文件（按文件名逆序或按 mtime 最大）
    candidates = [f for f in os.listdir(dir_path) if f.endswith('.txt')]
    if not candidates:
        return jsonify({'error': 'no_config', 'message': '该设备暂无备份配置文件'}), 404
    # 文件名一般包含时间戳，直接按名称倒序即可
    candidates.sort(reverse=True)
    latest = candidates[0]
    full_path = os.path.join(dir_path, latest)
    try:
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except OSError:
        return jsonify({'error': 'read_failed', 'message': '读取配置文件失败'}), 500

    # 获取设备类型信息（如有）
    dev = Device.query.filter_by(hostname=hostname).first()
    dev_type = dev.device_type if dev else ''
    result = check_config(content, hostname=hostname, device_type=dev_type)
    result['latest_file'] = latest
    return jsonify(result)


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
