# -*- coding: utf-8 -*-
"""CSV report export routes."""
import csv
import io
from datetime import datetime, timedelta

from flask import Blueprint, Response, request

from models import BackupLog, Device


def create_reports_blueprint(deps):
    bp = Blueprint('reports', __name__)
    ensure_tables = deps['ensure_tables']
    ensure_device_group_column = deps['ensure_device_group_column']
    current_user_allowed_groups = deps['current_user_allowed_groups']
    write_audit = deps['write_audit']

    @bp.route('/api/dashboard/export-no-backup-24h')
    def export_no_backup_24h_csv():
        try:
            ensure_tables()
            ensure_device_group_column()
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
        allowed_grps = current_user_allowed_groups()
        if allowed_grps is not None:
            enabled_devs = [
                d for d in enabled_devs
                if (d.device_group or '').strip() in allowed_grps
                or (not (d.device_group or '').strip() and '（未分组）' in allowed_grps)
            ]
        no_backup = [d for d in enabled_devs if (d.hostname or '') not in recent_ok_hosts]
        buf = io.StringIO()
        buf.write('\ufeff')
        writer = csv.writer(buf)
        writer.writerow(['主机名', '管理IP', '设备类型', '分组'])
        for device in no_backup:
            writer.writerow([device.hostname, device.ip, device.device_type or '', (device.device_group or '').strip() or ''])
        buf.seek(0)
        return Response(
            buf.getvalue(),
            mimetype='text/csv; charset=utf-8-sig',
            headers={'Content-Disposition': 'attachment; filename=no_backup_24h.csv'},
        )

    @bp.route('/api/devices/export')
    def export_devices_csv():
        enabled = request.args.get('enabled')
        q = Device.query.order_by(Device.hostname)
        if enabled is not None:
            q = q.filter(Device.enabled == (enabled.lower() == 'true'))
        devices = q.all()
        buf = io.StringIO()
        buf.write('\ufeff')
        writer = csv.writer(buf)
        writer.writerow(['主机名', '管理IP', '设备类型', '分组', '连接方式', '启用', '备注'])
        for device in devices:
            conn = (device.connection_type or '').upper() or '默认'
            group = (device.device_group or '').strip() or ''
            writer.writerow([device.hostname, device.ip, device.device_type, group, conn, '是' if device.enabled else '否', ''])
        buf.seek(0)
        try:
            write_audit('export_devices_csv', resource_type='device', resource_id='', detail=f'count={len(devices)}')
        except Exception:
            pass

        return Response(
            buf.getvalue(),
            mimetype='text/csv; charset=utf-8-sig',
            headers={'Content-Disposition': 'attachment; filename=devices.csv'},
        )

    return bp
