#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 vConfig 的 SQLite 数据迁移到 MariaDB。

用法（在项目根目录执行）：
  1. 先配置目标 MariaDB（创建库和用户），并设置环境变量，例如：
     export MARIADB_HOST=localhost
     export MARIADB_PORT=3306
     export MARIADB_USER=vconfig
     export MARIADB_PASSWORD=your_password
     export MARIADB_DATABASE=vconfig
  2. 可选：指定源 SQLite 文件路径（默认使用当前目录下的 vconfig.db）
     export SOURCE_SQLITE_PATH=/path/to/vconfig.db
  3. 执行迁移：
     python migrate_sqlite_to_mariadb.py

迁移完成后，将应用改为使用 MariaDB（设置 MARIADB_* 或 DATABASE_URL），
然后重启服务即可。不会推送到 GitHub，仅在本地运行。
"""

import os
import sys

# 确保项目根在 path 中
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

# 源 SQLite 路径
SOURCE_SQLITE = os.environ.get('SOURCE_SQLITE_PATH', os.path.join(BASE_DIR, 'vconfig.db'))


def _mariadb_uri():
    """从环境变量构建 MariaDB 连接 URI（与 config 中逻辑一致）。"""
    host = os.environ.get('MARIADB_HOST', 'localhost').strip()
    port = os.environ.get('MARIADB_PORT', '3306').strip()
    user = (os.environ.get('MARIADB_USER', '') or 'vconfig').strip()
    password = os.environ.get('MARIADB_PASSWORD', '')
    database = (os.environ.get('MARIADB_DATABASE', '') or 'vconfig').strip()
    if not all([host, user, database]):
        return None
    from urllib.parse import quote_plus
    pw_enc = quote_plus(password) if password else ''
    auth = f'{user}:{pw_enc}' if pw_enc else user
    return f'mysql+pymysql://{auth}@{host}:{port}/{database}'


def main():
    if not os.path.isfile(SOURCE_SQLITE):
        print('错误: 未找到 SQLite 文件: %s' % SOURCE_SQLITE)
        print('可设置环境变量 SOURCE_SQLITE_PATH 指定路径。')
        sys.exit(1)

    mariadb_url = os.environ.get('DATABASE_URL', '').strip() or _mariadb_uri()
    if not mariadb_url or 'mysql' not in mariadb_url.lower():
        print('错误: 未配置 MariaDB。请设置 DATABASE_URL 或 MARIADB_* 环境变量。')
        sys.exit(1)

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine.url import make_url

    sqlite_uri = 'sqlite:///' + SOURCE_SQLITE
    print('源 SQLite: %s' % SOURCE_SQLITE)
    print('目标 MariaDB: %s' % (mariadb_url.split('@')[-1] if '@' in mariadb_url else mariadb_url))

    engine_sqlite = create_engine(sqlite_uri)
    engine_mariadb = create_engine(mariadb_url)

    # 使用与 models 一致的表结构在 MariaDB 上建表
    from app import app
    from models import db
    with app.app_context():
        db.metadata.create_all(engine_mariadb)

    # 按外键依赖顺序复制表（无外键或依赖表先复制）
    table_order = [
        'users',
        'devices',
        'app_settings',
        'device_type_configs',
        'backup_logs',
        'backup_job_runs',
        'login_logs',
        'audit_logs',
        'config_push_logs',
        'config_change_records',
        'auto_discovery_rules',
        'auto_discovery_run_logs',
        'alert_logs',
        'auto_discovery_jobs',
    ]

    with engine_sqlite.connect() as src, engine_mariadb.connect() as dst:
        # MariaDB 导入时暂时关闭外键检查
        try:
            dst.execute(text('SET FOREIGN_KEY_CHECKS = 0'))
        except Exception:
            pass

        for table in table_order:
            try:
                rows = src.execute(text('SELECT * FROM %s' % table)).fetchall()
            except Exception as e:
                print('  跳过表 %s（源库不存在或为空）: %s' % (table, e))
                continue
            if not rows:
                print('  表 %s: 0 行' % table)
                continue
            # Row 转 dict：兼容 SQLAlchemy 1.4/2.0（_mapping 或 _asdict）
            first = rows[0]
            if hasattr(first, '_mapping'):
                keys = list(first._mapping.keys())
                def row_dict(r):
                    return dict(r._mapping)
            else:
                keys = list(first.keys())
                def row_dict(r):
                    return dict(r)
            cols = ', '.join('`%s`' % k for k in keys)
            placeholders = ', '.join([':%s' % k for k in keys])
            sql = 'INSERT IGNORE INTO `%s` (%s) VALUES (%s)' % (table, cols, placeholders)
            count = 0
            for row in rows:
                try:
                    dst.execute(text(sql), row_dict(row))
                    count += 1
                except Exception as e:
                    print('  插入失败 %s: %s' % (table, e))
            dst.commit()
            print('  表 %s: %d 行' % (table, count))

        try:
            dst.execute(text('SET FOREIGN_KEY_CHECKS = 1'))
        except Exception:
            pass

    print('迁移完成。请将应用配置为使用 MariaDB 并重启服务。')


if __name__ == '__main__':
    main()
