# -*- coding: utf-8 -*-
"""数据模型"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import json

db = SQLAlchemy()


def _isoformat_utc(dt):
    """序列化为 ISO 字符串并带 Z 后缀，供前端按 UTC 解析后按设置时区显示"""
    if dt is None:
        return None
    s = dt.isoformat()
    if s and not s.endswith('Z') and '+' not in s[-6:] and not s.endswith('-'):
        return s + 'Z'
    return s


class Device(db.Model):
    """设备"""
    __tablename__ = 'devices'
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(64), nullable=False, index=True)
    hostname = db.Column(db.String(128), nullable=False, index=True)
    device_type = db.Column(db.String(32), nullable=False)  # 设备类型代码，关联 DeviceTypeConfig.type_code
    enabled = db.Column(db.Boolean, default=True)
    device_group = db.Column(db.String(64), nullable=True)  # 分组/区域，用于筛选与统计（API 暴露为 group）
    maintenance_start = db.Column(db.String(8), nullable=True)  # 维护窗口开始时间 "HH:MM"，空表示无
    maintenance_end = db.Column(db.String(8), nullable=True)   # 维护窗口结束时间 "HH:MM"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # 可选：设备单独账号
    username = db.Column(db.String(64), nullable=True)
    password = db.Column(db.String(128), nullable=True)
    # 连接方式：TELNET / SSH，空则使用全局默认
    connection_type = db.Column(db.String(16), nullable=True)
    # SSH 端口：空则使用全局默认（仅对 SSH 连接生效）
    ssh_port = db.Column(db.Integer, nullable=True)
    # Telnet 端口：空则使用全局默认（仅对 Telnet 连接生效）
    telnet_port = db.Column(db.Integer, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'ip': self.ip,
            'hostname': self.hostname,
            'device_type': self.device_type,
            'enabled': self.enabled,
            'group': self.device_group,
            'maintenance_start': self.maintenance_start,
            'maintenance_end': self.maintenance_end,
            'username': self.username,
            'connection_type': self.connection_type,
            'ssh_port': self.ssh_port,
            'telnet_port': self.telnet_port,
            'created_at': _isoformat_utc(self.created_at),
        }


class BackupLog(db.Model):
    """备份日志"""
    __tablename__ = 'backup_logs'
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=True)
    ip = db.Column(db.String(64), nullable=False)
    hostname = db.Column(db.String(128), nullable=False)
    device_type = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(32), nullable=False)  # OK, Fail_Network, Fail_Login, Fail
    message = db.Column(db.String(256), nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)
    config_path = db.Column(db.String(512), nullable=True)  # 保存的配置文件路径
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'ip': self.ip,
            'hostname': self.hostname,
            'device_type': self.device_type,
            'status': self.status,
            'message': self.message,
            'duration_seconds': self.duration_seconds,
            'config_path': self.config_path,
            'created_at': _isoformat_utc(self.created_at),
        }


class BackupJobRun(db.Model):
    """备份任务记录（持久化，重启后仍可查看）"""
    __tablename__ = 'backup_job_runs'
    id = db.Column(db.String(32), primary_key=True)  # 如 20250206120000
    # 由于历史数据中使用 VARCHAR 保存 ISO 字符串，这里保持为字符串类型，避免与现有库不兼容
    start_time = db.Column(db.String(64), nullable=False)
    end_time = db.Column(db.String(64), nullable=True)
    total = db.Column(db.Integer, default=0)
    done = db.Column(db.Integer, default=0)
    ok = db.Column(db.Integer, default=0)
    fail = db.Column(db.Integer, default=0)
    status = db.Column(db.String(16), default='completed')
    run_type = db.Column(db.String(16), default='manual')  # manual | scheduled
    executor = db.Column(db.String(128), default='')       # 执行者：用户名或 System

    def to_dict(self):
        # 前端会按 ISO 字符串解析时间，这里直接返回原始字符串即可
        return {
            'id': self.id,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'total': self.total,
            'done': self.done,
            'ok': self.ok,
            'fail': self.fail,
            'status': self.status,
            'run_type': self.run_type or 'manual',
            'executor': self.executor or '',
        }


class AppSetting(db.Model):
    """应用设置（键值对）"""
    __tablename__ = 'app_settings'
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'key': self.key,
            'value': self.value,
            'updated_at': _isoformat_utc(self.updated_at),
        }


class LoginLog(db.Model):
    """用户登录日志（用于仪表盘最近登录展示）"""
    __tablename__ = 'login_logs'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False)
    source_ip = db.Column(db.String(64), nullable=True)
    auth_source = db.Column(db.String(16), nullable=False)  # local / ldap
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'source_ip': self.source_ip,
            'auth_source': self.auth_source,
            'created_at': _isoformat_utc(self.created_at),
        }


class AuditLog(db.Model):
    """审计日志（敏感操作记录）"""
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False)
    action = db.Column(db.String(64), nullable=False)  # add_device, delete_device, etc.
    resource_type = db.Column(db.String(32), nullable=True)  # device, user, etc.
    resource_id = db.Column(db.String(128), nullable=True)
    detail = db.Column(db.String(512), nullable=True)
    auth_source = db.Column(db.String(16), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'action': self.action,
            'resource_type': self.resource_type,
            'resource_id': self.resource_id,
            'detail': self.detail,
            'auth_source': self.auth_source,
            'created_at': _isoformat_utc(self.created_at),
        }


class ConfigPushLog(db.Model):
    """配置下发日志"""
    __tablename__ = 'config_push_logs'
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=True)
    hostname = db.Column(db.String(128), nullable=False)
    ip = db.Column(db.String(64), nullable=False)
    device_type = db.Column(db.String(32), nullable=True)
    status = db.Column(db.String(32), nullable=False)  # OK, Fail
    message = db.Column(db.String(512), nullable=True)
    pushed_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'hostname': self.hostname,
            'ip': self.ip,
            'device_type': self.device_type,
            'status': self.status,
            'message': self.message,
            'pushed_by': self.pushed_by,
            'created_at': _isoformat_utc(self.created_at),
        }


class User(db.Model):
    """用户（本地账号 + LDAP 账号）"""
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(128), nullable=True)
    email = db.Column(db.String(128), nullable=True)
    phone = db.Column(db.String(32), nullable=True)
    source = db.Column(db.String(16), nullable=False, default='local')  # local / ldap
    role = db.Column(db.String(16), nullable=False, default='readonly')  # admin / operator / readonly
    is_active = db.Column(db.Boolean, default=True)
    allowed_groups = db.Column(db.String(512), nullable=True)  # 可管理设备分组，逗号分隔，空=全部
    password_hash = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, raw_password: str):
        """设置密码（仅本地账号）"""
        self.password_hash = generate_password_hash(
            str(raw_password),
            method='pbkdf2:sha256',
            salt_length=16
        )

    def check_password(self, raw_password: str) -> bool:
        """验证密码（仅本地账号）"""
        if not raw_password or not self.password_hash:
            return False
        return check_password_hash(self.password_hash, str(raw_password))

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'display_name': self.display_name,
            'email': self.email,
            'phone': self.phone,
            'source': self.source,
            'role': self.role,
            'is_active': self.is_active,
            'allowed_groups': self.allowed_groups,
            'created_at': _isoformat_utc(self.created_at),
            'updated_at': _isoformat_utc(self.updated_at),
        }


class ConfigChangeRecord(db.Model):
    """配置变动记录（用于首页配置变动展示）"""
    __tablename__ = 'config_change_records'
    id = db.Column(db.Integer, primary_key=True)
    hostname = db.Column(db.String(128), nullable=False)
    prefix = db.Column(db.String(64), nullable=False)
    ip = db.Column(db.String(64), nullable=True)
    device_type = db.Column(db.String(32), nullable=True)
    added_count = db.Column(db.Integer, default=0)
    removed_count = db.Column(db.Integer, default=0)
    change_count = db.Column(db.Integer, default=0)
    computed_at = db.Column(db.DateTime, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'hostname': self.hostname,
            'prefix': self.prefix,
            'ip': self.ip,
            'device_type': self.device_type,
            'added_count': self.added_count,
            'removed_count': self.removed_count,
            'change_count': self.change_count,
            'computed_at': _isoformat_utc(self.computed_at),
        }


class DeviceTypeConfig(db.Model):
    """设备类型配置表（可扩展的设备类型支持）"""
    __tablename__ = 'device_type_configs'
    id = db.Column(db.Integer, primary_key=True)
    type_code = db.Column(db.String(32), unique=True, nullable=False, index=True)  # 如 'Cisco', 'Juniper', 'Fortinet'
    display_name = db.Column(db.String(64), nullable=False)  # 显示名称，如 'Cisco交换机', 'Fortinet防火墙'
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    # 备份相关配置（JSON存储）
    backup_config = db.Column(db.Text, nullable=True)  # JSON: {"init_commands": [], "backup_command": "", "prompt": ""}
    # 连接相关配置
    connection_config = db.Column(db.Text, nullable=True)  # JSON: {"login_prompt": "", "password_prompt": ""}
    # 驱动类型：builtin（内置驱动）/ generic（通用驱动，基于配置）/ custom（自定义Python驱动）
    driver_type = db.Column(db.String(16), default='generic', nullable=False)
    # 自定义驱动模块路径（仅当 driver_type='custom' 时使用）
    driver_module = db.Column(db.String(128), nullable=True)  # 如 'device_drivers.custom.fortinet'
    # 排序权重（用于前端显示顺序）
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_backup_config(self) -> dict:
        """解析备份配置JSON"""
        if not self.backup_config:
            return {}
        try:
            return json.loads(self.backup_config)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_backup_config(self, config: dict):
        """设置备份配置JSON"""
        self.backup_config = json.dumps(config) if config else None

    def get_connection_config(self) -> dict:
        """解析连接配置JSON"""
        if not self.connection_config:
            return {}
        try:
            return json.loads(self.connection_config)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_connection_config(self, config: dict):
        """设置连接配置JSON"""
        self.connection_config = json.dumps(config) if config else None

    def to_dict(self):
        return {
            'id': self.id,
            'type_code': self.type_code,
            'display_name': self.display_name,
            'enabled': self.enabled,
            'backup_config': self.get_backup_config(),
            'connection_config': self.get_connection_config(),
            'driver_type': self.driver_type,
            'driver_module': self.driver_module,
            'sort_order': self.sort_order,
            'created_at': _isoformat_utc(self.created_at),
            'updated_at': _isoformat_utc(self.updated_at),
        }


class AutoDiscoveryRule(db.Model):
    """自动发现规则：用于按 IP 范围 + SNMP 规则批量发现并加入设备列表。"""
    __tablename__ = 'auto_discovery_rules'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    ip_range = db.Column(db.Text, nullable=False)  # 多行 IP / 段 / CIDR
    snmp_community = db.Column(db.String(128), nullable=True)  # 为空则使用全局 SNMP community
    hostname_oid = db.Column(db.String(128), nullable=True)   # 主机名 OID，默认 1.3.6.1.2.1.1.5.0
    device_type_oid = db.Column(db.String(128), nullable=True)  # 设备类型 OID 或 sysObjectID
    device_group = db.Column(db.String(64), nullable=True)    # 自动加入的设备分组
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'ip_range': self.ip_range,
            'snmp_community': self.snmp_community,
            'hostname_oid': self.hostname_oid,
            'device_type_oid': self.device_type_oid,
            'device_group': self.device_group,
            'enabled': self.enabled,
            'created_at': _isoformat_utc(self.created_at),
            'updated_at': _isoformat_utc(self.updated_at),
        }


class AutoDiscoveryRunLog(db.Model):
    """自动发现规则运行日志：记录每次执行的扫描范围与结果概要。"""
    __tablename__ = 'auto_discovery_run_logs'
    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey('auto_discovery_rules.id'), nullable=False, index=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    scanned = db.Column(db.Integer, default=0)
    added_count = db.Column(db.Integer, default=0)
    added_json = db.Column(db.Text, nullable=True)   # JSON 序列化的新增设备列表
    skipped_json = db.Column(db.Text, nullable=True) # JSON 序列化的跳过设备列表

    def to_dict(self):
        return {
            'id': self.id,
            'rule_id': self.rule_id,
            'started_at': _isoformat_utc(self.started_at),
            'finished_at': _isoformat_utc(self.finished_at),
            'scanned': self.scanned,
            'added_count': self.added_count,
            'added': json.loads(self.added_json or '[]'),
            'skipped': json.loads(self.skipped_json or '[]'),
        }
