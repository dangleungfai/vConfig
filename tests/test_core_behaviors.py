import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import backup_service
import config
from models import normalize_user_role
from resource_indexer import parse_config_resources

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CoreBehaviorTests(unittest.TestCase):
    def test_settings_asset_routes_are_registered_once(self):
        from app import app

        rules = {}
        for rule in app.url_map.iter_rules():
            rules.setdefault(rule.rule, []).append(rule.endpoint)

        self.assertEqual(rules.get('/api/settings/logo'), ['settings_assets.settings_logo'])
        self.assertEqual(rules.get('/logo'), ['settings_assets.logo'])
        self.assertEqual(rules.get('/api/settings/db/backup'), ['settings_assets.db_backup'])
        self.assertEqual(rules.get('/api/settings/db/restore'), ['settings_assets.db_restore'])
        self.assertEqual(rules.get('/api/configs'), ['config_files.list_configs'])
        self.assertEqual(rules.get('/api/configs/devices'), ['config_files.list_configs_by_devices'])
        self.assertEqual(rules.get('/api/dashboard/config-changes'), ['config_files.dashboard_config_changes'])
        self.assertEqual(rules.get('/api/search/configs'), ['config_files.search_configs'])
        self.assertEqual(rules.get('/api/config-resources/summary'), ['config_resources.resource_summary'])
        self.assertEqual(rules.get('/api/config-resources/detail/<path:gateway>'), ['config_resources.resource_detail'])
        self.assertEqual(rules.get('/api/config-resources/search'), ['config_resources.resource_search'])
        self.assertEqual(rules.get('/api/config-resources/export'), ['config_resources.resource_export'])
        self.assertEqual(rules.get('/api/config-resources/rebuild'), ['config_resources.resource_rebuild'])
        self.assertEqual(set(rules.get('/api/users', [])), {'users.list_users_api', 'users.create_user_api'})
        self.assertEqual(set(rules.get('/api/users/<int:user_id>', [])), {'users.update_user_api', 'users.delete_user_api'})
        self.assertEqual(set(rules.get('/api/device-types', [])), {'device_types.list_device_types_api', 'device_types.create_device_type_api'})
        self.assertEqual(set(rules.get('/api/device-types/<int:type_id>', [])), {'device_types.update_device_type_api', 'device_types.delete_device_type_api'})
        self.assertEqual(rules.get('/api/settings/test-webhook'), ['settings_ops.test_webhook'])
        self.assertEqual(rules.get('/api/settings/test-email'), ['settings_ops.test_email'])
        self.assertEqual(rules.get('/api/alert-logs'), ['settings_ops.list_alert_logs'])
        self.assertEqual(rules.get('/api/settings/upload-ssl-cert'), ['settings_ops.upload_ssl_cert'])
        self.assertEqual(rules.get('/api/settings/update-ssl-cert'), ['settings_ops.update_ssl_cert'])
        self.assertEqual(rules.get('/api/settings/restart'), ['settings_ops.restart_service'])
        self.assertEqual(set(rules.get('/api/settings', [])), {'settings_core.get_settings', 'settings_core.update_settings'})
        self.assertEqual(rules.get('/api/settings/reset-defaults'), ['settings_core.reset_settings_to_defaults'])
        self.assertEqual(set(rules.get('/api/device-groups', [])), {'device_groups.list_device_groups', 'device_groups.create_device_group'})
        self.assertEqual(rules.get('/api/device-groups/<path:name>'), ['device_groups.delete_device_group'])
        self.assertEqual(rules.get('/api/logs'), ['backup_logs.list_logs'])
        self.assertEqual(rules.get('/api/devices/<int:pk>/history'), ['backup_logs.device_backup_history'])
        self.assertEqual(rules.get('/api/dashboard/export-no-backup-24h'), ['reports.export_no_backup_24h_csv'])
        self.assertEqual(rules.get('/api/devices/export'), ['reports.export_devices_csv'])
        self.assertEqual(rules.get('/'), ['pages.index'])
        self.assertEqual(rules.get('/configs/device/<prefix>/<path:hostname>'), ['pages.config_device_page'])
        self.assertEqual(rules.get('/api/footer-info'), ['pages.footer_info'])
        self.assertEqual(rules.get('/login'), ['auth.login_view'])
        self.assertEqual(rules.get('/api/login'), ['auth.api_login'])
        self.assertEqual(rules.get('/api/ldap/test'), ['auth.api_ldap_test'])
        self.assertEqual(rules.get('/logout'), ['auth.logout_view'])
        self.assertEqual(set(rules.get('/api/devices', [])), {'device_inventory.list_devices', 'device_inventory.add_device'})
        self.assertEqual(rules.get('/api/devices/<int:pk>'), ['device_inventory.device_detail'])
        self.assertEqual(rules.get('/api/devices/batch-delete'), ['device_inventory.batch_delete_devices'])
        self.assertEqual(rules.get('/api/devices/delete-all'), ['device_inventory.delete_all_devices'])
        self.assertEqual(rules.get('/api/devices/batch-update'), ['device_inventory.batch_update_devices'])
        self.assertEqual(rules.get('/api/devices/sites'), ['device_inventory.list_sites'])
        self.assertEqual(rules.get('/api/devices/import'), ['device_inventory.import_devices'])
        self.assertEqual(rules.get('/api/devices/discover'), ['device_inventory.discover_devices'])

    def test_templates_and_blueprints_do_not_reference_old_endpoint_names(self):
        checked_files = list((PROJECT_ROOT / 'templates').glob('*.html'))
        checked_files.extend((PROJECT_ROOT / 'blueprints').glob('*.py'))
        forbidden = ("url_for('logout_view'", 'url_for("logout_view"', "url_for('index'", 'url_for("index"')
        for path in checked_files:
            text = path.read_text(encoding='utf-8')
            for pattern in forbidden:
                self.assertNotIn(pattern, text, f'{pattern} remains in {path}')

    def test_user_role_normalization_keeps_current_roles_and_maps_legacy_values(self):
        self.assertEqual(normalize_user_role('admin'), 'admin')
        self.assertEqual(normalize_user_role('ops'), 'ops')
        self.assertEqual(normalize_user_role('viewer'), 'viewer')
        self.assertEqual(normalize_user_role('operator'), 'ops')
        self.assertEqual(normalize_user_role('readonly'), 'viewer')
        self.assertEqual(normalize_user_role('unknown'), 'viewer')

    def test_database_uri_rejects_non_mysql_urls(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'sqlite:///vconfig.db'}, clear=False):
            with self.assertRaises(RuntimeError):
                config._database_uri()

    def test_database_uri_defaults_to_mariadb_connection(self):
        env = {
            'DATABASE_URL': '',
            'MARIADB_HOST': '',
            'MARIADB_PORT': '',
            'MARIADB_USER': '',
            'MARIADB_PASSWORD': '',
            'MARIADB_DATABASE': '',
        }
        with patch.dict(os.environ, env, clear=False):
            for key in env:
                os.environ.pop(key, None)
            self.assertEqual(
                config._database_uri(),
                'mysql+pymysql://vconfig:vconfig@localhost:3306/vconfig',
            )

    def test_parse_config_resources_extracts_interface_resource_fields(self):
        text = '''
interface GigabitEthernet0/0/1.100
 description 9809-CUST-A Example Customer
 encapsulation dot1Q 100
 ip vrf forwarding VRF-A
 ip address 10.0.0.1 255.255.255.248
 ip address 10.0.0.2 255.255.255.248 secondary
 bandwidth 100000
!
router bgp 9809
 address-family ipv4 vrf VRF-A
  neighbor 10.0.0.6 remote-as 64510
!
'''
        rows = parse_config_resources(text, gateway='pe-a', device_profile='Cisco')

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['gateway'], 'pe-a')
        self.assertEqual(row['device_profile'], 'Cisco')
        self.assertEqual(row['interface_name'], 'GigabitEthernet0/0/1.100')
        self.assertEqual(row['interface_description'], '9809-CUST-A Example Customer')
        self.assertEqual(row['vrf_name'], 'VRF-A')
        self.assertEqual(row['pe_address'], '10.0.0.1')
        self.assertEqual(row['secondary_ip'], '10.0.0.2')
        self.assertEqual(row['vlan_id'], '100')
        self.assertEqual(row['bandwidth'], '100000')
        self.assertEqual(row['remote_as'], '64510')
        self.assertEqual(row['customer_info'], 'Example Customer')

    def test_run_backup_task_honors_max_workers(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_backup(ip, hostname, dev_type, username, password, store_path, log_callback, *args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.05)
                log_callback(ip, hostname, dev_type, 'OK', None, 0, store_path)
            finally:
                with lock:
                    active -= 1

        devices = [
            ('192.0.2.%d' % i, 'device-%d' % i, 'Cisco', 'u', 'p', 'SSH', 22, 23)
            for i in range(4)
        ]
        logs = []

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(backup_service, '_backup_via_ssh', side_effect=fake_backup):
                backup_service.run_backup_task(
                    devices,
                    tmpdir,
                    'u',
                    'p',
                    '',
                    lambda *args: logs.append(args),
                    default_connection_type='SSH',
                    max_workers=4,
                )

        self.assertEqual(len(logs), 4)
        self.assertGreaterEqual(peak, 2)


if __name__ == '__main__':
    unittest.main()
