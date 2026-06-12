# -*- coding: utf-8 -*-
"""Settings operations: alert tests/logs, SSL certificate upload, and service restart."""
import os
import ssl
import threading
import urllib.error
import urllib.request

from flask import Blueprint, current_app, jsonify, request

from models import AlertLog


def _validate_pem_cert(data: bytes) -> bool:
    try:
        text = data.decode('utf-8', errors='ignore')
        return '-----BEGIN CERTIFICATE-----' in text and '-----END CERTIFICATE-----' in text
    except Exception:
        return False


def _validate_pem_key(data: bytes) -> bool:
    try:
        text = data.decode('utf-8', errors='ignore')
        if '-----BEGIN PRIVATE KEY-----' in text and '-----END PRIVATE KEY-----' in text:
            return True
        if '-----BEGIN RSA PRIVATE KEY-----' in text and '-----END RSA PRIVATE KEY-----' in text:
            return True
        if '-----BEGIN EC PRIVATE KEY-----' in text and '-----END EC PRIVATE KEY-----' in text:
            return True
        return False
    except Exception:
        return False


def create_settings_ops_blueprint(deps):
    bp = Blueprint('settings_ops', __name__)
    can_edit_settings = deps['can_edit_settings']
    get_setting = deps['get_setting']
    ensure_tables = deps['ensure_tables']
    write_audit = deps['write_audit']
    webhook_body_for_url = deps['webhook_body_for_url']
    send_alert_email = deps['send_alert_email']
    log_alert = deps['log_alert']
    certs_dir = deps['certs_dir']

    @bp.route('/api/settings/test-webhook', methods=['POST'])
    def test_webhook():
        """发送一条测试告警到备份失败 Webhook URL，用于验证是否可达。"""
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权修改全局设置，请使用管理员账号登录。'}), 403
        data = request.get_json(silent=True) or {}
        url = (data.get('url') or '').strip() or (get_setting('alert_webhook_url', '') or '').strip()
        if not url:
            return jsonify({'error': '请先填写 Webhook URL'}), 400
        if not url.startswith(('http://', 'https://')):
            return jsonify({'error': 'URL 须以 http:// 或 https:// 开头'}), 400
        body = webhook_body_for_url(url, 'Hello!!!')
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        try:
            req = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': 'application/json; charset=utf-8'})
            resp = urllib.request.urlopen(req, timeout=10, context=ssl_ctx)
            status = resp.getcode() if hasattr(resp, 'getcode') else 200
            current_app.logger.info('Webhook 测试已发送: url=%s, status=%s', url, status)
            log_alert('test', 'webhook', url[:80], None, 'Hello!!!', 'success', None)
            return jsonify({'ok': True, 'status': status, 'message': '已发送测试消息 Hello!!! 至 Webhook，HTTP %d' % status})
        except urllib.error.HTTPError as exc:
            current_app.logger.info('Webhook 测试已发送: url=%s, status=%s', url, exc.code)
            log_alert('test', 'webhook', url[:80], None, 'Hello!!!', 'success', None)
            return jsonify({'ok': True, 'status': exc.code, 'message': '请求已送达，接收方返回 HTTP %d' % exc.code})
        except urllib.error.URLError as exc:
            reason = str(exc.reason) if exc.reason else str(exc)
            current_app.logger.warning('Webhook 测试请求失败: url=%s, reason=%s', url, reason)
            lowered = reason.lower()
            if 'timed out' in lowered or 'timeout' in lowered:
                return jsonify({'error': '连接超时。测试请求由 vConfig 所在服务器发出，请确保 Webhook URL 可从该服务器访问（勿填仅本机可用的地址如 localhost）。'}), 502
            if 'certificate' in lowered or 'ssl' in lowered:
                return jsonify({'error': 'SSL 证书验证失败：%s' % reason}), 502
            if 'connection refused' in lowered or 'refused' in lowered:
                return jsonify({'error': '连接被拒绝。测试请求由 vConfig 所在服务器发出，请确保 URL 可从服务器访问且服务已启动。'}), 502
            return jsonify({'error': '无法连接：%s。提示：测试由服务器发起，Webhook URL 须在服务器侧可访问。' % reason}), 502
        except Exception as exc:
            current_app.logger.warning('Webhook 测试请求异常: url=%s, err=%s', url, exc)
            return jsonify({'error': '请求失败：%s' % (str(exc) or '未知错误')}), 502

    @bp.route('/api/settings/test-email', methods=['POST'])
    def test_email():
        """发送一封测试告警邮件，用于验证 SMTP 配置。"""
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权修改全局设置，请使用管理员账号登录。'}), 403
        to_str = (get_setting('alert_email_to', '') or '').strip()
        to_list = [item.strip() for item in to_str.split(',') if item.strip()]
        if not to_list:
            return jsonify({'error': '请先配置收件人（告警邮箱地址）'}), 400
        ok, err = send_alert_email(to_list, '【vConfig】告警测试', '这是一封来自 vConfig 的告警测试邮件。若收到此邮件，说明邮箱配置正确。')
        if not ok:
            return jsonify({'error': err or '邮件发送失败'}), 502
        log_alert('test', 'email', ','.join(to_list), '【vConfig】告警测试', '测试邮件', 'success', None)
        return jsonify({'ok': True, 'message': '测试邮件已发送至 %s' % ', '.join(to_list)})

    @bp.route('/api/alert-logs', methods=['GET'])
    def list_alert_logs():
        """告警发送日志列表，支持分页与搜索。"""
        ensure_tables()
        page = max(1, int(request.args.get('page', 1)))
        per_page = max(1, min(100, int(request.args.get('per_page', 50))))
        event = (request.args.get('event_type') or '').strip()
        channel = (request.args.get('channel') or '').strip()
        query = AlertLog.query
        if event:
            query = query.filter(AlertLog.event_type == event)
        if channel:
            query = query.filter(AlertLog.channel == channel)
        pagination = query.order_by(AlertLog.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
        return jsonify({
            'items': [item.to_dict() for item in pagination.items],
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
        })

    @bp.route('/api/settings/upload-ssl-cert', methods=['POST'])
    def upload_ssl_cert():
        """用户上传自有域名证书（cert.pem + key.pem）。"""
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权修改全局设置。'}), 403
        cert_f = request.files.get('cert')
        key_f = request.files.get('key')
        if not cert_f or not cert_f.filename:
            return jsonify({'error': '请选择证书文件（.crt 或 .pem）'}), 400
        if not key_f or not key_f.filename:
            return jsonify({'error': '请选择私钥文件（.key 或 .pem）'}), 400
        try:
            cert_data = cert_f.read()
            key_data = key_f.read()
        except Exception as exc:
            return jsonify({'error': '读取文件失败：%s' % str(exc)}), 400
        if len(cert_data) < 50 or len(key_data) < 50:
            return jsonify({'error': '证书或私钥文件内容过短，请检查文件是否正确。'}), 400
        if not _validate_pem_cert(cert_data):
            return jsonify({'error': '证书格式无效，应为 PEM 格式（含 -----BEGIN CERTIFICATE-----）。'}), 400
        if not _validate_pem_key(key_data):
            return jsonify({'error': '私钥格式无效，应为 PEM 格式（含 -----BEGIN PRIVATE KEY----- 或 -----BEGIN RSA PRIVATE KEY-----）。'}), 400
        cert_file = os.path.join(certs_dir, 'cert.pem')
        key_file = os.path.join(certs_dir, 'key.pem')
        os.makedirs(certs_dir, mode=0o700, exist_ok=True)
        try:
            with open(cert_file, 'wb') as fh:
                fh.write(cert_data)
            with open(key_file, 'wb') as fh:
                fh.write(key_data)
            write_audit('upload_ssl_cert', resource_type='settings', resource_id='', detail='SSL cert uploaded by user')
            return jsonify({'ok': True, 'message': '证书已上传，请重启服务后生效。'})
        except Exception as exc:
            return jsonify({'error': '保存证书失败：%s' % str(exc)}), 500

    @bp.route('/api/settings/update-ssl-cert', methods=['POST'])
    def update_ssl_cert():
        """用户自助更新 HTTPS 自签名证书，删除旧证书并重新生成。"""
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权修改全局设置，请使用管理员账号登录。'}), 403
        cert_file = os.path.join(certs_dir, 'cert.pem')
        key_file = os.path.join(certs_dir, 'key.pem')
        for path in (cert_file, key_file):
            if os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError as exc:
                    return jsonify({'error': '删除旧证书失败：%s' % str(exc)}), 500
        os.makedirs(certs_dir, mode=0o700, exist_ok=True)
        import subprocess
        cmd = [
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
            '-keyout', key_file, '-out', cert_file,
            '-days', '36500', '-nodes',
            '-subj', '/CN=localhost/O=vConfig/C=CN',
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            write_audit('update_ssl_cert', resource_type='settings', resource_id='', detail='SSL cert regenerated')
            return jsonify({'ok': True, 'message': 'SSL 证书已重新生成，请重启服务后生效。'})
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return jsonify({'error': '生成证书失败（需安装 openssl）：%s' % str(exc)}), 500

    @bp.route('/api/settings/restart', methods=['POST'])
    def restart_service():
        """重启服务：等同于执行 systemctl restart vconfig。"""
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权重启服务，请使用管理员账号登录。'}), 403

        def do_restart():
            try:
                import subprocess
                subprocess.Popen(['systemctl', 'restart', 'vconfig'])
                current_app.logger.info('systemctl restart vconfig 已触发')
            except Exception as exc:
                current_app.logger.warning('执行 systemctl restart vconfig 失败: %s', exc)

        threading.Thread(target=do_restart, daemon=True).start()
        write_audit('restart_service', resource_type='settings', resource_id='', detail='user triggered restart via systemctl')
        return jsonify({'ok': True, 'message': '已调用 systemctl restart vconfig，服务即将重启。'})

    return bp
