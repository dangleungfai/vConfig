# -*- coding: utf-8 -*-
"""Device type management routes."""
from flask import Blueprint, jsonify, request
from sqlalchemy import func

from models import Device, DeviceTypeConfig, db


BUILTIN_TYPE_CODES = {'Cisco', 'Juniper', 'Huawei', 'H3C', 'RouterOS'}


def create_device_types_blueprint(deps):
    bp = Blueprint('device_types', __name__)
    can_edit_settings = deps['can_edit_settings']
    ensure_tables = deps['ensure_tables']
    get_builtin_type_config = deps['get_builtin_type_config']

    @bp.route('/api/device-types', methods=['GET'])
    def list_device_types_api():
        """设备类型列表（供前端下拉使用）。"""
        ensure_tables()
        include_disabled = request.args.get('include_disabled') in ('1', 'true', 'yes')
        query = DeviceTypeConfig.query
        if not include_disabled:
            query = query.filter_by(enabled=True)
        types = query.order_by(DeviceTypeConfig.sort_order.asc(), DeviceTypeConfig.type_code.asc()).all()
        items = [item.to_dict() for item in types]
        for item in items:
            code = (item.get('type_code') or '').strip()
            if code in BUILTIN_TYPE_CODES:
                default_cfg = get_builtin_type_config(code)
                if default_cfg:
                    item['backup_config'] = default_cfg.get('backup_config') or {}
                    item['connection_config'] = default_cfg.get('connection_config') or {}
        return jsonify({'items': items, 'can_edit_settings': can_edit_settings()})

    @bp.route('/api/device-types', methods=['POST'])
    def create_device_type_api():
        """新增设备类型，仅允许有设置权限的用户操作。"""
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权管理设备类型，请使用管理员账号登录。'}), 403
        ensure_tables()
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
        max_order = db.session.query(func.max(DeviceTypeConfig.sort_order)).scalar()
        config = DeviceTypeConfig(
            type_code=type_code,
            display_name=display_name,
            driver_type=driver_type,
            driver_module=driver_module,
            sort_order=(max_order or 0) + 1,
            enabled=enabled,
        )
        try:
            if isinstance(backup_config, dict):
                config.set_backup_config(backup_config)
            if isinstance(connection_config, dict):
                config.set_connection_config(connection_config)
            db.session.add(config)
            db.session.commit()
            return jsonify(config.to_dict()), 201
        except Exception as exc:
            try:
                db.session.rollback()
            except Exception:
                pass
            return jsonify({'error': '保存设备类型失败: %s' % exc}), 500

    @bp.route('/api/device-types/<int:type_id>', methods=['PUT'])
    def update_device_type_api(type_id):
        """更新设备类型配置，仅允许有设置权限的用户操作。"""
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权管理设备类型，请使用管理员账号登录。'}), 403
        ensure_tables()
        config = DeviceTypeConfig.query.get_or_404(type_id)
        data = request.get_json(force=True, silent=True) or {}
        display_name = (data.get('display_name') or config.display_name or '').strip()
        driver_type = (data.get('driver_type') or config.driver_type or 'generic').strip() or 'generic'
        driver_module = (data.get('driver_module') or config.driver_module or '').strip() or None
        sort_order = data.get('sort_order', config.sort_order)
        enabled = bool(data.get('enabled', config.enabled))
        backup_config = data.get('backup_config')
        connection_config = data.get('connection_config')
        if not display_name:
            return jsonify({'error': '显示名称不能为空。'}), 400
        if driver_type not in ('builtin', 'generic', 'custom'):
            return jsonify({'error': '驱动类型不合法，仅支持 builtin/generic/custom。'}), 400
        try:
            sort_order = int(sort_order)
        except (TypeError, ValueError):
            sort_order = config.sort_order or 0
        config.display_name = display_name
        config.driver_type = driver_type
        config.driver_module = driver_module
        config.sort_order = sort_order
        config.enabled = enabled
        if isinstance(backup_config, dict):
            config.set_backup_config(backup_config)
        if isinstance(connection_config, dict):
            config.set_connection_config(connection_config)
        try:
            db.session.commit()
            return jsonify(config.to_dict())
        except Exception as exc:
            try:
                db.session.rollback()
            except Exception:
                pass
            return jsonify({'error': '更新设备类型失败: %s' % exc}), 500

    @bp.route('/api/device-types/<int:type_id>', methods=['DELETE'])
    def delete_device_type_api(type_id):
        """删除设备类型：仅当没有设备使用该类型时允许删除。"""
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权管理设备类型，请使用管理员账号登录。'}), 403
        ensure_tables()
        config = DeviceTypeConfig.query.get_or_404(type_id)
        if (config.type_code or '').strip() in BUILTIN_TYPE_CODES:
            return jsonify({'error': '内置设备类型不可删除，如需隐藏可在界面中禁用。'}), 400
        used_count = Device.query.filter_by(device_type=config.type_code).count()
        if used_count > 0:
            return jsonify({'error': '当前仍有 %d 台设备使用该类型，无法删除。可先在设备列表中修改设备类型，或仅将该类型禁用。' % used_count}), 400
        try:
            db.session.delete(config)
            db.session.commit()
            return jsonify({'ok': True})
        except Exception as exc:
            try:
                db.session.rollback()
            except Exception:
                pass
            return jsonify({'error': '删除设备类型失败: %s' % exc}), 500

    return bp
