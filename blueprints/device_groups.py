# -*- coding: utf-8 -*-
"""Device group management routes."""
from flask import Blueprint, jsonify, request

from models import Device


def create_device_groups_blueprint(deps):
    bp = Blueprint('device_groups', __name__)
    can_edit_settings = deps['can_edit_settings']
    ensure_device_group_column = deps['ensure_device_group_column']
    get_setting = deps['get_setting']
    set_setting = deps['set_setting']

    @bp.route('/api/device-groups', methods=['GET'])
    def list_device_groups():
        raw = (get_setting('device_groups', '') or '').strip()
        groups = [g.strip() for g in raw.split(',') if g.strip()]
        if request.args.get('from_devices'):
            try:
                ensure_device_group_column()
            except Exception:
                pass
            from_devices = {r[0] for r in Device.query.with_entities(Device.device_group).distinct().all() if r[0]}
            for group in from_devices:
                if group and group.strip() and group.strip() not in groups:
                    groups.append(group.strip())
            if '（未分组）' not in groups:
                groups.append('（未分组）')
            groups = sorted(groups)
        return jsonify({'groups': groups})

    @bp.route('/api/device-groups', methods=['POST'])
    def create_device_group():
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权操作，请使用管理员账号登录。'}), 403
        data = request.get_json(force=True, silent=True) or {}
        name = (str(data.get('name') or '').strip())[:64]
        if not name:
            return jsonify({'error': '分组名称不能为空。'}), 400
        raw = (get_setting('device_groups', '') or '').strip()
        current = [g.strip() for g in raw.split(',') if g.strip()]
        if name in current:
            return jsonify({'error': '该分组已存在。', 'groups': current}), 400
        current.append(name)
        set_setting('device_groups', ','.join(sorted(current)))
        return jsonify({'ok': True, 'groups': sorted(current)})

    @bp.route('/api/device-groups/<path:name>', methods=['DELETE'])
    def delete_device_group(name):
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权操作，请使用管理员账号登录。'}), 403
        name = name.strip()
        raw = (get_setting('device_groups', '') or '').strip()
        current = [g.strip() for g in raw.split(',') if g.strip()]
        if name not in current:
            return jsonify({'ok': True, 'groups': [g for g in current]})
        current = [g for g in current if g != name]
        set_setting('device_groups', ','.join(current))
        return jsonify({'ok': True, 'groups': current})

    return bp
