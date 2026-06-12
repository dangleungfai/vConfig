# -*- coding: utf-8 -*-
"""Page rendering and lightweight page metadata routes."""
from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from config import DEFAULT_TIMEZONE


def create_pages_blueprint(deps):
    bp = Blueprint('pages', __name__)
    ensure_tables = deps['ensure_tables']
    ensure_connection_type_column = deps['ensure_connection_type_column']
    ensure_device_group_column = deps['ensure_device_group_column']
    ensure_device_maintenance_columns = deps['ensure_device_maintenance_columns']
    ensure_device_ssh_port_column = deps['ensure_device_ssh_port_column']
    ensure_device_telnet_port_column = deps['ensure_device_telnet_port_column']
    ensure_user_allowed_groups_column = deps['ensure_user_allowed_groups_column']
    get_setting = deps['get_setting']

    @bp.route('/')
    def index():
        try:
            ensure_tables()
            ensure_connection_type_column()
            ensure_device_group_column()
            ensure_device_maintenance_columns()
            ensure_device_ssh_port_column()
            ensure_device_telnet_port_column()
            ensure_user_allowed_groups_column()
        except Exception:
            pass
        return render_template('index.html')

    @bp.route('/configs/device/<prefix>/<path:hostname>')
    def config_device_page(prefix, hostname):
        if '..' in prefix or '..' in hostname or '/' in prefix:
            return 'Invalid path', 400
        from urllib.parse import quote
        frag = 'config-device/' + quote(prefix, safe='') + '/' + quote(hostname, safe='')
        return redirect(url_for('pages.index') + '#' + frag)

    @bp.route('/api/footer-info')
    def footer_info():
        ensure_tables()
        raw = request.headers.get('X-Forwarded-For') or request.headers.get('X-Real-IP') or (getattr(request, 'remote_addr', None) or '')
        client_ip = (raw.split(',')[0].strip() if isinstance(raw, str) and ',' in raw else raw) or ''
        if not isinstance(client_ip, str):
            client_ip = str(client_ip)
        return jsonify({
            'client_ip': client_ip,
            'timezone': get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
            'footer_text': get_setting('footer_text', '') or '',
        })

    return bp
