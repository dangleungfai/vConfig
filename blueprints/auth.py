# -*- coding: utf-8 -*-
"""Authentication routes."""
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from config import DEFAULT_PASSWORD, DEFAULT_USERNAME
from models import LoginLog, User, db


def create_auth_blueprint(deps):
    bp = Blueprint('auth', __name__)
    ensure_tables = deps['ensure_tables']
    ensure_user_password_column = deps['ensure_user_password_column']
    ensure_super_admin = deps['ensure_super_admin']
    ensure_user_record = deps['ensure_user_record']
    get_setting = deps['get_setting']
    check_login_locked = deps['check_login_locked']
    login_fail_record = deps['login_fail_record']
    login_fail_clear = deps['login_fail_clear']
    write_audit = deps['write_audit']

    @bp.route('/login', methods=['GET'])
    def login_view():
        if session.get('user'):
            return redirect(url_for('pages.index'))
        return render_template('login.html')

    @bp.route('/api/login', methods=['POST'])
    def api_login():
        ensure_tables()
        data = request.get_json(force=True, silent=True) or {}
        username = (data.get('username') or '').strip()
        password = data.get('password') or ''
        if not username or not password:
            return jsonify({'error': '用户名和密码不能为空'}), 400

        locked, remain = check_login_locked()
        if locked:
            return jsonify({'error': f'登录失败次数过多，请 {remain} 分钟后再试。'}), 403

        try:
            ensure_user_password_column()
            ensure_super_admin()
            user = User.query.filter_by(username=username).first()
            if user is not None and user.is_active is False:
                return jsonify({'error': '该用户已被禁用，请联系系统管理员。'}), 403
        except Exception:
            pass

        authed = False
        auth_source = None

        try:
            user = User.query.filter_by(username=username, source='local').first()
        except Exception:
            user = None
        if user is not None and user.check_password(password):
            authed = True
            auth_source = 'local'

        if not authed:
            local_user = get_setting('username', DEFAULT_USERNAME)
            local_pass = get_setting('password', DEFAULT_PASSWORD)
            if username == local_user and password == local_pass:
                authed = True
                auth_source = 'local'
                _upsert_legacy_local_user(username, password, ensure_user_password_column)

        if not authed and get_setting('ldap_enabled', '0') == '1':
            ok, error = _authenticate_ldap(username, password, get_setting)
            if error:
                status = 500 if error.startswith('服务器未安装') or error.startswith('LDAP 配置不完整') else 401
                return jsonify({'error': error}), status
            authed = ok
            auth_source = 'ldap'

        if not authed:
            login_fail_record()
            return jsonify({'error': '用户名或密码错误'}), 401

        login_fail_clear()
        session['user'] = username
        session['auth_source'] = auth_source or 'local'

        _record_login(username, auth_source or 'local')

        try:
            ensure_user_record(username, auth_source or 'local')
        except Exception:
            pass

        write_audit('login_success', resource_type='auth', resource_id=username)
        return jsonify({'ok': True})

    @bp.route('/api/ldap/test', methods=['POST'])
    def api_ldap_test():
        ensure_tables()
        if get_setting('ldap_enabled', '0') != '1':
            return jsonify({'ok': False, 'message': 'LDAP 未启用，请先在设置中勾选「启用 LDAP 登录」。'}), 400
        data = request.get_json(force=True, silent=True) or {}
        username = (data.get('username') or '').strip()
        password = data.get('password') or ''
        if not username or not password:
            return jsonify({'ok': False, 'message': '请输入测试用户名和密码。'}), 400
        ok, message, status = _test_ldap(username, password, get_setting)
        return jsonify({'ok': ok, 'message': message}), status

    @bp.route('/logout')
    def logout_view():
        user = session.get('user') or ''
        write_audit('logout', resource_type='auth', resource_id=user)
        session.clear()
        return redirect(url_for('auth.login_view'))

    return bp


