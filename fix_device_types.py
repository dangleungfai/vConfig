#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性修复设备类型：
- CISCO/JUNIPER/HUAWEI/ROS -> Cisco/Juniper/Huawei/RouterOS
"""

from sqlalchemy import text

from app import app, db, Device, BackupLog  # noqa: F401  引入以确保模型已加载


def main():
    with app.app_context():
        try:
            # 使用 db.session 执行并统一提交，兼容 SQLAlchemy 新版本
            # devices 表
            db.session.execute(text("UPDATE devices SET device_type = 'Cisco'   WHERE device_type = 'CISCO'"))
            db.session.execute(text("UPDATE devices SET device_type = 'Juniper' WHERE device_type = 'JUNIPER'"))
            db.session.execute(text("UPDATE devices SET device_type = 'Huawei'  WHERE device_type = 'HUAWEI'"))
            db.session.execute(text("UPDATE devices SET device_type = 'RouterOS' WHERE device_type = 'ROS'"))
            # backup_logs 表
            db.session.execute(text("UPDATE backup_logs SET device_type = 'Cisco'   WHERE device_type = 'CISCO'"))
            db.session.execute(text("UPDATE backup_logs SET device_type = 'Juniper' WHERE device_type = 'JUNIPER'"))
            db.session.execute(text("UPDATE backup_logs SET device_type = 'Huawei'  WHERE device_type = 'HUAWEI'"))
            db.session.execute(text("UPDATE backup_logs SET device_type = 'RouterOS' WHERE device_type = 'ROS'"))
            db.session.commit()
            print(u'设备类型字段已批量修正为 Cisco/Juniper/Huawei。')
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            print(u'修正设备类型失败: {0}'.format(e))


if __name__ == '__main__':
    main()

