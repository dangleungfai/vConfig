// 中英双语：当前语言存 localStorage key vconfig_lang，与后端 setting language 同步
(function () {
    window.__LANG = window.localStorage.getItem('vconfig_lang') || window.__INIT_LANG || 'zh';
    window.__T = {
        zh: {
            nav_dashboard: '仪表盘',
            nav_devices: '设备管理',
            nav_backup: '备份任务',
            nav_logs: '备份日志',
            nav_configs: '备份配置',
            nav_config_changes: '配置变动',
            nav_settings: '系统设置',
            settings_general: '通用设置',
            settings_backup: '备份设置',
            settings_discovery: '自动发现',
            settings_device_types: '设备类型',
            settings_auth: '认证设置',
            settings_users: '用户管理',
            settings_db_backup: '数据备份',
            label_system_language: '系统语言',
            hint_system_language: '切换界面显示语言，保存后刷新生效',
            btn_save: '保存',
            btn_search: '搜索',
            btn_logout: '退出',
            dashboard_title: '运维概览',
            dashboard_desc: '配置备份可观测与快捷操作',
            dashboard_btn_backup: '立即全量备份',
            panel_settings: '系统设置',
            panel_devices: '设备管理',
            panel_backup: '执行备份',
            panel_logs: '备份日志',
            panel_configs: '已备份配置',
            panel_config_changes: '配置变动',
            panel_device_list: '设备列表',
            toast_settings_saved: '设置已保存',
        },
        en: {
            nav_dashboard: 'Dashboard',
            nav_devices: 'Devices',
            nav_backup: 'Backup',
            nav_logs: 'Backup Logs',
            nav_configs: 'Configs',
            nav_config_changes: 'Config Changes',
            nav_settings: 'Settings',
            settings_general: 'General',
            settings_backup: 'Backup',
            settings_discovery: 'Discovery',
            settings_device_types: 'Device Types',
            settings_auth: 'Auth',
            settings_users: 'Users',
            settings_db_backup: 'DB Backup',
            label_system_language: 'System Language',
            hint_system_language: 'Switch UI language, takes effect after save',
            btn_save: 'Save',
            btn_search: 'Search',
            btn_logout: 'Logout',
            dashboard_title: 'Overview',
            dashboard_desc: 'Config backup observability & quick actions',
            dashboard_btn_backup: 'Run Full Backup',
            panel_settings: 'Settings',
            panel_devices: 'Devices',
            panel_backup: 'Backup',
            panel_logs: 'Backup Logs',
            panel_configs: 'Configs',
            panel_config_changes: 'Config Changes',
            panel_device_list: 'Device List',
            toast_settings_saved: 'Settings saved',
        },
    };
    window.t = function (key) {
        if (!key) return '';
        var lang = window.__LANG || 'zh';
        var map = window.__T[lang];
        if (map && map[key] !== undefined) return map[key];
        if (window.__T.zh && window.__T.zh[key] !== undefined) return window.__T.zh[key];
        return key;
    };
    window.applyI18n = function () {
        var lang = window.__LANG || 'zh';
        document.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN';
        document.querySelectorAll('[data-i18n]').forEach(function (el) {
            var key = el.getAttribute('data-i18n');
            if (key) el.textContent = window.t(key);
        });
        document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
            var key = el.getAttribute('data-i18n-placeholder');
            if (key) el.placeholder = window.t(key);
        });
    };
})();