def _upsert_legacy_local_user(username, password, ensure_user_password_column):
    try:
        ensure_user_password_column()
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username[:128],
                display_name=username[:128],
                source='local',
                role='admin',
                is_active=True,
            )
            db.session.add(user)
        user.set_password(password)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _record_login(username, auth_source):
    try:
        raw = request.headers.get('X-Forwarded-For') or request.headers.get('X-Real-IP') or (getattr(request, 'remote_addr', None) or '')
        src_ip = (raw.split(',')[0].strip() if isinstance(raw, str) and ',' in raw else raw) or ''
        if not isinstance(src_ip, str):
            src_ip = str(src_ip)
        db.session.add(LoginLog(username=username, source_ip=src_ip, auth_source=auth_source))
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _authenticate_ldap(username, password, get_setting):
    try:
        from ldap3 import ALL, Connection, Server
    except ImportError:
        return False, '服务器未安装 ldap3，无法使用 LDAP 登录'
    ldap_server = get_setting('ldap_server', '').strip()
    ldap_base_dn = get_setting('ldap_base_dn', '').strip()
    ldap_bind_dn = get_setting('ldap_bind_dn', '').strip()
    ldap_bind_password = get_setting('ldap_bind_password', '')
    ldap_user_filter = (get_setting('ldap_user_filter', '(uid={username})') or '(uid={username})').strip()
    if not ldap_server or not ldap_base_dn:
        return False, 'LDAP 配置不完整（服务器或 Base DN 未设置）'
    try:
        server = Server(ldap_server, get_info=ALL)
        conn = Connection(server, user=ldap_bind_dn, password=ldap_bind_password, auto_bind=True) if ldap_bind_dn else Connection(server, auto_bind=True)
        search_filter = ldap_user_filter.replace('{username}', username)
        if not conn.search(ldap_base_dn, search_filter):
            return False, 'LDAP 登录失败：未找到该用户'
        user_dn = conn.entries[0].entry_dn
        user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
        user_conn.unbind()
        conn.unbind()
        return True, ''
    except Exception as exc:
        return False, f'LDAP 登录失败：{exc}'


def _test_ldap(username, password, get_setting):
    try:
        from ldap3 import ALL, Connection, Server
    except ImportError:
        return False, '服务器未安装 ldap3，无法使用 LDAP 登录。', 500

    ldap_server = get_setting('ldap_server', '').strip()
    ldap_base_dn = get_setting('ldap_base_dn', '').strip()
    ldap_bind_dn = get_setting('ldap_bind_dn', '').strip()
    ldap_bind_password = get_setting('ldap_bind_password', '')
    ldap_user_filter = (get_setting('ldap_user_filter', '(uid={username})') or '(uid={username})').strip()
    if not ldap_server or not ldap_base_dn:
        return False, 'LDAP 配置不完整（服务器地址或 Base DN 未设置）。', 500

    search_filter = ldap_user_filter.replace('{username}', username)
    try:
        server = Server(ldap_server, get_info=ALL)
        conn = Connection(server, user=ldap_bind_dn, password=ldap_bind_password, auto_bind=True) if ldap_bind_dn else Connection(server, auto_bind=True)
        found = conn.search(ldap_base_dn, search_filter)
        if not found or not conn.entries:
            conn.unbind()
            return False, f'未找到该用户（filter={search_filter}, base_dn={ldap_base_dn}）', 200
        user_dn = conn.entries[0].entry_dn
        try:
            user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
            user_conn.unbind()
            conn.unbind()
            return True, f'LDAP 登录测试成功，用户 DN: {user_dn}（filter={search_filter}）', 200
        except Exception as exc:
            conn.unbind()
            return False, f'用户密码校验失败：{exc}（user_dn={user_dn}, filter={search_filter}）', 200
    except Exception as exc:
        return False, f'LDAP 连接或搜索失败：{exc}（filter={search_filter}, base_dn={ldap_base_dn}）', 200
