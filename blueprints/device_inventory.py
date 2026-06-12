# -*- coding: utf-8 -*-
"""Device inventory CRUD routes."""
from flask import Blueprint, jsonify, request

from config import DEFAULT_CONNECTION_TYPE
from models import Device, db


def create_device_inventory_blueprint(deps):
    bp = Blueprint('device_inventory', __name__)
    can_edit_settings = deps['can_edit_settings']
    current_user_allowed_groups = deps['current_user_allowed_groups']
    get_setting = deps['get_setting']
    normalize_device_type = deps['normalize_device_type']
    write_audit = deps['write_audit']

    @bp.route('/api/devices', methods=['GET'])
    def list_devices():
        enabled = request.args.get('enabled')
        page = request.args.get('page', 1, type=int)
        default_pp = int(get_setting('device_per_page_default', '50') or '50')
        per_page = max(1, min(request.args.get('per_page', default_pp, type=int), 200))
        site = request.args.get('site', '').strip()
        dev_type = request.args.get('device_type', '').strip()
        group = request.args.get('group', '').strip()
        search = request.args.get('search', '').strip()
        sort_by = (request.args.get('sort_by') or 'hostname').strip()
        sort_dir = (request.args.get('sort_dir') or 'asc').strip().lower()

        q = _apply_device_scope(Device.query, current_user_allowed_groups)
        if enabled is not None:
            q = q.filter(Device.enabled == (enabled.lower() == 'true'))
        if site:
            q = q.filter(Device.hostname.like(f'{site}.%'))
        if group:
            q = q.filter(Device.device_group == group)
        if dev_type:
            q = q.filter(Device.device_type == normalize_device_type(dev_type))
        if search:
            q = q.filter((Device.hostname.ilike(f'%{search}%')) | (Device.ip.ilike(f'%{search}%')))

        order_col = {
            'ip': Device.ip,
            'device_type': Device.device_type,
            'group': Device.device_group,
        }.get(sort_by, Device.hostname)
        if sort_dir == 'desc':
            order_col = order_col.desc()
        else:
            sort_dir = 'asc'
        q = q.order_by(order_col, Device.id.asc())

        pagination = q.paginate(page=page, per_page=per_page)
        default_conn = get_setting('default_connection_type', DEFAULT_CONNECTION_TYPE).upper() or 'TELNET'
        from_devices = {r[0] for r in Device.query.with_entities(Device.device_group).distinct().all() if r[0]}
        predefined = [g.strip() for g in (get_setting('device_groups', '') or '').split(',') if g.strip()]
        return jsonify({
            'items': [d.to_dict() for d in pagination.items],
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'groups': sorted(set(predefined) | from_devices),
            'default_connection_type': default_conn,
            'sort_by': sort_by,
            'sort_dir': sort_dir,
            'can_manage_devices': can_edit_settings(),
        })

    @bp.route('/api/devices', methods=['POST'])
    def add_device():
        data = request.get_json()
        if not data or not data.get('ip') or not data.get('hostname') or not data.get('device_type'):
            return jsonify({'error': '缺少 ip/hostname/device_type'}), 400
        dev = Device(
            ip=data['ip'].strip(),
            hostname=data['hostname'].strip(),
            device_type=normalize_device_type(data['device_type']),
            enabled=data.get('enabled', True),
            device_group=(str(data.get('group') or '').strip())[:64] or None,
            maintenance_start=(str(data.get('maintenance_start') or '').strip())[:8] or None,
            maintenance_end=(str(data.get('maintenance_end') or '').strip())[:8] or None,
            username=data.get('username'),
            password=data.get('password'),
            connection_type=_connection_type_or_none(data.get('connection_type')),
            ssh_port=_port_or_none(data.get('ssh_port')),
            telnet_port=_port_or_none(data.get('telnet_port')),
        )
        db.session.add(dev)
        db.session.commit()
        write_audit('add_device', resource_type='device', resource_id=str(dev.id), detail=f'hostname={dev.hostname}, ip={dev.ip}')
        return jsonify(dev.to_dict())

    @bp.route('/api/devices/<int:pk>', methods=['GET', 'PUT', 'DELETE'])
    def device_detail(pk):
        dev = Device.query.get_or_404(pk)
        if request.method == 'GET':
            return jsonify(dev.to_dict())
        if request.method == 'DELETE':
            if not can_edit_settings():
                return jsonify({'error': '当前账号无权删除设备，请使用管理员账号登录。'}), 403
            write_audit('delete_device', resource_type='device', resource_id=str(dev.id), detail=f'hostname={dev.hostname}, ip={dev.ip}')
            db.session.delete(dev)
            db.session.commit()
            return jsonify({'ok': True})
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权修改设备，请使用管理员账号登录。'}), 403
        data = request.get_json(force=True, silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({'error': '请求体无效或非 JSON'}), 400
        ip_raw = (data.get('ip') or '').strip()
        hostname_raw = (data.get('hostname') or '').strip()
        if not ip_raw or not hostname_raw:
            return jsonify({'error': '缺少 IP 或主机名'}), 400
        _update_device_from_payload(dev, data, normalize_device_type)
        _force_connection_type_update(dev)
        db.session.commit()
        write_audit('update_device', resource_type='device', resource_id=str(dev.id), detail=f'hostname={dev.hostname}, ip={dev.ip}')
        return jsonify(dev.to_dict())

    @bp.route('/api/devices/batch-delete', methods=['POST'])
    def batch_delete_devices():
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权批量删除设备，请使用全局用户名登录。'}), 403
        data = request.get_json()
        ids = data.get('ids') if isinstance(data, dict) else []
        if not ids or not isinstance(ids, list):
            return jsonify({'error': '请提供 ids 数组'}), 400
        count = Device.query.filter(Device.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        write_audit('batch_delete_devices', resource_type='device', resource_id='', detail=f'ids={len(ids)}, deleted={count}')
        return jsonify({'ok': True, 'deleted': count})

    @bp.route('/api/devices/delete-all', methods=['POST'])
    def delete_all_devices():
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权操作，请使用管理员账号登录。'}), 403
        q = _apply_device_scope(Device.query, current_user_allowed_groups)
        count = q.delete(synchronize_session=False)
        db.session.commit()
        write_audit('delete_all_devices', resource_type='device', resource_id='', detail=f'deleted={count}')
        return jsonify({'ok': True, 'deleted': count})

    @bp.route('/api/devices/batch-update', methods=['POST'])
    def batch_update_devices():
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权批量修改设备，请使用管理员账号登录。'}), 403
        data = request.get_json(force=True, silent=True) or {}
        ids = data.get('ids') if isinstance(data.get('ids'), list) else []
        if not ids:
            return jsonify({'error': '请提供设备 id 列表 ids'}), 400
        devices = Device.query.filter(Device.id.in_(ids)).all()
        if not devices:
            return jsonify({'ok': True, 'updated': 0})
        single_full = len(ids) == 1 and isinstance(data.get('ip'), str) and isinstance(data.get('hostname'), str)
        updated = sum(1 for dev in devices if _batch_update_one(dev, data, single_full, normalize_device_type))
        db.session.commit()
        write_audit('batch_update_devices', resource_type='device', resource_id='', detail=f'ids={len(ids)}, updated={updated}')
        return jsonify({'ok': True, 'updated': updated})

    @bp.route('/api/devices/sites')
    def list_sites():
        rows = Device.query.with_entities(Device.hostname).distinct().all()
        prefixes = set()
        for (hostname,) in rows:
            if hostname and '.' in hostname:
                prefixes.add(hostname.split('.', 1)[0])
            elif hostname:
                prefixes.add(hostname)
        return jsonify({'sites': sorted(prefixes)})

    @bp.route('/api/devices/import', methods=['POST'])
    def import_devices():
        text = request.get_data(as_text=True) or request.form.get('text', '')
        count = 0
        for line in [line.strip() for line in text.splitlines() if line.strip()]:
            parts = line.split()
            if len(parts) >= 3:
                hostname, ip, dev_type = parts[0], parts[1], normalize_device_type(parts[2])
                group = (parts[3].strip())[:64] if len(parts) > 3 and parts[3].strip() else None
                if Device.query.filter_by(ip=ip, hostname=hostname).first():
                    continue
                db.session.add(Device(ip=ip, hostname=hostname, device_type=dev_type, device_group=group))
                count += 1
        db.session.commit()
        if count > 0:
            write_audit('import_devices', resource_type='device', resource_id='', detail=f'imported={count}')
        return jsonify({'imported': count})

    @bp.route('/api/devices/discover', methods=['POST'])
    def discover_devices():
        data = request.get_json(force=True, silent=True) or {}
        ips = _parse_discovery_ips(data)
        if not ips:
            return jsonify({'error': '请提供 ip_range（如 192.168.1.1-192.168.1.20 或 192.168.1.0/24）或 ips（每行一个 IP）'}), 400
        results = []
        for ip in ips:
            ssh_open = _check_port_open(ip, 22)
            telnet_open = _check_port_open(ip, 23)
            if ssh_open or telnet_open:
                results.append({'ip': ip, 'ssh_open': ssh_open, 'telnet_open': telnet_open})
        return jsonify({'results': results, 'scanned': len(ips)})

    return bp


def _apply_device_scope(q, current_user_allowed_groups):
    allowed_grps = current_user_allowed_groups()
    if allowed_grps is not None:
        from sqlalchemy import or_
        group_condition = Device.device_group.in_(allowed_grps)
        if '（未分组）' in allowed_grps:
            group_condition = or_(group_condition, Device.device_group.is_(None), Device.device_group == '')
        q = q.filter(group_condition)
    return q


def _connection_type_or_none(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    value = str(value).strip().upper()
    return value if value in ('TELNET', 'SSH') else None


def _port_or_none(value):
    try:
        if value is None or str(value).strip() == '':
            return None
        return max(1, min(65535, int(value)))
    except (TypeError, ValueError):
        return None


def _update_device_from_payload(dev, data, normalize_device_type):
    dev.ip = (data.get('ip') or '').strip()
    dev.hostname = (data.get('hostname') or '').strip()
    if data.get('device_type'):
        dev.device_type = normalize_device_type(data['device_type'])
    if 'enabled' in data:
        dev.enabled = data['enabled']
    if 'username' in data:
        dev.username = data['username'] or None
    if 'password' in data:
        dev.password = data['password'] if data['password'] else None
    if 'connection_type' in data:
        dev.connection_type = _connection_type_or_none(data.get('connection_type'))
    if 'group' in data:
        dev.device_group = (str(data.get('group') or '').strip())[:64] or None
    if 'maintenance_start' in data:
        dev.maintenance_start = (str(data.get('maintenance_start') or '').strip())[:8] or None
    if 'maintenance_end' in data:
        dev.maintenance_end = (str(data.get('maintenance_end') or '').strip())[:8] or None
    if 'ssh_port' in data:
        dev.ssh_port = _port_or_none(data.get('ssh_port'))
    if 'telnet_port' in data:
        dev.telnet_port = _port_or_none(data.get('telnet_port'))


def _force_connection_type_update(dev):
    try:
        from sqlalchemy import text as sql_text
        db.session.execute(sql_text("UPDATE devices SET connection_type = :ct WHERE id = :id"), {"ct": dev.connection_type, "id": dev.id})
    except Exception:
        pass


def _batch_update_one(dev, data, single_full, normalize_device_type):
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
            changed = _assign_if_changed(dev, 'device_type', normalize_device_type(str(data['device_type']))) or changed
        if 'enabled' in data:
            changed = _assign_if_changed(dev, 'enabled', bool(data['enabled'])) or changed
        if 'username' in data:
            changed = _assign_if_changed(dev, 'username', (data.get('username') or '').strip() or None) or changed
        if 'password' in data:
            value = data.get('password')
            dev.password = (value.strip() if value and str(value).strip() else None) if value else None
            changed = True
    if 'device_type' in data and data['device_type'] is not None and not single_full:
        changed = _assign_if_changed(dev, 'device_type', normalize_device_type(str(data['device_type']))) or changed
    if 'group' in data:
        changed = _assign_if_changed(dev, 'device_group', (str(data.get('group') or '').strip())[:64] or None) or changed
    if 'connection_type' in data:
        changed = _assign_if_changed(dev, 'connection_type', _connection_type_or_none(data.get('connection_type'))) or changed
    if 'ssh_port' in data:
        changed = _assign_if_changed(dev, 'ssh_port', _port_or_none(data.get('ssh_port'))) or changed
    if 'telnet_port' in data:
        changed = _assign_if_changed(dev, 'telnet_port', _port_or_none(data.get('telnet_port'))) or changed
    if 'maintenance_start' in data:
        changed = _assign_if_changed(dev, 'maintenance_start', (str(data.get('maintenance_start') or '').strip())[:8] or None) or changed
    if 'maintenance_end' in data:
        changed = _assign_if_changed(dev, 'maintenance_end', (str(data.get('maintenance_end') or '').strip())[:8] or None) or changed
    return changed


def _assign_if_changed(obj, attr, value):
    if getattr(obj, attr) != value:
        setattr(obj, attr, value)
        return True
    return False


def _parse_discovery_ips(data):
    ips = []
    if data.get('ip_range'):
        raw_range = (data.get('ip_range') or '').strip()
        if '-' in raw_range:
            start_raw, end_raw = raw_range.split('-', 1)
            try:
                import ipaddress
                start = int(ipaddress.ip_address(start_raw.strip()))
                end = int(ipaddress.ip_address(end_raw.strip()))
                for value in range(start, min(end + 1, start + 256)):
                    ips.append(str(ipaddress.ip_address(value)))
            except Exception:
                pass
        else:
            try:
                import ipaddress
                network = ipaddress.ip_network(raw_range, strict=False)
                for addr in list(network.hosts())[:256]:
                    ips.append(str(addr))
            except Exception:
                pass
    if data.get('ips'):
        ips.extend([item.strip() for item in (data.get('ips') or '').splitlines() if item.strip()])
    return list(dict.fromkeys(ips))[:128]


def _check_port_open(ip: str, port: int, timeout: float = 1.0) -> bool:
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False
