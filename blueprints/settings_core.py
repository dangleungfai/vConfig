# -*- coding: utf-8 -*-
"""Core system settings routes."""
import os

from flask import Blueprint, jsonify, request, url_for

from config import BACKUP_RETENTION_DAYS, BACKUP_THREAD_NUM, DEFAULT_CONNECTION_TYPE, DEFAULT_TIMEZONE, SSH_PORT


def create_settings_core_blueprint(deps):
    bp = Blueprint('settings_core', __name__)
    current_role = deps['current_role']
    can_edit_settings = deps['can_edit_settings']
    get_setting = deps['get_setting']
    set_setting = deps['set_setting']
    setting_has_secret_value = deps['setting_has_secret_value']
    get_default_settings = deps['get_default_settings']
    reload_backup_schedule = deps['reload_backup_schedule']
    write_audit = deps['write_audit']
    logo_dir = deps['logo_dir']

    @bp.route('/api/settings', methods=['GET'])
    def get_settings():
        if current_role() == 'viewer':
            return jsonify({'error': '只读用户无权访问系统设置'}), 403

        discovery_type_keywords = get_setting('discovery_type_keywords', '') or ''
        if not discovery_type_keywords:
            discovery_type_keywords = "\n".join([
                "Cisco=Cisco,IOS,ISR,ASR,NCS",
                "Juniper=Juniper,JUNOS",
                "Huawei=Huawei,FutureMatrix,VRP",
                "H3C=H3C",
                "RouterOS=RouterOS,MikroTik,CHR",
            ])

        logo_file = get_setting('logo_file', '') or ''
        return jsonify({
            'username': get_setting('username', ''),
            'password': '',
            'password_configured': setting_has_secret_value('password'),
            'system_name': get_setting('system_name', '配置备份中心'),
            'backup_frequency': get_setting('backup_frequency', 'daily') or 'daily',
            'default_connection_type': get_setting('default_connection_type', DEFAULT_CONNECTION_TYPE).upper() or 'SSH',
            'backup_retention_days': get_setting('backup_retention_days', str(BACKUP_RETENTION_DAYS)),
            'timezone': get_setting('timezone', DEFAULT_TIMEZONE),
            'language': get_setting('language', 'zh') or 'zh',
            'footer_text': get_setting('footer_text', ''),
            'logo_url': url_for('settings_assets.logo') if logo_file else '',
            'logo_enabled': bool(logo_file),
            'session_timeout_minutes': get_setting('session_timeout_minutes', '0'),
            'login_lockout_attempts': get_setting('login_lockout_attempts', '0'),
            'login_lockout_minutes': get_setting('login_lockout_minutes', '15'),
            'password_min_length': get_setting('password_min_length', '6'),
            'password_require_digit': get_setting('password_require_digit', '0'),
            'password_require_upper': get_setting('password_require_upper', '0'),
            'password_require_lower': get_setting('password_require_lower', '0'),
            'password_require_special': get_setting('password_require_special', '0'),
            'device_per_page_default': get_setting('device_per_page_default', '50'),
            'log_per_page_default': get_setting('log_per_page_default', '50'),
            'snmp_version': get_setting('snmp_version', '2c'),
            'snmp_community': get_setting('snmp_community', 'public'),
            'snmp_timeout_ms': get_setting('snmp_timeout_ms', '2000'),
            'snmp_retries': get_setting('snmp_retries', '1'),
            'backup_timeout_seconds': get_setting('backup_timeout_seconds', '30'),
            'backup_read_timeout_seconds': get_setting('backup_read_timeout_seconds', '30'),
            'backup_thread_num': get_setting('backup_thread_num', str(BACKUP_THREAD_NUM)),
            'backup_connection_fallback': get_setting('backup_connection_fallback', '0'),
            'ssh_port': get_setting('ssh_port', str(SSH_PORT)),
            'telnet_port': get_setting('telnet_port', '23'),
            'alert_webhook_url': get_setting('alert_webhook_url', ''),
            'alert_smtp_host': get_setting('alert_smtp_host', ''),
            'alert_smtp_port': get_setting('alert_smtp_port', '587'),
            'alert_smtp_user': get_setting('alert_smtp_user', ''),
            'alert_smtp_password': '',
            'alert_smtp_password_configured': setting_has_secret_value('alert_smtp_password'),
            'alert_smtp_from': get_setting('alert_smtp_from', ''),
            'alert_smtp_use_tls': get_setting('alert_smtp_use_tls', '1'),
            'alert_email_to': get_setting('alert_email_to', ''),
            'alert_on_backup_fail_email': get_setting('alert_on_backup_fail_email', '0'),
            'alert_on_backup_fail_webhook': get_setting('alert_on_backup_fail_webhook', '1'),
            'alert_on_discovery_new_email': get_setting('alert_on_discovery_new_email', '0'),
            'alert_on_discovery_new_webhook': get_setting('alert_on_discovery_new_webhook', '0'),
            'api_tokens': '',
            'api_tokens_configured': setting_has_secret_value('api_tokens'),
            'discovery_frequency': get_setting('discovery_frequency', 'twice_daily'),
            'discovery_type_keywords': discovery_type_keywords,
            'discovery_hostname_split_char': get_setting('discovery_hostname_split_char', ''),
            'discovery_hostname_segment_index': get_setting('discovery_hostname_segment_index', '1'),
            'discovery_unique_by': get_setting('discovery_unique_by', 'hostname') or 'hostname',
            'ldap_enabled': get_setting('ldap_enabled', '0'),
            'ldap_server': get_setting('ldap_server', ''),
            'ldap_base_dn': get_setting('ldap_base_dn', ''),
            'ldap_bind_dn': get_setting('ldap_bind_dn', ''),
            'ldap_bind_password': '',
            'ldap_bind_password_configured': setting_has_secret_value('ldap_bind_password'),
            'ldap_user_filter': get_setting('ldap_user_filter', '(uid={username})'),
            'can_edit_settings': can_edit_settings(),
        })

    @bp.route('/api/settings', methods=['PUT'])
    def update_settings():
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权修改全局设置，请使用全局用户名登录。'}), 403

        data = request.get_json(force=True, silent=True) or {}
        if data.get('username') is not None:
            set_setting('username', str(data['username']))
        if data.get('password_clear') in (True, 1, '1', 'true', 'on', 'yes'):
            set_setting('password', '')
        elif str(data.get('password') or '').strip():
            set_setting('password', str(data.get('password') or '').strip())
        if data.get('backup_frequency') is not None:
            set_setting('backup_frequency', str(data['backup_frequency']))
        if data.get('default_connection_type') is not None:
            ct = str(data['default_connection_type']).strip().upper()
            set_setting('default_connection_type', ct if ct in ('TELNET', 'SSH') else 'TELNET')
        if 'backup_connection_fallback' in data:
            set_setting('backup_connection_fallback', '1' if data.get('backup_connection_fallback') in (True, 1, '1', 'true', 'on') else '0')
        if data.get('system_name') is not None:
            name = (str(data.get('system_name') or '').strip())[:100]
            set_setting('system_name', name or '配置备份中心')
        if data.get('backup_retention_days') is not None:
            try:
                v = int(data['backup_retention_days'])
                set_setting('backup_retention_days', str(max(0, min(3650, v))))
            except (TypeError, ValueError):
                set_setting('backup_retention_days', str(BACKUP_RETENTION_DAYS))
        if data.get('timezone') is not None:
            set_setting('timezone', str(data['timezone']).strip() or DEFAULT_TIMEZONE)
        if data.get('language') is not None:
            lang = str(data.get('language') or 'zh').strip().lower()
            set_setting('language', lang if lang in ('zh', 'en') else 'zh')
        set_setting('footer_text', (str(data.get('footer_text', '') or '').strip())[:500])

        if 'ldap_enabled' in data:
            enabled = str(data.get('ldap_enabled') or '0').strip()
            set_setting('ldap_enabled', '1' if enabled in ('1', 'true', 'on', 'yes') else '0')
        for key in ('ldap_server', 'ldap_base_dn', 'ldap_bind_dn'):
            if key in data:
                set_setting(key, str(data.get(key) or '').strip())
        if data.get('ldap_bind_password_clear') in (True, 1, '1', 'true', 'on', 'yes'):
            set_setting('ldap_bind_password', '')
        elif str(data.get('ldap_bind_password') or '').strip():
            set_setting('ldap_bind_password', str(data.get('ldap_bind_password') or '').strip())
        if 'ldap_user_filter' in data:
            set_setting('ldap_user_filter', str(data.get('ldap_user_filter') or '(uid={username})').strip())

        _set_int_setting(data, set_setting, 'session_timeout_minutes', 0, 1440, '0', allow_empty_zero=True)
        _set_int_setting(data, set_setting, 'login_lockout_attempts', 0, 20, '0')
        _set_int_setting(data, set_setting, 'login_lockout_minutes', 0, 120, '15')
        if data.get('password_min_length') is not None:
            try:
                n = int(data['password_min_length'])
                set_setting('password_min_length', str(max(6, min(32, n))) if n else '')
            except (TypeError, ValueError):
                set_setting('password_min_length', '6')
        for key in ('password_require_digit', 'password_require_upper', 'password_require_lower', 'password_require_special'):
            if key in data:
                set_setting(key, '1' if data.get(key) in (True, 1, '1', 'true', 'on') else '0')
        _set_choice_setting(data, set_setting, 'device_per_page_default', {'20', '50', '100', '200'}, '50')
        _set_choice_setting(data, set_setting, 'log_per_page_default', {'20', '50', '100'}, '50')

        _set_int_setting(data, set_setting, 'backup_timeout_seconds', 5, 300, '30')
        _set_int_setting(data, set_setting, 'backup_read_timeout_seconds', 10, 300, '30')
        _set_int_setting(data, set_setting, 'backup_thread_num', 1, 50, str(BACKUP_THREAD_NUM))
        _set_int_setting(data, set_setting, 'ssh_port', 1, 65535, str(SSH_PORT))
        _set_int_setting(data, set_setting, 'telnet_port', 1, 65535, '23')
        if 'alert_webhook_url' in data:
            set_setting('alert_webhook_url', (str(data.get('alert_webhook_url') or '').strip())[:512])

        for key in ('alert_smtp_host', 'alert_smtp_user', 'alert_smtp_from', 'alert_email_to'):
            if key in data:
                set_setting(key, str(data.get(key) or '').strip()[:256])
        if data.get('alert_smtp_password_clear') in (True, 1, '1', 'true', 'on', 'yes'):
            set_setting('alert_smtp_password', '')
        elif str(data.get('alert_smtp_password') or '').strip():
            set_setting('alert_smtp_password', str(data.get('alert_smtp_password') or '').strip()[:256])
        _set_int_setting(data, set_setting, 'alert_smtp_port', 1, 65535, '587')
        if 'alert_smtp_use_tls' in data:
            set_setting('alert_smtp_use_tls', '1' if data.get('alert_smtp_use_tls') in (True, 1, '1', 'true', 'on') else '0')
        for key in ('alert_on_backup_fail_email', 'alert_on_backup_fail_webhook', 'alert_on_discovery_new_email', 'alert_on_discovery_new_webhook'):
            if key in data:
                set_setting(key, '1' if data.get(key) in (True, 1, '1', 'true', 'on') else '0')
        if data.get('api_tokens_clear') in (True, 1, '1', 'true', 'on', 'yes'):
            set_setting('api_tokens', '')
        elif str(data.get('api_tokens') or '').strip():
            set_setting('api_tokens', str(data.get('api_tokens') or '').strip()[:1024])

        if 'snmp_version' in data:
            version = str(data.get('snmp_version') or '2c').strip()
            set_setting('snmp_version', version if version in ('1', '2c', '3') else '2c')
        if 'snmp_community' in data:
            set_setting('snmp_community', str(data.get('snmp_community') or 'public').strip())
        _set_int_setting(data, set_setting, 'snmp_timeout_ms', 500, 10000, '2000')
        _set_int_setting(data, set_setting, 'snmp_retries', 0, 5, '1')

        if 'discovery_type_keywords' in data:
            set_setting('discovery_type_keywords', str(data.get('discovery_type_keywords') or '').strip())
        if 'discovery_hostname_split_char' in data:
            set_setting('discovery_hostname_split_char', str(data.get('discovery_hostname_split_char') or '').strip()[:4])
        _set_int_setting(data, set_setting, 'discovery_hostname_segment_index', 1, 10, '1')
        if 'discovery_unique_by' in data:
            v = (data.get('discovery_unique_by') or 'hostname').strip().lower()
            set_setting('discovery_unique_by', v if v in ('hostname', 'ip') else 'hostname')
        if 'discovery_frequency' in data:
            freq = str(data.get('discovery_frequency') or 'none').strip()
            allowed = {'none', 'hourly', 'twice_daily', 'daily', 'weekly', 'custom'}
            if freq not in allowed and len(freq.split()) < 5:
                freq = 'none'
            set_setting('discovery_frequency', freq)

        if data.get('backup_frequency') is not None or 'discovery_frequency' in data:
            reload_backup_schedule()
        write_audit('update_settings', resource_type='settings', resource_id='', detail='global settings updated')
        return jsonify({'ok': True})

    @bp.route('/api/settings/reset-defaults', methods=['POST'])
    def reset_settings_to_defaults():
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权修改全局设置，请使用管理员账号登录。'}), 403
        defaults = get_default_settings()
        old_logo = get_setting('logo_file', '')
        for key, value in defaults.items():
            set_setting(key, value if value is not None else '')
        if defaults.get('logo_file') == '' and old_logo:
            safe_name = os.path.basename(old_logo)
            path = os.path.join(logo_dir, safe_name)
            if os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        reload_backup_schedule()
        write_audit('reset_settings_defaults', resource_type='settings', resource_id='', detail='all settings reset to defaults')
        return jsonify({'ok': True})

    return bp


def _set_int_setting(data, set_setting, key, minimum, maximum, default, allow_empty_zero=False):
    if data.get(key) is None:
        return
    try:
        raw = data[key]
        if raw == '':
            if allow_empty_zero:
                n = 0
            else:
                raise ValueError()
        else:
            n = int(raw)
        set_setting(key, str(max(minimum, min(maximum, n))))
    except (TypeError, ValueError):
        set_setting(key, '0' if allow_empty_zero else default)


def _set_choice_setting(data, set_setting, key, allowed, default):
    if data.get(key) is None:
        return
    value = str(data.get(key) or default).strip()
    if value in allowed:
        set_setting(key, value)
