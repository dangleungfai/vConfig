# -*- coding: utf-8 -*-
"""Configuration resource index APIs."""
import csv
import io

from flask import Blueprint, Response, jsonify, request

from models import ConfigResourceInterface, ConfigResourceParseRun
from resource_indexer import rebuild_config_resource_index


RESOURCE_COLUMNS = [
    ('device_profile', '设备类型'),
    ('device_model', '设备型号'),
    ('gateway', 'PE名称'),
    ('interface_name', '接口名称'),
    ('interface_description', '接口备注'),
    ('vrf_name', 'VRF名称'),
    ('pe_address', 'PE互联地址'),
    ('secondary_ip', 'Secondary地址'),
    ('vlan_id', 'VLAN ID'),
    ('tunnel_source', '隧道源地址'),
    ('tunnel_destination', '隧道目的地址'),
    ('bandwidth', '带宽'),
    ('qos_policy', 'QoS策略'),
    ('remote_as', 'CE AS号'),
    ('backup_name', '备份标记'),
    ('load_balance', '负载标记'),
    ('customer_info', '客户信息'),
    ('config_path', '配置文件'),
]


def create_config_resources_blueprint(deps):
    bp = Blueprint('config_resources', __name__)
    configs_dir = deps['configs_dir']
    can_edit_settings = deps['can_edit_settings']
    ensure_tables = deps['ensure_tables']
    app_context = deps['app_context']
    write_audit = deps['write_audit']

    @bp.route('/api/config-resources/summary')
    def resource_summary():
        ensure_tables()
        sort = (request.args.get('sort') or 'count').strip()
        q = ConfigResourceInterface.query
        rows = (
            q.with_entities(
                ConfigResourceInterface.gateway,
                ConfigResourceInterface.device_profile,
                ConfigResourceInterface.device_model,
            )
            .all()
        )
        grouped = {}
        for gateway, profile, model in rows:
            key = (gateway or '', profile or '', model or '')
            grouped[key] = grouped.get(key, 0) + 1
        items = [
            {
                'gateway': gateway,
                'device_profile': profile,
                'device_model': model,
                'count': count,
            }
            for (gateway, profile, model), count in grouped.items()
        ]
        if sort == 'gateway':
            items.sort(key=lambda x: x['gateway'])
        else:
            items.sort(key=lambda x: x['count'], reverse=True)
        latest_run = ConfigResourceParseRun.query.order_by(ConfigResourceParseRun.id.desc()).first()
        return jsonify({
            'items': items,
            'total_gateways': len(items),
            'total_interfaces': sum(item['count'] for item in items),
            'latest_run': latest_run.to_dict() if latest_run else None,
            'can_rebuild': can_edit_settings(),
        })

    @bp.route('/api/config-resources/detail/<path:gateway>')
    def resource_detail(gateway):
        ensure_tables()
        rows = (
            ConfigResourceInterface.query
            .filter(ConfigResourceInterface.gateway == gateway)
            .order_by(ConfigResourceInterface.interface_name.asc())
            .all()
        )
        return jsonify({'items': [row.to_dict() for row in rows], 'columns': _columns_payload()})

    @bp.route('/api/config-resources/search')
    def resource_search():
        ensure_tables()
        q = ConfigResourceInterface.query
        q = _apply_filters(q, request.args)
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 500)
        pagination = q.order_by(ConfigResourceInterface.gateway.asc(), ConfigResourceInterface.interface_name.asc()).paginate(page=page, per_page=per_page)
        return jsonify({
            'items': [row.to_dict() for row in pagination.items],
            'columns': _columns_payload(),
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
        })

    @bp.route('/api/config-resources/export')
    def resource_export():
        ensure_tables()
        q = _apply_filters(ConfigResourceInterface.query, request.args)
        rows = q.order_by(ConfigResourceInterface.gateway.asc(), ConfigResourceInterface.interface_name.asc()).all()
        buf = io.StringIO()
        buf.write('\ufeff')
        writer = csv.writer(buf)
        writer.writerow([label for _, label in RESOURCE_COLUMNS])
        for row in rows:
            data = row.to_dict()
            writer.writerow([data.get(key, '') for key, _ in RESOURCE_COLUMNS])
        buf.seek(0)
        return Response(
            buf.getvalue(),
            mimetype='text/csv; charset=utf-8-sig',
            headers={'Content-Disposition': 'attachment; filename=config_resources.csv'},
        )

    @bp.route('/api/config-resources/rebuild', methods=['POST'])
    def resource_rebuild():
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权重建资源索引，请使用管理员账号登录。'}), 403
        ensure_tables()
        run = rebuild_config_resource_index(configs_dir, app_context=app_context)
        write_audit('rebuild_config_resource_index', resource_type='config_resource', resource_id=str(run.id), detail=f'indexed={run.indexed_interfaces}')
        return jsonify({'ok': True, 'run': run.to_dict()})

    return bp


def _columns_payload():
    return [{'key': key, 'label': label} for key, label in RESOURCE_COLUMNS]


def _apply_filters(q, args):
    filter_map = {
        'device_profile': ConfigResourceInterface.device_profile,
        'gateway': ConfigResourceInterface.gateway,
        'interface_name': ConfigResourceInterface.interface_name,
        'interface_description': ConfigResourceInterface.interface_description,
        'vrf_name': ConfigResourceInterface.vrf_name,
        'pe_address': ConfigResourceInterface.pe_address,
        'secondary_ip': ConfigResourceInterface.secondary_ip,
        'vlan_id': ConfigResourceInterface.vlan_id,
        'tunnel_source': ConfigResourceInterface.tunnel_source,
        'tunnel_destination': ConfigResourceInterface.tunnel_destination,
        'bandwidth': ConfigResourceInterface.bandwidth,
        'remote_as': ConfigResourceInterface.remote_as,
        'customer_info': ConfigResourceInterface.customer_info,
    }
    for key, column in filter_map.items():
        value = (args.get(key) or '').strip()
        if value:
            if key == 'gateway' and args.get('gateway_mode') == 'exact':
                q = q.filter(column == value)
            else:
                q = q.filter(column.ilike(f'%{value}%'))
    for key, column in (
        ('backup_name', ConfigResourceInterface.backup_name),
        ('load_balance', ConfigResourceInterface.load_balance),
    ):
        value = (args.get(key) or '').strip()
        if value == 'yes':
            q = q.filter(column != '')
        elif value == 'no':
            q = q.filter((column == '') | (column.is_(None)))
    return q
