# -*- coding: utf-8 -*-
"""Backup log query routes."""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from config import DEFAULT_TIMEZONE
from models import BackupJobRun, BackupLog, Device


def create_backup_logs_blueprint(deps):
    bp = Blueprint('backup_logs', __name__)
    get_setting = deps['get_setting']
    get_zoneinfo = deps['get_zoneinfo']

    @bp.route('/api/logs', methods=['GET'])
    def list_logs():
        page = request.args.get('page', 1, type=int)
        default_pp = int(get_setting('log_per_page_default', '50') or '50')
        per_page = min(request.args.get('per_page', default_pp, type=int), 100)
        hostname = request.args.get('hostname', '').strip()
        ip = request.args.get('ip', '').strip()
        search = request.args.get('search', '').strip()
        status = request.args.get('status', '').strip()
        device_id = request.args.get('device_id', type=int)
        fail_only = request.args.get('fail_only', '') in ('1', 'true', 'yes')
        date_str = request.args.get('date', '').strip()
        job_id = request.args.get('job_id', '').strip()
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
        q = BackupLog.query.order_by(order_col.desc() if sort_dir == 'desc' else order_col.asc())

        if search:
            from sqlalchemy import or_
            q = q.filter(or_(BackupLog.hostname.ilike(f'%{search}%'), BackupLog.ip.ilike(f'%{search}%')))
        else:
            if hostname:
                q = q.filter(BackupLog.hostname.ilike(f'%{hostname}%'))
            if ip:
                q = q.filter(BackupLog.ip.ilike(f'%{ip}%'))
        if device_id:
            dev = Device.query.get(device_id)
            if dev:
                q = q.filter(BackupLog.hostname == dev.hostname)
        if status:
            q = q.filter(BackupLog.status == status)
        if fail_only:
            q = q.filter(BackupLog.status != 'OK')
        if date_str:
            q = _apply_date_filter(q, date_str, get_setting, get_zoneinfo)
        if job_id:
            q = _apply_job_filter(q, job_id)

        pagination = q.paginate(page=page, per_page=per_page)
        timezone = get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
        return jsonify({
            'items': [x.to_dict() for x in pagination.items],
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'timezone': timezone,
            'sort_by': sort_by,
            'sort_dir': sort_dir,
        })

    @bp.route('/api/devices/<int:pk>/history')
    def device_backup_history(pk):
        dev = Device.query.get_or_404(pk)
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 50)
        q = BackupLog.query.filter(BackupLog.hostname == dev.hostname).order_by(BackupLog.created_at.desc())
        pagination = q.paginate(page=page, per_page=per_page)
        timezone = get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
        return jsonify({
            'hostname': dev.hostname,
            'items': [x.to_dict() for x in pagination.items],
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'timezone': timezone,
        })

    return bp


def _apply_date_filter(q, date_str, get_setting, get_zoneinfo):
    try:
        from datetime import timezone as dt_timezone
        ZoneInfo = get_zoneinfo()
        tz_name = get_setting('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
        if ZoneInfo:
            tz = ZoneInfo(tz_name)
            day_start = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=tz)
            day_end = day_start + timedelta(days=1)
            day_start_utc = day_start.astimezone(dt_timezone.utc).replace(tzinfo=None)
            day_end_utc = day_end.astimezone(dt_timezone.utc).replace(tzinfo=None)
            q = q.filter(BackupLog.created_at >= day_start_utc, BackupLog.created_at < day_end_utc)
    except Exception:
        pass
    return q


def _apply_job_filter(q, job_id):
    run = BackupJobRun.query.get(job_id)
    if run and run.start_time and run.end_time:
        try:
            from datetime import timezone as dt_timezone
            st = datetime.fromisoformat((run.start_time or '').replace('Z', '+00:00'))
            et = datetime.fromisoformat((run.end_time or '').replace('Z', '+00:00'))
            if getattr(st, 'tzinfo', None):
                st = st.astimezone(dt_timezone.utc).replace(tzinfo=None)
            if getattr(et, 'tzinfo', None):
                et = et.astimezone(dt_timezone.utc).replace(tzinfo=None)
            q = q.filter(BackupLog.created_at >= st, BackupLog.created_at <= et, BackupLog.status != 'OK')
        except Exception:
            pass
    return q
