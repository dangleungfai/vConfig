# -*- coding: utf-8 -*-
"""User management routes."""
from flask import Blueprint, jsonify, request
from sqlalchemy import case

from models import ROLE_ALIASES, VALID_USER_ROLES, User, db, normalize_user_role


def _is_known_user_role(role: str) -> bool:
    value = (role or '').strip().lower()
    return value in VALID_USER_ROLES or value in ROLE_ALIASES


def create_users_blueprint(deps):
    bp = Blueprint('users', __name__)
    can_edit_settings = deps['can_edit_settings']
    check_password_policy = deps['check_password_policy']
    super_admin_username = deps['super_admin_username']

    @bp.route('/api/users', methods=['GET'])
    def list_users_api():
        """用户列表：所有登录用户可查看；内置超级管理员永远排最前。"""
        users = User.query.order_by(
            case((User.username == super_admin_username, 0), else_=1),
            User.created_at.desc(),
        ).all()
        return jsonify({
            'items': [user.to_dict() for user in users],
            'can_edit_settings': can_edit_settings(),
        })

    @bp.route('/api/users', methods=['POST'])
    def create_user_api():
        """新建本地账号：仅管理员可操作。"""
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权新建用户，请使用管理员账号登录。'}), 403
        data = request.get_json(force=True, silent=True) or {}
        username = (data.get('username') or '').strip()
        if not username:
            return jsonify({'error': '用户名不能为空。'}), 400
        if username == super_admin_username:
            return jsonify({'error': '内置超级管理员账号已存在，无需重复创建。'}), 400
        if User.query.filter_by(username=username).first():
            return jsonify({'error': '该用户名已存在，请换一个。'}), 400
        role_raw = data.get('role') or 'viewer'
        if not _is_known_user_role(role_raw):
            return jsonify({'error': '角色不合法，仅支持 admin / ops / viewer。'}), 400
        password = (data.get('password') or '').strip()
        if not password:
            return jsonify({'error': '请为本地账号设置登录密码。'}), 400
        ok_pwd, msg_pwd = check_password_policy(password)
        if not ok_pwd:
            return jsonify({'error': msg_pwd}), 400
        user = User(
            username=username[:128],
            display_name=(str(data.get('display_name') or '')[:128]) or None,
            email=(str(data.get('email') or '').strip())[:128] or None,
            phone=(str(data.get('phone') or '').strip())[:32] or None,
            source='local',
            role=normalize_user_role(role_raw),
            is_active=bool(data.get('is_active', True)),
            allowed_groups=(str(data.get('allowed_groups') or '').strip())[:512] or None,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return jsonify(user.to_dict()), 201

    @bp.route('/api/users/<int:user_id>', methods=['PUT'])
    def update_user_api(user_id):
        """更新用户角色与启用状态：仅管理员可操作。"""
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权修改用户信息，请使用管理员账号登录。'}), 403
        user = User.query.get_or_404(user_id)
        data = request.get_json(force=True, silent=True) or {}

        new_role_raw = (data.get('role') or '').strip()
        if new_role_raw and not _is_known_user_role(new_role_raw):
            return jsonify({'error': '角色不合法，仅支持 admin / ops / viewer。'}), 400
        new_role = normalize_user_role(new_role_raw) if new_role_raw else ''
        target_role = new_role or normalize_user_role(user.role)
        target_active = bool(data['is_active']) if 'is_active' in data else bool(user.is_active)

        if (user.role == 'admin' and user.is_active) and (target_role != 'admin' or not target_active):
            other_admins = (
                User.query
                .filter(User.id != user.id, User.role == 'admin', User.is_active == True)  # noqa: E712
                .count()
            )
            if other_admins == 0:
                return jsonify({'error': '系统中至少需要保留一个启用状态的管理员账号，此操作会导致没有任何管理员，请先为其他用户设置管理员角色。'}), 400

        if new_role_raw:
            user.role = new_role
        if 'is_active' in data:
            user.is_active = target_active
        if 'display_name' in data:
            user.display_name = (str(data.get('display_name') or '')[:128]) or None
        if 'email' in data:
            user.email = (str(data.get('email') or '').strip())[:128] or None
        if 'phone' in data:
            user.phone = (str(data.get('phone') or '').strip())[:32] or None
        if 'allowed_groups' in data:
            user.allowed_groups = (str(data.get('allowed_groups') or '').strip())[:512] or None

        if 'password' in data:
            raw = (data.get('password') or '').strip()
            if raw:
                if (user.source or 'local') != 'local':
                    return jsonify({'error': '不能为 LDAP 用户设置本地密码。'}), 400
                ok_pwd, msg_pwd = check_password_policy(raw)
                if not ok_pwd:
                    return jsonify({'error': msg_pwd}), 400
                user.set_password(raw)

        db.session.commit()
        return jsonify(user.to_dict())

    @bp.route('/api/users/<int:user_id>', methods=['DELETE'])
    def delete_user_api(user_id):
        """删除用户：仅管理员可操作，且必须保留至少一个启用的管理员。"""
        if not can_edit_settings():
            return jsonify({'error': '当前登录账号无权删除用户，请使用管理员账号登录。'}), 403
        user = User.query.get_or_404(user_id)
        if user.username == super_admin_username:
            return jsonify({'error': '内置超级管理员账号不能删除。'}), 400
        if user.role == 'admin' and user.is_active:
            other_admins = (
                User.query
                .filter(User.id != user.id, User.role == 'admin', User.is_active == True)  # noqa: E712
                .count()
            )
            if other_admins == 0:
                return jsonify({'error': '系统中至少需要保留一个启用状态的管理员账号，无法删除最后一个管理员。'}), 400
        db.session.delete(user)
        db.session.commit()
        return jsonify({'ok': True})

    return bp
