# -*- coding: utf-8 -*-
"""Settings-adjacent asset and database maintenance routes."""
import os
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, request, send_from_directory, url_for


def create_settings_assets_blueprint(deps):
    """Create routes that need app-level helpers without importing app.py."""
    bp = Blueprint('settings_assets', __name__)
    logo_dir = deps['logo_dir']
    logo_max_size = deps['logo_max_size']
    can_edit_settings = deps['can_edit_settings']
    ensure_tables = deps['ensure_tables']
    get_setting = deps['get_setting']
    set_setting = deps['set_setting']
    write_audit = deps['write_audit']

    @bp.route('/api/settings/logo', methods=['POST', 'DELETE'])
    def settings_logo():
        """上传或删除系统 Logo（仅允许有全局设置权限的用户操作）。"""
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权修改全局设置，请使用全局用户名登录。'}), 403
        ensure_tables()
        if request.method == 'DELETE':
            filename = get_setting('logo_file', '') or ''
            if filename:
                safe_name = os.path.basename(filename)
                file_path = os.path.join(logo_dir, safe_name)
                if os.path.isfile(file_path):
                    try:
                        os.unlink(file_path)
                    except OSError:
                        pass
            set_setting('logo_file', '')
            write_audit('update_settings', resource_type='settings', resource_id='', detail='logo reset to default')
            return jsonify({'ok': True})

        file = request.files.get('file')
        if not file or file.filename == '':
            return jsonify({'error': '未选择文件。'}), 400
        name = file.filename or ''
        ext = os.path.splitext(name)[1].lower()
        if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico'):
            return jsonify({'error': '仅支持 PNG/JPG/GIF/WebP/ICO 格式的图片。'}), 400
        file.stream.seek(0, os.SEEK_END)
        size = file.stream.tell()
        file.stream.seek(0)
        if size > 512 * 1024:
            return jsonify({'error': 'Logo 文件过大，请控制在 512KB 以内。'}), 400
        os.makedirs(logo_dir, exist_ok=True)
        try:
            from PIL import Image
            file.stream.seek(0)
            img = Image.open(file.stream).convert('RGBA')
            w, h = img.size
            if w > logo_max_size[0] or h > logo_max_size[1]:
                img.thumbnail(logo_max_size, Image.Resampling.LANCZOS)
                save_ext = '.png'
            else:
                save_ext = ext
            new_name = datetime.utcnow().strftime('%Y%m%d%H%M%S') + save_ext
            dest_path = os.path.join(logo_dir, new_name)
            old = get_setting('logo_file', '') or ''
            if old:
                old_path = os.path.join(logo_dir, os.path.basename(old))
                if os.path.isfile(old_path):
                    try:
                        os.unlink(old_path)
                    except OSError:
                        pass
            fmt_map = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.gif': 'GIF', '.webp': 'WEBP', '.ico': 'PNG'}
            fmt = fmt_map.get(save_ext, 'PNG')
            if fmt == 'JPEG':
                img.convert('RGB').save(dest_path, 'JPEG', quality=90)
            else:
                img.save(dest_path, fmt)
        except ImportError:
            return jsonify({'error': 'Logo 尺寸处理需要安装 Pillow，请执行 pip install Pillow。'}), 500
        except Exception:
            file.stream.seek(0)
            new_name = datetime.utcnow().strftime('%Y%m%d%H%M%S') + ext
            dest_path = os.path.join(logo_dir, new_name)
            old = get_setting('logo_file', '') or ''
            if old:
                old_path = os.path.join(logo_dir, os.path.basename(old))
                if os.path.isfile(old_path):
                    try:
                        os.unlink(old_path)
                    except OSError:
                        pass
            file.save(dest_path)
        set_setting('logo_file', new_name)
        write_audit('update_settings', resource_type='settings', resource_id='', detail='logo updated')
        return jsonify({'ok': True, 'logo_url': url_for('settings_assets.logo')})

    @bp.route('/logo')
    def logo():
        """返回当前自定义 Logo 文件（若存在）。"""
        ensure_tables()
        filename = get_setting('logo_file', '') or ''
        if not filename:
            return ('', 404)
        safe_name = os.path.basename(filename)
        file_path = os.path.join(logo_dir, safe_name)
        if not os.path.isfile(file_path):
            return ('', 404)
        return send_from_directory(logo_dir, safe_name)

    @bp.route('/api/settings/db/backup')
    def db_backup():
        """备份 MariaDB/MySQL 数据库：调用 mysqldump 导出 SQL 并提供下载。"""
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权备份数据库。'}), 403
        uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if not uri:
            return jsonify({'error': '未找到数据库连接配置，无法备份。'}), 400
        try:
            from sqlalchemy.engine.url import make_url
            import subprocess
        except ImportError:
            return jsonify({'error': '服务器缺少必要组件，无法执行 MariaDB 备份。'}), 500

        try:
            url = make_url(uri)
        except Exception as e:
            return jsonify({'error': '解析数据库连接失败: %s' % e}), 500

        driver = (url.drivername or '').split('+')[0]
        if driver not in ('mysql', 'mariadb'):
            return jsonify({'error': '当前数据库类型暂不支持在线备份，请使用数据库自带工具。'}), 400

        db_name = url.database
        if not db_name:
            return jsonify({'error': '数据库名称缺失，无法备份。'}), 400

        user = url.username or ''
        host = url.host or 'localhost'
        port = url.port or 3306
        password = url.password or ''

        if not user:
            return jsonify({'error': '数据库用户名缺失，请在 DATABASE_URL 或 MARIADB_* 中配置用户名。'}), 400

        env = os.environ.copy()
        if password:
            env['MYSQL_PWD'] = str(password)

        cmd = [
            'mysqldump',
            '-h', str(host),
            '-P', str(port),
            '-u', str(user),
            '--single-transaction',
            '--quick',
            '--skip-lock-tables',
            str(db_name),
        ]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=env,
            )
        except FileNotFoundError:
            return jsonify({'error': '未检测到 mysqldump，请在服务器上安装 MariaDB/MySQL 客户端工具后重试。'}), 500
        except Exception as e:
            return jsonify({'error': '执行 mysqldump 失败: %s' % e}), 500

        if proc.returncode != 0:
            err = proc.stderr.decode('utf-8', errors='ignore')
            return jsonify({'error': 'mysqldump 返回非零状态码: %s' % (err[:400] or proc.returncode)}), 500

        sql_data = proc.stdout
        if not sql_data:
            return jsonify({'error': '备份结果为空，请检查数据库中是否有数据。'}), 500

        fn = 'vconfig_' + datetime.utcnow().strftime('%Y%m%d') + '.sql'
        resp = Response(sql_data, mimetype='application/sql')
        resp.headers['Content-Disposition'] = 'attachment; filename=%s' % fn
        return resp

    @bp.route('/api/settings/db/restore', methods=['POST'])
    def db_restore():
        """MariaDB/MySQL 恢复不在 Web 进程内执行，避免覆盖运行中的业务库。"""
        if not can_edit_settings():
            return jsonify({'error': '当前账号无权恢复数据库。'}), 403
        return jsonify({'error': '当前版本仅支持 MariaDB/MySQL。请使用 mysql 命令或数据库运维工具导入 SQL 备份。'}), 400

    return bp
