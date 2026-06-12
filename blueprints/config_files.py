# -*- coding: utf-8 -*-
"""Backed-up configuration file browsing, diffing, search, and compliance routes."""
import os
import re
from datetime import datetime

from flask import Blueprint, Response, jsonify, redirect, request, send_from_directory, url_for

from compliance import check_config
from models import ConfigChangeRecord, Device, db


class ConfigFilesService:
    def __init__(self, deps):
        self.configs_dir = deps['configs_dir']
        self.can_edit_settings = deps['can_edit_settings']
        self.is_admin = deps['is_admin']
        self.write_audit = deps['write_audit']
        self.ensure_tables = deps['ensure_tables']
        self.app_context = deps['app_context']
        self.logger = deps['logger']

    @staticmethod
    def config_prefix(hostname):
        """与 backup_service 一致：根据 hostname 得到配置目录的 prefix"""
        return hostname.split('.', 1)[0] if '.' in hostname else hostname

    _diff_ignore_patterns = [
        re.compile(
            r'^\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+'
            r'[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(\.\d+)?\s+[A-Z]+'
        ),
        re.compile(r'^\s*ntp\s+clock-period\s+\d+\s*$', re.IGNORECASE),
    ]
    _diff_timestamp_pattern = re.compile(
        r'\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}:\d{2}(?:\.\d+)?(?:\s*[+-]\d{1,2}:\d{2})?'
    )

    @classmethod
    def diff_canonical_line(cls, line: str) -> str:
        if not line:
            return line
        text = cls._diff_timestamp_pattern.sub('', line).strip()
        return text if text else line

    @classmethod
    def diff_config_lines(cls, content_old, content_new):
        def norm_lines(text: str):
            lines = []
            for raw in (text or '').splitlines():
                line = raw.strip()
                if not line:
                    continue
                if any(pattern.match(line) for pattern in cls._diff_ignore_patterns):
                    continue
                lines.append(line)
            return lines

        lines_old = norm_lines(content_old)
        lines_new = norm_lines(content_new)
        old_canonical = {cls.diff_canonical_line(line) for line in lines_old}
        new_canonical = {cls.diff_canonical_line(line) for line in lines_new}
        added = sorted(line for line in lines_new if cls.diff_canonical_line(line) not in old_canonical)
        removed = sorted(line for line in lines_old if cls.diff_canonical_line(line) not in new_canonical)
        return added, removed

    def resolve_config_dir(self, prefix, hostname):
        """解析配置目录实际路径，支持大小写不敏感回退。"""
        dir_path = os.path.join(self.configs_dir, prefix, hostname)
        if os.path.isdir(dir_path):
            return prefix, hostname, dir_path
        if not os.path.isdir(self.configs_dir):
            return None, None, None
        host_lower = (hostname or '').lower()
        prefix_lower = (prefix or '').lower()
        for candidate_prefix in os.listdir(self.configs_dir):
            prefix_path = os.path.join(self.configs_dir, candidate_prefix)
            if not os.path.isdir(prefix_path) or candidate_prefix.lower() != prefix_lower:
                continue
            for candidate_host in os.listdir(prefix_path):
                host_path = os.path.join(prefix_path, candidate_host)
                if os.path.isdir(host_path) and candidate_host.lower() == host_lower:
                    return candidate_prefix, candidate_host, host_path
        return None, None, None

    def dashboard_config_change_devices(self, limit=15):
        """按「最新 vs 上一份」配置变动行数排序，返回变动最多的设备列表。"""
        result = []
        for dev in Device.query.order_by(Device.hostname).all():
            prefix = self.config_prefix(dev.hostname)
            _, resolved_host, dir_path = self.resolve_config_dir(prefix, dev.hostname)
            if not dir_path or not resolved_host:
                continue
            files = sorted([name for name in os.listdir(dir_path) if name.endswith('.txt')], reverse=True)
            if len(files) < 2:
                continue
            path_new = os.path.join(dir_path, files[0])
            path_old = os.path.join(dir_path, files[1])
            try:
                with open(path_new, 'r', encoding='utf-8', errors='replace') as fh:
                    content_new = fh.read()
                with open(path_old, 'r', encoding='utf-8', errors='replace') as fh:
                    content_old = fh.read()
            except OSError:
                continue
            added, removed = self.diff_config_lines(content_old, content_new)
            change_count = len(added) + len(removed)
            if change_count == 0:
                continue
            result.append({
                'hostname': resolved_host,
                'prefix': prefix,
                'ip': (dev.ip or '').strip(),
                'device_type': (dev.device_type or '').strip(),
                'change_count': change_count,
                'added_count': len(added),
                'removed_count': len(removed),
            })
        result.sort(key=lambda item: item['change_count'], reverse=True)
        return result[:limit]

    def save_config_changes_to_db(self):
        """在备份任务完成后调用：计算配置变动并写入数据库。"""
        with self.app_context():
            devices = self.dashboard_config_change_devices(limit=30)
            try:
                self.ensure_tables()
                ConfigChangeRecord.query.delete()
                now = datetime.utcnow()
                for item in devices:
                    db.session.add(ConfigChangeRecord(
                        hostname=item.get('hostname') or '',
                        prefix=item.get('prefix') or '',
                        ip=item.get('ip') or '',
                        device_type=item.get('device_type') or '',
                        added_count=item.get('added_count', 0),
                        removed_count=item.get('removed_count', 0),
                        change_count=item.get('change_count', 0),
                        computed_at=now,
                    ))
                db.session.commit()
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                raise


def create_config_files_blueprint(service: ConfigFilesService):
    bp = Blueprint('config_files', __name__)

    @bp.route('/api/configs')
    def list_configs():
        """列出已备份的配置文件目录结构"""
        if not os.path.exists(service.configs_dir):
            return jsonify({'tree': []})
        tree = []
        for prefix in sorted(os.listdir(service.configs_dir)):
            prefix_path = os.path.join(service.configs_dir, prefix)
            if not os.path.isdir(prefix_path):
                continue
            hosts = []
            for host in sorted(os.listdir(prefix_path)):
                host_path = os.path.join(prefix_path, host)
                if not os.path.isdir(host_path):
                    continue
                files = sorted([name for name in os.listdir(host_path) if name.endswith('.txt')], reverse=True)[:20]
                hosts.append({'name': host, 'files': files})
            tree.append({'prefix': prefix, 'hosts': hosts})
        return jsonify({'tree': tree})

    @bp.route('/api/configs/devices')
    def list_configs_by_devices():
        page = max(1, request.args.get('page', 1, type=int))
        per_page = max(1, min(request.args.get('per_page', 50, type=int), 200))
        search = (request.args.get('search') or '').strip()
        sort_by = (request.args.get('sort_by') or 'hostname').strip()
        sort_dir = (request.args.get('sort_dir') or 'asc').strip().lower()
        devices = []
        for dev in Device.query.order_by(Device.hostname).all():
            prefix = service.config_prefix(dev.hostname)
            resolved_prefix, resolved_host, dir_path = service.resolve_config_dir(prefix, dev.hostname)
            files = []
            path_host = dev.hostname
            if dir_path and resolved_host:
                path_host = resolved_host
                try:
                    files = sorted([name for name in os.listdir(dir_path) if name.endswith('.txt')], reverse=True)
                except OSError:
                    pass
            devices.append({
                'hostname': path_host,
                'display_hostname': dev.hostname,
                'ip': dev.ip or '',
                'device_type': dev.device_type or '',
                'prefix': resolved_prefix if resolved_prefix else prefix,
                'files': files,
                'file_count': len(files),
            })
        if search:
            q_lower = search.lower()
            devices = [
                item for item in devices
                if q_lower in (item.get('display_hostname') or item.get('hostname') or '').lower()
                or q_lower in (item.get('ip') or '').lower()
                or q_lower in (item.get('device_type') or '').lower()
            ]
        order_cols = {'hostname': 'display_hostname', 'ip': 'ip', 'device_type': 'device_type'}
        key_field = order_cols.get(sort_by, 'display_hostname')
        devices.sort(key=lambda item: ((item.get(key_field) or item.get('hostname')) or '').lower(), reverse=sort_dir == 'desc')
        total = len(devices)
        start = (page - 1) * per_page
        end = start + per_page
        return jsonify({
            'devices': devices[start:end],
            'total': total,
            'page': page,
            'per_page': per_page,
            'sort_by': sort_by,
            'sort_dir': sort_dir,
            'can_delete_backups': service.is_admin(),
        })

    @bp.route('/api/configs/devices/<prefix>/<hostname>')
    def list_config_files_for_device(prefix, hostname):
        if '..' in prefix or '..' in hostname or '/' in prefix or '/' in hostname:
            return jsonify({'error': 'invalid'}), 400
        _, _, dir_path = service.resolve_config_dir(prefix, hostname)
        if not dir_path:
            return jsonify({'hostname': hostname, 'prefix': prefix, 'files': []})
        entries = []
        for name in sorted(os.listdir(dir_path), reverse=True):
            if not name.endswith('.txt'):
                continue
            file_path = os.path.join(dir_path, name)
            if not os.path.isfile(file_path):
                continue
            try:
                size = os.path.getsize(file_path)
            except OSError:
                size = None
            entries.append({'name': name, 'size': size})
        return jsonify({'hostname': hostname, 'prefix': prefix, 'files': entries})

    @bp.route('/api/configs/devices/<prefix>/<hostname>/diff-latest')
    def device_config_diff_latest(prefix, hostname):
        if '..' in prefix or '..' in hostname or '/' in prefix or '/' in hostname:
            return jsonify({'error': 'invalid'}), 400
        _, _, dir_path = service.resolve_config_dir(prefix, hostname)
        if not dir_path:
            return jsonify({'added': [], 'removed': [], 'hostname': hostname})
        files = sorted([name for name in os.listdir(dir_path) if name.endswith('.txt')], reverse=True)
        if len(files) < 2:
            return jsonify({'added': [], 'removed': [], 'hostname': hostname})
        try:
            with open(os.path.join(dir_path, files[0]), 'r', encoding='utf-8', errors='replace') as fh:
                content_new = fh.read()
            with open(os.path.join(dir_path, files[1]), 'r', encoding='utf-8', errors='replace') as fh:
                content_old = fh.read()
        except OSError as exc:
            service.logger.warning('读取配置 diff 失败: %s', exc)
            return jsonify({'error': 'read failed', 'added': [], 'removed': []}), 500
        added, removed = service.diff_config_lines(content_old, content_new)
        return jsonify({'hostname': hostname, 'added': added, 'removed': removed})

    @bp.route('/api/dashboard/config-changes')
    def dashboard_config_changes():
        service.ensure_tables()
        limit = max(1, min(30, request.args.get('limit', 15, type=int)))
        rows = (
            ConfigChangeRecord.query
            .order_by(ConfigChangeRecord.change_count.desc())
            .limit(limit)
            .all()
        )
        return jsonify({'devices': [row.to_dict() for row in rows]})

    @bp.route('/config-changes')
    def config_changes_page():
        return redirect(url_for('index') + '#config-changes')

    @bp.route('/api/configs/devices/<prefix>/<hostname>/delete', methods=['POST'])
    def delete_config_files_for_device(prefix, hostname):
        if not service.can_edit_settings():
            return jsonify({'error': '当前登录账号无权删除备份文件，请使用全局用户名登录。'}), 403
        if '..' in prefix or '..' in hostname or '/' in prefix or '/' in hostname:
            return jsonify({'error': 'invalid'}), 400
        _, _, dir_path = service.resolve_config_dir(prefix, hostname)
        if not dir_path:
            return jsonify({'ok': True, 'deleted': 0})
        deleted = 0
        for name in os.listdir(dir_path):
            if not name.endswith('.txt'):
                continue
            file_path = os.path.join(dir_path, name)
            if os.path.isfile(file_path):
                try:
                    os.unlink(file_path)
                    deleted += 1
                except OSError:
                    pass
        service.write_audit('delete_backups', resource_type='config', resource_id=f'{prefix}/{hostname}', detail=f'deleted={deleted}')
        return jsonify({'ok': True, 'deleted': deleted})

    @bp.route('/api/configs/<path:filepath>')
    def get_config_file(filepath):
        if '..' in filepath or filepath.startswith('/'):
            return jsonify({'error': 'invalid path'}), 400
        parts = filepath.split('/')
        if len(parts) < 3:
            full_path = os.path.normpath(os.path.join(service.configs_dir, filepath))
        else:
            prefix, hostname, fname = parts[0], parts[1], '/'.join(parts[2:])
            _, _, dir_path = service.resolve_config_dir(prefix, hostname)
            full_path = os.path.join(dir_path, fname) if dir_path else os.path.normpath(os.path.join(service.configs_dir, filepath))
        abs_configs = os.path.abspath(service.configs_dir)
        abs_full = os.path.abspath(full_path)
        if not (abs_full == abs_configs or abs_full.startswith(abs_configs + os.sep)):
            return jsonify({'error': 'invalid path'}), 400
        if not os.path.isfile(full_path):
            return jsonify({'error': 'file not found'}), 404
        rel_path = os.path.relpath(abs_full, abs_configs)
        if request.args.get('download') == '1':
            with open(full_path, 'rb') as fh:
                body = fh.read()
            name = os.path.basename(full_path)
            if not name.lower().endswith('.txt'):
                name = name + '.txt'
            return Response(
                body,
                mimetype='text/plain; charset=utf-8',
                headers={'Content-Disposition': f'attachment; filename="{name}"'},
            )
        return send_from_directory(service.configs_dir, rel_path)

    @bp.route('/api/search/configs')
    def search_configs():
        q = (request.args.get('q') or '').strip()
        limit = request.args.get('limit', 50, type=int)
        limit = max(1, min(limit, 200))
        if not q:
            return jsonify({'error': '请输入搜索关键字 q'}), 400
        if not os.path.exists(service.configs_dir):
            return jsonify({'items': []})
        q_lower = q.lower()
        host_ip = {
            hostname: ip for hostname, ip in
            Device.query.with_entities(Device.hostname, Device.ip).all()
        }
        items = []
        for prefix in sorted(os.listdir(service.configs_dir)):
            prefix_dir = os.path.join(service.configs_dir, prefix)
            if not os.path.isdir(prefix_dir):
                continue
            for host in sorted(os.listdir(prefix_dir)):
                host_dir = os.path.join(prefix_dir, host)
                if not os.path.isdir(host_dir):
                    continue
                for filename in sorted(os.listdir(host_dir)):
                    if not filename.endswith('.txt'):
                        continue
                    file_path = os.path.join(host_dir, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as fh:
                            for line_no, line in enumerate(fh, start=1):
                                if q_lower in line.lower():
                                    snippet = line.strip()
                                    if len(snippet) > 200:
                                        snippet = snippet[:197] + '...'
                                    items.append({
                                        'prefix': prefix,
                                        'hostname': host,
                                        'ip': host_ip.get(host, ''),
                                        'filename': filename,
                                        'line_no': line_no,
                                        'line': snippet,
                                    })
                                    if len(items) >= limit:
                                        return jsonify({'items': items})
                    except OSError:
                        continue
        return jsonify({'items': items})

    @bp.route('/api/compliance/<prefix>/<hostname>', methods=['GET'])
    def check_compliance(prefix, hostname):
        if '..' in prefix or '..' in hostname or '/' in prefix or '/' in hostname:
            return jsonify({'error': 'invalid'}), 400
        _, _, dir_path = service.resolve_config_dir(prefix, hostname)
        if not dir_path:
            return jsonify({'error': 'not_found', 'message': '未找到该设备的配置目录'}), 404
        candidates = [name for name in os.listdir(dir_path) if name.endswith('.txt')]
        if not candidates:
            return jsonify({'error': 'no_config', 'message': '该设备暂无备份配置文件'}), 404
        candidates.sort(reverse=True)
        latest = candidates[0]
        try:
            with open(os.path.join(dir_path, latest), 'r', encoding='utf-8', errors='ignore') as fh:
                content = fh.read()
        except OSError:
            return jsonify({'error': 'read_failed', 'message': '读取配置文件失败'}), 500
        dev = Device.query.filter_by(hostname=hostname).first()
        result = check_config(content, hostname=hostname, device_type=dev.device_type if dev else '')
        result['latest_file'] = latest
        return jsonify(result)

    return bp
