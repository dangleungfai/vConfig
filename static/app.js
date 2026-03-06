// 配置备份 Web 管理 - 前端逻辑
const API = '/api';
const SETTINGS_DRAFT_KEY = 'vconfig_settings_draft';

function readSettingsDraft() {
    try {
        const s = window.localStorage.getItem(SETTINGS_DRAFT_KEY);
        return s ? JSON.parse(s) : {};
    } catch (_) {
        return {};
    }
}

function writeSettingsDraft(partial) {
    try {
        const cur = readSettingsDraft();
        const next = Object.assign({}, cur, partial || {});
        window.localStorage.setItem(SETTINGS_DRAFT_KEY, JSON.stringify(next));
    } catch (_) {
        // 忽略本地存储错误（例如隐私模式）
    }
}

// 设备类型下拉选项（从后端动态加载）
async function refreshDeviceTypeOptions() {
    const sel = document.getElementById('device-type');
    if (!sel) return;
    try {
        const res = await fetch(`${API}/device-types`);
        if (!res.ok) throw new Error('加载设备类型失败');
        const data = await res.json();
        const items = Array.isArray(data.items) ? data.items : [];
        if (!items.length) return;
        sel.innerHTML = items.map(it => {
            const code = String(it.type_code || '').trim();
            const label = String(it.display_name || code || '未知类型');
            return `<option value="${code}">${label}</option>`;
        }).join('');
        window.__deviceTypesLoaded = true;
    } catch (e) {
        // 出错时保持原有静态选项，避免影响现有功能
        console.warn('refreshDeviceTypeOptions failed', e);
    }
}

// 设备类型管理（系统设置 -> 设备类型）
let _deviceTypeCache = null;
let _canEditDeviceTypes = true;
function renderDeviceTypeRows(items, canEdit) {
    if (!items.length) return '';
    return items.map(it => {
        const enabled = it.enabled ? '已启用' : '已禁用';
        const enabledCls = it.enabled ? 'status-ok' : 'status-fail';
        const driverLabel = it.driver_type === 'builtin'
            ? '内置驱动'
            : (it.driver_type === 'custom' ? '自定义驱动' : '通用驱动');
        const code = String(it.type_code || '').trim();
        const isBuiltin = ['Cisco', 'Juniper', 'Huawei', 'H3C', 'RouterOS'].includes(code);
        const deleteBtn = isBuiltin
            ? '<button type="button" class="btn btn-secondary btn-sm" disabled title="内置类型不可删除">删除</button>'
            : '<button type="button" class="btn btn-secondary btn-sm btn-danger-soft" data-device-type-delete>删除</button>';
        const actionCells = canEdit
            ? `<button type="button" class="btn btn-secondary btn-sm" data-device-type-edit>编辑</button>
                <button type="button" class="btn btn-secondary btn-sm" data-device-type-toggle>${it.enabled ? '禁用' : '启用'}</button>
                ${deleteBtn}`
            : '—';
        return `
            <tr data-id="${it.id}">
                <td>${escapeHtml(code)}</td>
                <td>${escapeHtml(it.display_name || '')}</td>
                <td>${escapeHtml(driverLabel)}</td>
                <td class="${enabledCls}">${enabled}</td>
                <td>${it.sort_order ?? 0}</td>
                <td>${actionCells}</td>
            </tr>
        `;
    }).join('');
}
async function loadDeviceTypes(force) {
    const tbody = document.getElementById('device-type-list');
    const hint = document.getElementById('device-type-hint');
    if (!tbody) return;

    // 若已有缓存且未强制刷新，直接渲染缓存数据，避免每次切换都重新请求
    if (!force && Array.isArray(_deviceTypeCache)) {
        const items = _deviceTypeCache;
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="6">暂无设备类型，请点击「新增类型」添加。</td></tr>';
            if (hint) hint.style.display = '';
        } else {
            tbody.innerHTML = renderDeviceTypeRows(items, _canEditDeviceTypes);
            if (hint) hint.style.display = 'none';
        }
        return;
    }

    try {
        const res = await fetch(`${API}/device-types?include_disabled=1`);
        const data = await res.json();
        const items = Array.isArray(data.items) ? data.items : [];
        _deviceTypeCache = items;
        _canEditDeviceTypes = (data.can_edit_settings === undefined) ? true : !!data.can_edit_settings;
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="6">暂无设备类型，请点击「新增类型」添加。</td></tr>';
            if (hint) hint.style.display = '';
            return;
        }
        tbody.innerHTML = renderDeviceTypeRows(items, _canEditDeviceTypes);
        if (hint) hint.style.display = 'none';
    } catch (e) {
        console.warn('loadDeviceTypes failed', e);
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="6">加载失败，请稍后重试。</td></tr>';
        }
        if (hint) hint.style.display = '';
    }
}

function openDeviceTypeModal(item) {
    const modal = document.getElementById('modal-device-type');
    if (!modal) return;
    const titleEl = document.getElementById('modal-device-type-title');
    const codeEl = document.getElementById('device-type-code');
    const nameEl = document.getElementById('device-type-display-name');
    const driverTypeEl = document.getElementById('device-type-driver-type');
    const driverModuleEl = document.getElementById('device-type-driver-module');
    const driverModuleRow = document.getElementById('device-type-driver-module-row');
    const sortEl = document.getElementById('device-type-sort-order');
    const enabledEl = document.getElementById('device-type-enabled');
    const initEl = document.getElementById('device-type-init-commands');
    const backupCmdEl = document.getElementById('device-type-backup-command');
    const promptEl = document.getElementById('device-type-prompt');
    const loginPromptEl = document.getElementById('device-type-login-prompt');
    const passwordPromptEl = document.getElementById('device-type-password-prompt');
    if (!titleEl || !codeEl || !nameEl || !driverTypeEl || !sortEl || !enabledEl || !backupCmdEl) return;

    const bc = (item && item.backup_config) || {};
    const cc = (item && item.connection_config) || {};

    modal.dataset.editingId = item && item.id ? String(item.id) : '';
    if (item) {
        titleEl.textContent = '编辑设备类型';
        codeEl.value = item.type_code || '';
        codeEl.disabled = true;
        nameEl.value = item.display_name || '';
        driverTypeEl.value = item.driver_type || 'generic';
        driverModuleEl.value = item.driver_module || '';
        sortEl.value = String(item.sort_order ?? 0);
        enabledEl.checked = !!item.enabled;
        if (initEl) initEl.value = Array.isArray(bc.init_commands) ? bc.init_commands.join('\n') : '';
        backupCmdEl.value = bc.backup_command || '';
        if (promptEl) promptEl.value = bc.prompt || '';
        if (loginPromptEl) loginPromptEl.value = cc.login_prompt || '';
        if (passwordPromptEl) passwordPromptEl.value = cc.password_prompt || '';
    } else {
        titleEl.textContent = '新增设备类型';
        codeEl.value = '';
        codeEl.disabled = false;
        nameEl.value = '';
        driverTypeEl.value = 'generic';
        driverModuleEl.value = '';
        const maxOrder = Array.isArray(_deviceTypeCache) && _deviceTypeCache.length
            ? Math.max(..._deviceTypeCache.map(t => Number(t.sort_order) || 0))
            : 0;
        sortEl.value = String(maxOrder + 1);
        enabledEl.checked = true;
        if (initEl) initEl.value = '';
        backupCmdEl.value = '';
        if (promptEl) promptEl.value = '';
        if (loginPromptEl) loginPromptEl.value = '';
        if (passwordPromptEl) passwordPromptEl.value = '';
    }
    if (driverModuleRow) {
        driverModuleRow.style.display = driverTypeEl.value === 'custom' ? '' : 'none';
    }
    driverTypeEl.onchange = () => {
        if (driverModuleRow) {
            driverModuleRow.style.display = driverTypeEl.value === 'custom' ? '' : 'none';
        }
    };
    modal.classList.add('show');
}

async function saveDeviceTypeFromModal() {
    const modal = document.getElementById('modal-device-type');
    if (!modal) return;
    const editingId = modal.dataset.editingId || '';
    const codeEl = document.getElementById('device-type-code');
    const nameEl = document.getElementById('device-type-display-name');
    const driverTypeEl = document.getElementById('device-type-driver-type');
    const driverModuleEl = document.getElementById('device-type-driver-module');
    const sortEl = document.getElementById('device-type-sort-order');
    const enabledEl = document.getElementById('device-type-enabled');
    const initEl = document.getElementById('device-type-init-commands');
    const backupCmdEl = document.getElementById('device-type-backup-command');
    const promptEl = document.getElementById('device-type-prompt');
    const loginPromptEl = document.getElementById('device-type-login-prompt');
    const passwordPromptEl = document.getElementById('device-type-password-prompt');
    if (!codeEl || !nameEl || !driverTypeEl || !sortEl || !enabledEl || !backupCmdEl) return;
    const type_code = (codeEl.value || '').trim();
    const display_name = (nameEl.value || '').trim();
    const driver_type = driverTypeEl.value || 'generic';
    const driver_module = (driverModuleEl.value || '').trim() || null;
    let sort_order = parseInt(sortEl.value, 10);
    if (isNaN(sort_order)) sort_order = 0;
    const enabled = !!enabledEl.checked;

    const backup_config = {};
    const initRaw = initEl ? initEl.value : '';
    if (initRaw && initRaw.trim()) {
        backup_config.init_commands = initRaw.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    }
    const backupCmd = backupCmdEl.value.trim();
    if (backupCmd) backup_config.backup_command = backupCmd;
    const promptVal = promptEl ? promptEl.value : '';
    if (promptVal && promptVal.trim()) backup_config.prompt = promptVal;

    const connection_config = {};
    const loginPrompt = loginPromptEl ? loginPromptEl.value.trim() : '';
    if (loginPrompt) connection_config.login_prompt = loginPrompt;
    const passwordPrompt = passwordPromptEl ? passwordPromptEl.value.trim() : '';
    if (passwordPrompt) connection_config.password_prompt = passwordPrompt;
    if (!type_code) {
        alert('请填写类型代码。');
        return;
    }
    if (!display_name) {
        alert('请填写显示名称。');
        return;
    }
    const payload = {
        type_code,
        display_name,
        driver_type,
        driver_module,
        sort_order,
        enabled,
    };
    if (Object.keys(backup_config).length) payload.backup_config = backup_config;
    if (Object.keys(connection_config).length) payload.connection_config = connection_config;
    const url = editingId ? `${API}/device-types/${editingId}` : `${API}/device-types`;
    const method = editingId ? 'PUT' : 'POST';
    const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        alert(data.error || '保存失败');
        return;
    }
    modal.classList.remove('show');
    await loadDeviceTypes(true);
    // 刷新设备编辑弹窗下拉
    refreshDeviceTypeOptions().catch(() => {});
}
function debounce(fn, ms) {
    let t;
    return function () {
        clearTimeout(t);
        t = setTimeout(() => fn.apply(this, arguments), ms);
    };
}
function formatInTimezone(iso, tz) {
    if (!iso) return '';
    // 无时区后缀的 ISO 视为 UTC，避免被浏览器当本地时间解析导致慢 8 小时
    let s = String(iso).trim();
    if (s && !/Z|[+-]\d{2}:?\d{2}$/.test(s)) s = s + 'Z';
    const d = new Date(s);
    const tzName = (tz || 'Asia/Shanghai').trim();
    try {
        return d.toLocaleString('zh-CN', { timeZone: tzName, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    } catch (_) {
        try { return d.toLocaleString('zh-CN', { timeZone: tzName }); } catch (e) { return d.toLocaleString('zh-CN'); }
    }
}

function showTab(name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    const tab = document.querySelector(`[data-tab="${name}"]`);
    const panel = document.getElementById(`panel-${name}`);
    if (tab) tab.classList.add('active');
    if (panel) panel.classList.add('active');
    try { sessionStorage.setItem('vconfig_tab', name); } catch (e) {}
    // 切换 Tab 时同步地址栏 hash，避免从 #config-device/... 进入后点击其他菜单时 URL 不更新
    if (name !== 'config-device') {
        location.hash = name === 'dashboard' ? '' : name;
    }
    if (name === 'dashboard') loadDashboard();
    if (name === 'devices') { loadDevices(); refreshDeviceTypeOptions().catch(() => {}); }
    if (name === 'backup') loadBackupStatus();
    if (name === 'logs') loadLogs();
    if (name === 'configs') loadConfigs();
    if (name === 'config-changes') loadConfigChangesPage();
    if (name === 'settings') {
        loadSettings();
        const savedSection = (function() {
            try { return sessionStorage.getItem('vconfig_settings_section') || 'general'; }
            catch (e) { return 'general'; }
        })();
        showSettingsSection(savedSection);
        if (savedSection === 'users') {
            loadUsers();
        } else if (savedSection === 'device-types') {
            loadDeviceTypes(false);
        }
    }
}

document.body.addEventListener('click', e => {
    const t = e.target.closest('.tab');
    if (t && t.dataset.tab) {
        e.preventDefault();
        const panel = document.getElementById('panel-' + t.dataset.tab);
        if (!panel) {
            try { sessionStorage.setItem('vconfig_tab', t.dataset.tab); } catch (_) {}
            window.location.href = '/';
            return;
        }
        showTab(t.dataset.tab);
    }
});

function toast(message, type) {
    type = type || 'info';
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3500);
}

// 仪表盘
async function loadDashboard() {
    if (!document.getElementById('panel-dashboard')) return; // 非首页（如配置对比页）无仪表盘 DOM，直接返回
    const res = await fetch(`${API}/dashboard`);
    const d = await res.json();
    document.getElementById('stat-total').textContent = d.total_devices ?? '--';
    document.getElementById('stat-enabled').textContent = d.enabled_devices ?? '--';
    document.getElementById('stat-today-ok').textContent = d.today_ok ?? '--';
    document.getElementById('stat-today-fail').textContent = d.today_fail ?? '--';
    document.getElementById('stat-no-backup').textContent = d.no_backup_24h ?? '--';
    const slaEl = document.getElementById('stat-sla-24h');
    if (slaEl) slaEl.textContent = (d.sla_24h_ratio != null ? d.sla_24h_ratio + '%' : '--');

    try {
        window.__dashboard_today_ok = d.today_ok || 0;
    } catch (_) {}
    if (d.settings_summary) {
        if (d.settings_summary.device_per_page_default) {
            devicePerPage = parseInt(d.settings_summary.device_per_page_default, 10) || 50;
            const sel = document.getElementById('device-per-page');
            if (sel) sel.value = String(devicePerPage);
        }
        if (d.settings_summary.log_per_page_default) {
            logPerPage = parseInt(d.settings_summary.log_per_page_default, 10) || 50;
            const logSel = document.getElementById('log-per-page');
            if (logSel) logSel.value = String(logPerPage);
        }
    }

    const tz = d.timezone || 'Asia/Shanghai';
    const lastBackupEl = document.getElementById('dashboard-last-backup');
    if (lastBackupEl) {
        const t = d.last_backup_time;
        lastBackupEl.textContent = t ? '最近成功备份: ' + formatInTimezone(t, tz) : '';
        lastBackupEl.style.color = 'var(--text-muted)';
        lastBackupEl.style.fontSize = '0.85rem';
        lastBackupEl.style.marginBottom = '1rem';
    }

    const trend = d.trend || [];
    const chartEl = document.getElementById('dashboard-trend-chart');
    if (chartEl) {
        const barMaxH = 120;
        const bars = trend.map(day => {
            const okCount = day.ok || 0;
            const failCount = day.fail || 0;
            const maxDay = Math.max(1, okCount, failCount);
            const okH = Math.max(2, (okCount / maxDay) * barMaxH);
            const failH = Math.max(2, (failCount / maxDay) * barMaxH);
            return `
                <div class="chart-bar-wrap">
                    <div class="chart-bar-group">
                        <div class="chart-bar-col">
                            <span class="chart-bar-count chart-bar-count-ok">${okCount}</span>
                            <span class="chart-bar chart-bar-ok" style="height:${okH}px" title="成功 ${okCount}"></span>
                        </div>
                        <div class="chart-bar-col">
                            <span class="chart-bar-count chart-bar-count-fail">${failCount}</span>
                            <span class="chart-bar chart-bar-fail" style="height:${failH}px" title="失败 ${failCount}"></span>
                        </div>
                    </div>
                    <span class="chart-label">${day.date}</span>
                </div>`;
        }).join('');
        chartEl.innerHTML = `<div class="chart-bars-row">${bars}</div>
            <div class="chart-legend">
                <span class="chart-legend-item"><span class="chart-legend-dot ok"></span>成功</span>
                <span class="chart-legend-item"><span class="chart-legend-dot fail"></span>失败</span>
            </div>`;
    }

    const runEl = document.getElementById('dashboard-running');
    if (runEl) runEl.style.display = d.backup_running ? 'block' : 'none';
    const hintEl = document.getElementById('dashboard-hint');
    if (hintEl) {
        const n = d.no_backup_24h || 0;
        hintEl.innerHTML = n > 0
            ? `有 <strong>${n}</strong> 台已启用设备 24h 内未成功备份，可 <a href="${API}/dashboard/export-no-backup-24h" target="_blank" rel="noopener">导出未备份列表</a>，或检查 <a href="#" class="tab" data-tab="logs">备份日志</a> 或执行全量备份。`
            : '';
        hintEl.style.display = n > 0 ? 'block' : 'none';
    }

    // 按类型 24h 成功率
    const successByTypeEl = document.getElementById('dashboard-success-by-type');
    if (successByTypeEl) {
        const rows = d.success_rate_by_type || [];
        if (!rows.length) successByTypeEl.innerHTML = '<div class="hint">暂无数据</div>';
        else {
            successByTypeEl.innerHTML = `
                <div class="recent-header-row">
                    <span class="recent-header-cell">类型</span>
                    <span class="recent-header-cell">已备份/总数</span>
                    <span class="recent-header-cell">成功率</span>
                </div>
                ${rows.map(r => `
                    <div class="recent-item">
                        <span>${escapeHtml(r.device_type || '')}</span>
                        <span>${r.ok_24h}/${r.total}</span>
                        <span class="${r.rate_pct >= 95 ? 'status-ok' : r.rate_pct >= 80 ? 'status-warn' : 'status-fail'}">${r.rate_pct}%</span>
                    </div>
                `).join('')}
            `;
        }
    }

    // 按分组 24h 成功率
    const successByGroupEl = document.getElementById('dashboard-success-by-group');
    if (successByGroupEl) {
        const rows = d.success_rate_by_group || [];
        if (!rows.length) successByGroupEl.innerHTML = '<div class="hint">暂无数据</div>';
        else {
            successByGroupEl.innerHTML = `
                <div class="recent-header-row">
                    <span class="recent-header-cell">分组</span>
                    <span class="recent-header-cell">已备份/总数</span>
                    <span class="recent-header-cell">成功率</span>
                </div>
                ${rows.map(r => `
                    <div class="recent-item">
                        <span>${escapeHtml(r.group || '')}</span>
                        <span>${r.ok_24h}/${r.total}</span>
                        <span class="${r.rate_pct >= 95 ? 'status-ok' : r.rate_pct >= 80 ? 'status-warn' : 'status-fail'}">${r.rate_pct}%</span>
                    </div>
                `).join('')}
            `;
        }
    }

    const btn = document.getElementById('dashboard-btn-backup');
    if (btn) {
        const canRun = (d.can_run_backup === undefined) ? true : !!d.can_run_backup;
        btn.disabled = !!d.backup_running || !canRun;
        if (d.backup_running) {
            btn.textContent = '备份进行中...';
        } else if (!canRun) {
            btn.textContent = '无权执行';
        } else {
            btn.textContent = '立即全量备份';
        }
    }

    // 最近 7 天备份失败的设备列表（格式与最近活动一致）
    const recentFailEl = document.getElementById('dashboard-recent-fail');
    const recentFailEmpty = document.getElementById('dashboard-recent-fail-empty');
    if (recentFailEl) {
        const list = d.recent_fail_devices || [];
        if (recentFailEmpty) recentFailEmpty.style.display = list.length ? 'none' : 'block';
        if (!list.length) {
            recentFailEl.innerHTML = '';
        } else {
            const rows = list.map(item => {
                const time = item.created_at ? formatInTimezone(item.created_at, tz) : '';
                return `
                <div class="recent-item">
                    <span>${escapeHtml(item.hostname || '')}</span>
                    <span>${escapeHtml(item.ip || '')}</span>
                    <span class="status-fail" title="${escapeHtml(item.status_full || item.status || '-')}">${escapeHtml(item.status || 'Fail')}</span>
                    <span>${time}</span>
                </div>
            `;
            }).join('');
            recentFailEl.innerHTML = `
                <div class="recent-header-row">
                    <span class="recent-header-cell">主机名</span>
                    <span class="recent-header-cell">管理 IP</span>
                    <span class="recent-header-cell">备份状态</span>
                    <span class="recent-header-cell">时间</span>
                </div>
                ${rows}
            `;
        }
    }

    // 设备类型分布
    const typeEl = document.getElementById('dashboard-type-dist');
    if (typeEl) {
        const rows = d.type_distribution || [];
        const total = rows.reduce((sum, r) => sum + (r.count || 0), 0) || 1;
        if (!rows.length) {
            typeEl.innerHTML = '<div class="hint">暂无设备数据</div>';
        } else {
            typeEl.innerHTML = rows.map(r => {
                const count = r.count || 0;
                const pct = Math.round((count / total) * 100);
                const name = r.device_type || '未知';
                return `
                    <div class="type-row">
                        <div class="type-row-label">${escapeHtml(name)}</div>
                        <div class="type-row-bar-wrap">
                            <div class="type-row-bar" style="width:${Math.max(pct, 5)}%;"></div>
                        </div>
                        <div class="type-row-count">${count}</div>
                    </div>
                `;
            }).join('');
        }
    }

    // 系统配置概览
    const settingsEl = document.getElementById('dashboard-settings-summary');
    if (settingsEl && d.settings_summary) {
        const s = d.settings_summary;
        const freqMap = {
            'none': '手动执行',
            'hourly': '每小时（整点）',
            'twice_daily': '每12小时（0点和12点）',
            'daily': '每天凌晨02:00',
            'weekly': '每周日凌晨02:00',
            'custom': '自定义（高级）',
        };
        const freqLabel = freqMap[s.backup_frequency] || (s.backup_frequency || '手动执行');
        settingsEl.innerHTML = `
            <div class="dashboard-settings-item">
                <span class="label">当前时区</span><span class="value">${escapeHtml(s.timezone || '未设置')}</span>
            </div>
            <div class="dashboard-settings-item">
                <span class="label">默认连接方式</span><span class="value">${s.default_connection_type === 'SSH' ? 'SSH' : 'Telnet'}</span>
            </div>
            <div class="dashboard-settings-item">
                <span class="label">自动备份策略</span><span class="value">${escapeHtml(freqLabel)}</span>
            </div>
            <div class="dashboard-settings-item">
                <span class="label">LDAP 登录</span><span class="value">${s.ldap_enabled ? '已启用' : '未启用'}</span>
            </div>
        `;
    }

    // 最近登录
    const loginEl = document.getElementById('dashboard-login-logs');
    if (loginEl) {
        const logs = d.recent_logins || [];
        if (!logs.length) {
            loginEl.innerHTML = '<div class="hint">暂未记录登录信息</div>';
        } else {
            const rows = logs.map(x => {
                const time = x.created_at ? formatInTimezone(x.created_at, tz) : '';
                const src = (x.auth_source === 'ldap') ? 'LDAP' : '本地账号';
                return `
                    <div class="login-item">
                        <span class="login-item-username">${escapeHtml(x.username || '')}</span>
                        <span class="login-item-ip">${escapeHtml(x.source_ip || '')}</span>
                        <span class="login-item-method">${escapeHtml(src)}</span>
                        <span class="login-item-time">${escapeHtml(time)}</span>
                    </div>
                `;
            }).join('');
            loginEl.innerHTML = `
                <div class="login-header-row">
                    <span class="login-header-cell">用户名</span>
                    <span class="login-header-cell">登录 IP</span>
                    <span class="login-header-cell">登录方式</span>
                    <span class="login-header-cell">时间</span>
                </div>
                ${rows}
            `;
        }
    }

    // 安全审计（最近操作）
    const auditEl = document.getElementById('dashboard-audit-logs');
    if (auditEl) {
        const logs = d.recent_audits || [];
        if (!logs.length) {
            auditEl.innerHTML = '<div class="hint">暂无审计记录</div>';
        } else {
            const rows = logs.map(x => {
                const time = x.created_at ? formatInTimezone(x.created_at, tz) : '';
                const src = (x.auth_source === 'ldap') ? 'LDAP' : '本地账号';
                const action = x.action || '';
                const target = (x.resource_type || '') + (x.resource_id ? ':' + x.resource_id : '');
                return `
                    <div class="audit-item">
                        <span class="audit-user">${escapeHtml(x.username || '')}</span>
                        <span class="audit-action">${escapeHtml(action)}</span>
                        <span class="audit-target">${escapeHtml(target || '')}</span>
                        <span class="audit-time">${escapeHtml(time)}</span>
                    </div>
                `;
            }).join('');
            auditEl.innerHTML = `
                <div class="audit-header-row">
                    <span class="audit-header-cell">用户</span>
                    <span class="audit-header-cell">操作</span>
                    <span class="audit-header-cell">对象</span>
                    <span class="audit-header-cell">时间</span>
                </div>
                ${rows}
            `;
        }
    }

    // 配置变动最多设备（独立接口，与 dashboard 并行或紧随加载）
    loadDashboardConfigChanges();
}

async function loadDashboardConfigChanges() {
    const loadingEl = document.getElementById('dashboard-config-changes-loading');
    const listEl = document.getElementById('dashboard-config-changes-list');
    const emptyEl = document.getElementById('dashboard-config-changes-empty');
    if (!listEl) return;
    if (loadingEl) loadingEl.classList.remove('hidden');
    if (emptyEl) {
        emptyEl.style.display = 'none';
        emptyEl.textContent = '暂无配置变动数据（需至少有两份备份且存在差异）。';
    }
    try {
        const res = await fetch(`${API}/dashboard/config-changes?limit=15`);
        const data = res.ok ? await res.json() : { devices: [] };
        const devices = data.devices || [];
        if (!devices.length) {
            if (emptyEl) {
                emptyEl.textContent = res.ok
                    ? '暂无配置变动数据（需至少有两份备份且存在差异）。'
                    : '加载失败，请稍后重试。';
                emptyEl.style.display = 'block';
            }
            listEl.innerHTML = '';
            return;
        }
        // 每台设备按变动类型拆成多行：增加、删除（仅当数量 > 2 时显示）
        const rows = [];
        devices.forEach(d => {
            const added = d.added_count || 0;
            const removed = d.removed_count || 0;
            if (added > 2) rows.push({ ...d, change_type: '增加', count: added });
            if (removed > 2) rows.push({ ...d, change_type: '删除', count: removed });
        });
        rows.sort((a, b) => (b.count - a.count));
        if (!rows.length) {
            if (emptyEl) {
                emptyEl.textContent = '暂无配置变动数据（需至少有两份备份且存在差异；仅显示变动 > 2 条）。';
                emptyEl.style.display = 'block';
            }
            listEl.innerHTML = '';
            return;
        }
        if (emptyEl) emptyEl.style.display = 'none';
        const items = rows.map(d => {
            const hostname = escapeHtml(d.hostname || '');
            const ip = escapeHtml(d.ip || '');
            const deviceType = escapeHtml(d.device_type || '');
            const changeType = d.change_type === '删除' ? '删除' : '增加';
            const count = d.count || 0;
            const prefix = escapeHtml(d.prefix || '');
            const host = escapeHtml(d.hostname || '');
            return `
                <div class="recent-item" data-prefix="${prefix}" data-hostname="${host}">
                    <span>${hostname}</span>
                    <span>${ip}</span>
                    <span>${deviceType}</span>
                    <span>${changeType}</span>
                    <span><button type="button" class="btn-link config-changes-count-link">${count}</button></span>
                </div>
            `;
        }).join('');
        listEl.innerHTML = `
            <div class="recent-header-row">
                <span class="recent-header-cell">设备名称</span>
                <span class="recent-header-cell">管理 IP</span>
                <span class="recent-header-cell">设备类型</span>
                <span class="recent-header-cell">变动类型</span>
                <span class="recent-header-cell">命令数量</span>
            </div>
            ${items}
        `;
        bindConfigChangesCountClick();
    } catch (e) {
        if (emptyEl) {
            emptyEl.textContent = '加载失败，请稍后重试。';
            emptyEl.style.display = 'block';
        }
        listEl.innerHTML = '';
    } finally {
        if (loadingEl) loadingEl.classList.add('hidden');
    }
}

// 从首页卡片点击命令数量跳转时，打开指定设备明细（由 onConfigChangesCountClick 设置）
let _configChangesOpenDevice = null;

// 配置变动汇总页面：列表加载
async function loadConfigChangesPage() {
    const listEl = document.getElementById('config-changes-page-list');
    const emptyEl = document.getElementById('config-changes-page-empty');
    const loadingEl = document.getElementById('config-changes-page-loading');
    if (!listEl) return;
    if (loadingEl) loadingEl.classList.remove('hidden');
    if (emptyEl) {
        emptyEl.style.display = 'none';
        emptyEl.textContent = '暂无配置变动数据（需至少有两份备份且存在差异）。';
    }
    try {
        const res = await fetch(`${API}/dashboard/config-changes?limit=1000`);
        const data = res.ok ? await res.json() : { devices: [] };
        const devices = data.devices || [];
        if (!devices.length) {
            if (emptyEl) {
                emptyEl.textContent = res.ok
                    ? '暂无配置变动数据（需至少有两份备份且存在差异）。'
                    : '加载失败，请稍后重试。';
                emptyEl.style.display = 'block';
            }
            listEl.innerHTML = '';
            return;
        }
        if (emptyEl) emptyEl.style.display = 'none';
        // 左侧列表只需要每台有变更的设备一次，按变更行数降序排序
        const sortedDevices = [...devices].sort((a, b) => {
            const ca = a.change_count || (a.added_count || 0) + (a.removed_count || 0);
            const cb = b.change_count || (b.added_count || 0) + (b.removed_count || 0);
            return cb - ca;
        });
        const items = sortedDevices.map(d => {
            const hostname = escapeHtml(d.hostname || '');
            const ip = escapeHtml(d.ip || '');
            const prefix = escapeHtml(d.prefix || '');
            const host = escapeHtml(d.hostname || '');
            return `
                <div class="recent-item" data-prefix="${prefix}" data-hostname="${host}">
                    <span>${hostname}</span>
                    <span class="config-changes-ip">${ip}</span>
                </div>
            `;
        }).join('');
        listEl.innerHTML = items;
        // 若从首页卡片点击命令数量跳转而来，则选中并打开该设备明细；否则默认选中第一台
        const openDevice = _configChangesOpenDevice;
        _configChangesOpenDevice = null;
        let targetRow = null;
        if (openDevice && openDevice.prefix && openDevice.hostname) {
            listEl.querySelectorAll('.recent-item').forEach(r => {
                if (r.getAttribute('data-prefix') === openDevice.prefix && r.getAttribute('data-hostname') === openDevice.hostname) targetRow = r;
            });
        }
        if (!targetRow) targetRow = listEl.querySelector('.recent-item');
        if (targetRow) {
            const firstPrefix = targetRow.getAttribute('data-prefix');
            const firstHostname = targetRow.getAttribute('data-hostname');
            if (firstPrefix && firstHostname) {
                if (document.getElementById('config-changes-detail-body')) {
                    openConfigDiffInline(firstPrefix, firstHostname, targetRow);
                } else {
                    openConfigDiffModal(firstPrefix, firstHostname);
                }
            }
        }
        // 左侧列表整行可点击，展示右侧明细
        listEl.onclick = (e) => {
            const row = e.target.closest('.recent-item');
            if (!row) return;
            const prefix = row.getAttribute('data-prefix');
            const hostname = row.getAttribute('data-hostname');
            if (!prefix || !hostname) return;
            if (document.getElementById('config-changes-detail-body')) {
                openConfigDiffInline(prefix, hostname, row);
            } else {
                openConfigDiffModal(prefix, hostname);
            }
        };
    } catch (e) {
        if (emptyEl) {
            emptyEl.textContent = '加载失败，请稍后重试。';
            emptyEl.style.display = 'block';
        }
        listEl.innerHTML = '';
    } finally {
        if (loadingEl) loadingEl.classList.add('hidden');
    }
}

function bindConfigChangesCountClick() {
    const listEl = document.getElementById('dashboard-config-changes-list');
    if (!listEl) return;
    listEl.removeEventListener('click', onConfigChangesCountClick);
    listEl.addEventListener('click', onConfigChangesCountClick);
}

function onConfigChangesCountClick(e) {
    const btn = e.target.closest('.config-changes-count-link');
    if (!btn) return;
    const row = btn.closest('.recent-item');
    if (!row) return;
    const prefix = row.getAttribute('data-prefix');
    const hostname = row.getAttribute('data-hostname');
    if (!prefix || !hostname) return;
    // 先跳转到配置变动页，并标记要打开该设备明细（loadConfigChangesPage 会处理）
    _configChangesOpenDevice = { prefix, hostname };
    location.hash = 'config-changes';
    showTab('config-changes');
}

async function openConfigDiffModal(prefix, hostname) {
    const modal = document.getElementById('modal-config-diff');
    const titleEl = document.getElementById('modal-config-diff-title');
    const loadingEl = document.getElementById('modal-config-diff-loading');
    const bodyEl = document.getElementById('modal-config-diff-body');
    const addedEl = document.getElementById('modal-config-diff-added');
    const removedEl = document.getElementById('modal-config-diff-removed');
    if (!modal) return;
    if (titleEl) titleEl.textContent = (hostname || '') + ' - 命令变动';
    if (loadingEl) loadingEl.classList.remove('hidden');
    if (bodyEl) bodyEl.classList.add('hidden');
    modal.classList.add('show');
    try {
        const url = `${API}/configs/devices/${encodeURIComponent(prefix)}/${encodeURIComponent(hostname)}/diff-latest`;
        const res = await fetch(url);
        const data = await res.json();
        if (loadingEl) loadingEl.classList.add('hidden');
        if (bodyEl) bodyEl.classList.remove('hidden');
        const added = data.added || [];
        const removed = data.removed || [];
        if (addedEl) addedEl.textContent = added.length ? added.join('\n') : '（无）';
        if (removedEl) removedEl.textContent = removed.length ? removed.join('\n') : '（无）';
    } catch (e) {
        if (loadingEl) loadingEl.classList.add('hidden');
        if (bodyEl) bodyEl.classList.remove('hidden');
        if (addedEl) addedEl.textContent = '加载失败';
        if (removedEl) removedEl.textContent = '';
    }
}

async function openConfigDiffInline(prefix, hostname, row) {
    const detail = document.getElementById('config-changes-detail');
    const loadingEl = document.getElementById('config-changes-detail-loading');
    const bodyEl = document.getElementById('config-changes-detail-body');
    const addedEl = document.getElementById('config-changes-detail-added');
    const removedEl = document.getElementById('config-changes-detail-removed');
    if (!detail || !bodyEl || !addedEl || !removedEl) return;

    // 高亮当前行
    const listEl = row.parentElement;
    if (listEl) {
        listEl.querySelectorAll('.recent-item').forEach(it => it.classList.remove('config-changes-selected'));
    }
    row.classList.add('config-changes-selected');

    if (loadingEl) loadingEl.style.display = '';
    bodyEl.classList.add('hidden');

    try {
        const url = `${API}/configs/devices/${encodeURIComponent(prefix)}/${encodeURIComponent(hostname)}/diff-latest`;
        const res = await fetch(url);
        const data = await res.json();
        if (loadingEl) loadingEl.style.display = 'none';
        bodyEl.classList.remove('hidden');
        const added = data.added || [];
        const removed = data.removed || [];
        addedEl.textContent = added.length ? added.join('\n') : '（无）';
        removedEl.textContent = removed.length ? removed.join('\n') : '（无）';
    } catch (e) {
        if (loadingEl) loadingEl.style.display = 'none';
        bodyEl.classList.remove('hidden');
        addedEl.textContent = '加载失败';
        removedEl.textContent = '';
    }
}

// 配置变动右侧：一键复制全部命令
document.getElementById('btn-config-changes-copy')?.addEventListener('click', () => {
    const addedEl = document.getElementById('config-changes-detail-added');
    const removedEl = document.getElementById('config-changes-detail-removed');
    if (!addedEl || !removedEl) return;
    const addedText = addedEl.textContent || '';
    const removedText = removedEl.textContent || '';
    const allText = [addedText, removedText].filter(Boolean).join('\n\n');
    if (!allText.trim()) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(allText).then(() => {
            if (typeof toast === 'function') toast('已复制右侧所有命令', 'success');
        }).catch(() => {});
    }
});

// 只复制「删除的命令」
document.getElementById('btn-config-changes-copy-removed')?.addEventListener('click', () => {
    const removedEl = document.getElementById('config-changes-detail-removed');
    if (!removedEl) return;
    const removedText = removedEl.textContent || '';
    if (!removedText.trim()) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(removedText).then(() => {
            if (typeof toast === 'function') toast('已复制删除的命令', 'success');
        }).catch(() => {});
    }
});

document.getElementById('btn-modal-config-diff-close')?.addEventListener('click', () => {
    document.getElementById('modal-config-diff')?.classList.remove('show');
});

// 首页卡片拖拽排序（顺序持久化到 localStorage）
const DASHBOARD_CARD_ORDER_KEY = 'vconfig_dashboard_card_order';

function getDashboardCardOrder() {
    try {
        const raw = localStorage.getItem(DASHBOARD_CARD_ORDER_KEY);
        if (!raw) return null;
        const arr = JSON.parse(raw);
        return Array.isArray(arr) ? arr : null;
    } catch (e) {
        return null;
    }
}

function setDashboardCardOrder(ids) {
    try {
        localStorage.setItem(DASHBOARD_CARD_ORDER_KEY, JSON.stringify(ids));
    } catch (e) {}
}

function applyDashboardCardOrder() {
    const grid = document.getElementById('dashboard-grid');
    if (!grid) return;
    const cards = Array.from(grid.querySelectorAll('.dashboard-card[data-card-id]'));
    const defaultIds = cards.map(c => c.getAttribute('data-card-id'));
    const saved = getDashboardCardOrder();
    if (!saved || saved.length !== defaultIds.length || new Set(saved).size !== defaultIds.length) return;
    const idSet = new Set(defaultIds);
    if (saved.some(id => !idSet.has(id))) return;
    const byId = new Map(cards.map(c => [c.getAttribute('data-card-id'), c]));
    saved.forEach(id => {
        const el = byId.get(id);
        if (el) grid.appendChild(el);
    });
}

function initDashboardCardDrag() {
    const grid = document.getElementById('dashboard-grid');
    if (!grid) return;
    const cards = grid.querySelectorAll('.dashboard-card[data-card-id]');
    cards.forEach(card => {
        card.addEventListener('dragstart', (e) => {
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', card.getAttribute('data-card-id'));
            card.classList.add('dragging');
        });
        card.addEventListener('dragend', (e) => {
            card.classList.remove('dragging');
            grid.querySelectorAll('.dashboard-card').forEach(c => c.classList.remove('drag-over'));
        });
    });
    grid.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const over = document.elementFromPoint(e.clientX, e.clientY)?.closest('.dashboard-card');
        grid.querySelectorAll('.dashboard-card').forEach(c => {
            c.classList.toggle('drag-over', c === over && !c.classList.contains('dragging'));
        });
    });
    grid.addEventListener('drop', (e) => {
        e.preventDefault();
        grid.querySelectorAll('.dashboard-card').forEach(c => c.classList.remove('drag-over'));
        const id = e.dataTransfer.getData('text/plain');
        if (!id) return;
        const dragged = grid.querySelector(`.dashboard-card[data-card-id="${id}"]`);
        const over = document.elementFromPoint(e.clientX, e.clientY)?.closest('.dashboard-card');
        if (!dragged || !over || dragged === over) return;
        grid.insertBefore(dragged, over);
        const newOrder = Array.from(grid.querySelectorAll('.dashboard-card[data-card-id]')).map(c => c.getAttribute('data-card-id'));
        setDashboardCardOrder(newOrder);
    });
}

document.getElementById('dashboard-btn-backup')?.addEventListener('click', async () => {
    const btn = document.getElementById('dashboard-btn-backup');
    if (btn.disabled) return;
    // 若今天已经有成功备份，点击前弹出确认
    try {
        const todayOk = window.__dashboard_today_ok || 0;
        if (todayOk > 0) {
            const ok = window.confirm('今天已经有成功备份记录，你确定还要手动执行一次全量备份吗？');
            if (!ok) return;
        }
    } catch (_) {}
    btn.disabled = true;
    btn.textContent = '启动中...';
    const res = await fetch(`${API}/backup/run`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
        toast(data.message || '已启动全量备份', 'success');
        showTab('backup');
    } else {
        toast(data.error || '启动失败', 'error');
    }
    btn.disabled = false;
    btn.textContent = '立即全量备份';
    loadDashboard();
});

// 皮肤切换（系统默认深色）
function applyTheme(theme) {
    theme = theme || 'dark';
    if (theme === 'apple') theme = 'light';
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem('vconfig_theme', theme); } catch (e) {}
}
function initTheme() {
    let theme = 'dark';
    try { theme = localStorage.getItem('vconfig_theme') || 'dark'; } catch (e) {}
    applyTheme(theme);
}
initTheme();
document.getElementById('theme-btn')?.addEventListener('click', function(e) {
    e.stopPropagation();
    document.getElementById('theme-switcher')?.classList.toggle('open');
});
document.getElementById('theme-dropdown')?.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', function() {
        const theme = this.getAttribute('data-theme');
        applyTheme(theme);
        document.getElementById('theme-switcher')?.classList.remove('open');
    });
});
document.addEventListener('click', () => document.getElementById('theme-switcher')?.classList.remove('open'));

// 全局页脚：当前时间、来访者 IP、自定义文案（版权/备案号）
let footerTimezone = 'Asia/Shanghai';
let footerTimeInterval = null;
function updateFooterTime() {
    const el = document.getElementById('footer-time');
    if (!el) return;
    const str = formatInTimezone(new Date().toISOString(), footerTimezone);
    const fallback = new Date().toLocaleString('zh-CN', { hour12: false });
    el.textContent = '当前时间: ' + (str || fallback);
}
async function loadFooterInfo() {
    const customEl = document.getElementById('footer-custom');
    const timeEl = document.getElementById('footer-time');
    const ipEl = document.getElementById('footer-ip');
    const sep1 = document.getElementById('footer-sep1');
    const sep2 = document.getElementById('footer-sep2');
    try {
        const res = await fetch(`${API}/footer-info`);
        if (!res.ok) return;
        const d = await res.json();
        footerTimezone = (d.timezone || 'Asia/Shanghai').trim();
        if (customEl) customEl.textContent = (d.footer_text || '').trim();
        if (ipEl) ipEl.textContent = (d.client_ip != null && String(d.client_ip).trim() !== '') ? '来访者 IP: ' + String(d.client_ip).trim() : '来访者 IP: --';
        updateFooterTime();
        if (footerTimeInterval) clearInterval(footerTimeInterval);
        footerTimeInterval = setInterval(updateFooterTime, 1000);
        const hasCustom = customEl && customEl.textContent.trim() !== '';
        if (sep1) sep1.textContent = ' | ';
        if (sep2) sep2.textContent = hasCustom ? ' | ' : '';
    } catch (_) {}
}

function startFooterTimeTicker() {
    const footerEl = document.getElementById('app-footer');
    if (footerEl && footerEl.getAttribute('data-timezone')) footerTimezone = (footerEl.getAttribute('data-timezone') || 'Asia/Shanghai').trim();
    updateFooterTime();
    if (footerTimeInterval) clearInterval(footerTimeInterval);
    footerTimeInterval = setInterval(updateFooterTime, 1000);
}

// 页面加载：用服务端注入的 data-timezone 启动时间（不依赖接口），再恢复 tab / 仪表盘
document.addEventListener('DOMContentLoaded', () => {
    if (window.applyI18n) window.applyI18n();
    startFooterTimeTicker();
    applyDashboardCardOrder();
    initDashboardCardDrag();
    if (document.getElementById('panel-config-device')) initConfigDevicePanelHandlers();
    const hash = (location.hash || '').replace(/^#/, '');
    if (hash === 'config-changes') {
        showTab('config-changes');
    } else if (hash.startsWith('config-device/')) {
        const parts = hash.split('/');
        if (parts.length >= 3) {
            try {
                const prefix = decodeURIComponent(parts[1]);
                const hostname = decodeURIComponent(parts.slice(2).join('/'));
                openConfigDevicePanel(prefix, hostname);
            } catch (_) { showTab('dashboard'); loadDashboard(); }
        } else {
            let tab = 'dashboard';
            try { tab = sessionStorage.getItem('vconfig_tab') || 'dashboard'; } catch (e) {}
            const panel = document.getElementById('panel-' + tab);
            if (panel) showTab(tab); else loadDashboard();
        }
    } else {
        let tab = 'dashboard';
        try { tab = sessionStorage.getItem('vconfig_tab') || 'dashboard'; } catch (e) {}
        const panel = document.getElementById('panel-' + tab);
        if (panel) showTab(tab); else loadDashboard();
    }
    // 初始化设置页草稿监听（用于刷新后保留未保存内容）
    initSettingsDraftWatchers();

    // 自动发现规则 & 快速扫描
    if (document.getElementById('settings-section-discovery')) {
        initDiscoveryModule();
    }
});

let _discoveryDeviceTypesCache = [];

async function loadDiscoveryDeviceTypes() {
    try {
        const res = await fetch(`${API}/device-types?include_disabled=1`);
        const data = await res.json();
        _discoveryDeviceTypesCache = data.items || [];
        return _discoveryDeviceTypesCache;
    } catch (e) {
        _discoveryDeviceTypesCache = [];
        return [];
    }
}

async function renderDiscoveryTypeKeywordsRows(rawText) {
    const container = document.getElementById('discovery-type-keywords-rows');
    if (!container) return;
    if (!_discoveryDeviceTypesCache.length) await loadDiscoveryDeviceTypes();
    const types = _discoveryDeviceTypesCache;
    const rows = [];
    for (const line of (rawText || '').split(/\r?\n/)) {
        const l = line.trim();
        if (!l || l.startsWith('#')) continue;
        const eq = l.indexOf('=');
        if (eq >= 0) {
            const type = l.slice(0, eq).trim();
            const keywords = l.slice(eq + 1).trim();
            rows.push({ type, keywords });
        }
    }
    if (!rows.length) rows.push({ type: '', keywords: '' });
    const typeCodes = new Set(types.map(t => t.type_code || ''));
    container.innerHTML = rows.map((r, i) => {
        let opts = types.map(t => `<option value="${escapeHtml(t.type_code || '')}" ${(t.type_code || '') === r.type ? 'selected' : ''}>${escapeHtml(t.display_name || t.type_code || '')}</option>`).join('');
        if (r.type && !typeCodes.has(r.type)) opts = `<option value="${escapeHtml(r.type)}" selected>${escapeHtml(r.type)}</option>` + opts;
        return `<div class="discovery-type-keyword-row" data-row="${i}">
            <select class="discovery-type-select">${opts}</select>
            <span>=</span>
            <input type="text" class="discovery-type-keywords-input" placeholder="关键字，逗号分隔" value="${escapeHtml(r.keywords || '')}">
            <button type="button" class="btn btn-secondary btn-sm discovery-type-keyword-del" title="删除">×</button>
        </div>`;
    }).join('');
    container.querySelectorAll('.discovery-type-keyword-del').forEach(btn => {
        btn.addEventListener('click', () => {
            btn.closest('.discovery-type-keyword-row')?.remove();
        });
    });
}

function getDiscoveryTypeKeywordsFromRows() {
    const container = document.getElementById('discovery-type-keywords-rows');
    if (!container) return '';
    const lines = [];
    container.querySelectorAll('.discovery-type-keyword-row').forEach(row => {
        const sel = row.querySelector('.discovery-type-select');
        const inp = row.querySelector('.discovery-type-keywords-input');
        const type = (sel?.value || '').trim();
        const keywords = (inp?.value || '').trim();
        if (type && keywords) lines.push(`${type}=${keywords}`);
    });
    return lines.join('\n');
}

async function initDiscoveryModule() {
    await loadDiscoveryDeviceTypes();
    // 加载 SNMP 设置
    try {
        const res = await fetch(`${API}/discovery/settings`);
        if (res.ok) {
            const d = await res.json();
            const verEl = document.getElementById('setting-snmp-version');
            if (verEl && d.snmp_version) verEl.value = d.snmp_version;
            const commEl = document.getElementById('setting-snmp-community');
            if (commEl && d.snmp_community != null) commEl.value = d.snmp_community;
            const toutEl = document.getElementById('setting-snmp-timeout');
            if (toutEl && d.snmp_timeout_ms != null) toutEl.value = d.snmp_timeout_ms;
            const rtEl = document.getElementById('setting-snmp-retries');
            if (rtEl && d.snmp_retries != null) rtEl.value = d.snmp_retries;
        }
    } catch (e) {}

    await loadDiscoveryRules();

    // 快速扫描
    document.getElementById('btn-discovery-quick-scan')?.addEventListener('click', async () => {
        const raw = (document.getElementById('discovery-quick-ip-range').value || '').trim();
        if (!raw) {
            toast('请输入 IP 段或 IP 列表', 'warn');
            return;
        }
        // 复用现有 /api/devices/discover 进行 TCP 22/23 探测
        const lines = raw.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
        let body = {};
        if (lines.length === 1 && (lines[0].includes('-') || lines[0].includes('/'))) {
            body.ip_range = lines[0];
        } else {
            body.ips = lines.join('\n');
        }
        const btn = document.getElementById('btn-discovery-quick-scan');
        const outWrap = document.getElementById('discovery-quick-result');
        const outPre = document.getElementById('discovery-quick-result-text');
        if (!btn || !outWrap || !outPre) return;
        btn.disabled = true;
        btn.textContent = '扫描中...';
        outWrap.style.display = 'none';
        try {
            const res = await fetch(`${API}/devices/discover`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (!res.ok) {
                toast(data.error || '扫描失败', 'error');
                return;
            }
            const results = data.results || [];
            const linesOut = [
                `共扫描 ${data.scanned || 0} 个 IP，22/23 端口开放的有 ${results.length} 个：`,
                '',
                ...results.map(r => `${r.ip}  ssh:${r.ssh_open ? '开' : '关'}  telnet:${r.telnet_open ? '开' : '关'}`),
            ];
            outPre.textContent = linesOut.join('\n');
            outWrap.style.display = '';
        } catch (e) {
            toast('扫描失败，请稍后重试', 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '扫描';
        }
    });

    // 新建规则按钮（打开弹窗）
    document.getElementById('btn-discovery-rule-new')?.addEventListener('click', () => {
        openDiscoveryRuleModal(null);
    });
    // 设备类型过滤：添加一行
    document.getElementById('btn-discovery-type-keyword-add')?.addEventListener('click', () => {
        const container = document.getElementById('discovery-type-keywords-rows');
        if (!container) return;
        const types = _discoveryDeviceTypesCache;
        const opts = types.map(t => `<option value="${escapeHtml(t.type_code || '')}">${escapeHtml(t.display_name || t.type_code || '')}</option>`).join('');
        const div = document.createElement('div');
        div.className = 'discovery-type-keyword-row';
        div.innerHTML = `<select class="discovery-type-select">${opts}</select><span>=</span><input type="text" class="discovery-type-keywords-input" placeholder="关键字，逗号分隔"><button type="button" class="btn btn-secondary btn-sm discovery-type-keyword-del" title="删除">×</button>`;
        div.querySelector('.discovery-type-keyword-del').addEventListener('click', () => div.remove());
        container.appendChild(div);
    });
}

let _discoveryStatusTimer = null;
let _discoveryStatusMap = {};
let _discoveryLogTimer = null;
let _currentDiscoveryLogRuleId = null;

async function refreshDiscoveryStatuses() {
    try {
        const res = await fetch(`${API}/discovery/rules/status`);
        if (!res.ok) return;
        const data = await res.json();
        const items = data.items || [];
        const map = {};
        items.forEach(j => {
            if (!j || j.rule_id == null) return;
            map[String(j.rule_id)] = j;
        });
        _discoveryStatusMap = map;
        const tbody = document.getElementById('discovery-rule-list');
        if (!tbody) return;
        tbody.querySelectorAll('[data-discovery-run]').forEach(btn => {
            const id = btn.getAttribute('data-discovery-run');
            if (!id) return;
            const st = _discoveryStatusMap[id];
            if (st && st.status === 'running') {
                btn.disabled = true;
                btn.textContent = '运行中...';
            } else {
                btn.disabled = false;
                btn.textContent = '运行';
            }
        });
        const anyRunning = items.some(j => j && j.status === 'running');
        if (anyRunning && !_discoveryStatusTimer) {
            _discoveryStatusTimer = setInterval(refreshDiscoveryStatuses, 3000);
        } else if (!anyRunning && _discoveryStatusTimer) {
            clearInterval(_discoveryStatusTimer);
            _discoveryStatusTimer = null;
        }
    } catch (e) {
        // ignore polling errors
    }
}

async function loadDiscoveryRules() {
    const tbody = document.getElementById('discovery-rule-list');
    if (!tbody) return;
    try {
        const res = await fetch(`${API}/discovery/rules`);
        const data = await res.json();
        const rules = data.rules || [];
        const canEdit = (data.can_edit_settings === undefined) ? true : !!data.can_edit_settings;
        if (!rules.length) {
            tbody.innerHTML = '<tr><td colspan="6">暂无规则，请点击「+ 新建规则」添加。</td></tr>';
            return;
        }
        tbody.innerHTML = rules.map(r => {
            const actionBtns = canEdit
                ? `<button type="button" class="btn btn-secondary btn-sm" data-discovery-run="${r.id}">运行</button>
                    <button type="button" class="btn btn-secondary btn-sm" data-discovery-toggle="${r.id}">${r.enabled ? '禁用' : '启用'}</button>
                    <button type="button" class="btn btn-secondary btn-sm" data-discovery-log="${r.id}">日志</button>
                    <button type="button" class="btn btn-secondary btn-sm" data-discovery-edit="${r.id}">编辑</button>
                    <button type="button" class="btn btn-delete btn-sm" data-discovery-delete="${r.id}">删除</button>`
                : `<button type="button" class="btn btn-secondary btn-sm" data-discovery-log="${r.id}">日志</button>`;
            return `
            <tr data-id="${r.id}">
                <td>${escapeHtml(r.name || '')}</td>
                <td><pre style="margin:0;white-space:pre-wrap;">${escapeHtml(r.ip_range || '')}</pre></td>
                <td>${escapeHtml(r.hostname_oid || '')}</td>
                <td>${escapeHtml(r.device_type_oid || '')}</td>
                <td>${escapeHtml(r.device_group || '')}</td>
                <td>${actionBtns}</td>
            </tr>
        `;
        }).join('');

        tbody.querySelectorAll('[data-discovery-run]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = btn.getAttribute('data-discovery-run');
                if (!id) return;
                if (!confirm('确定运行此自动发现规则并将新设备加入设备管理？')) return;
                btn.disabled = true;
                btn.textContent = '运行中...';
                try {
                    const res = await fetch(`${API}/discovery/rules/${id}/run`, { method: 'POST' });
                    const data = await res.json();
                    if (!res.ok || !data.ok) {
                        toast(data.error || '执行规则失败', 'error');
                        btn.disabled = false;
                        btn.textContent = '运行';
                    } else {
                        toast('已提交自动发现任务，正在后台运行…', 'success');
                        // 后续按钮状态与日志由轮询接口刷新
                        refreshDiscoveryStatuses();
                    }
                } catch (e) {
                    toast('执行规则失败，请稍后重试', 'error');
                }
            });
        });

        // 查看运行日志
        tbody.querySelectorAll('[data-discovery-log]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = btn.getAttribute('data-discovery-log');
                if (!id) return;
                try {
                    const res = await fetch(`${API}/discovery/rules/${id}/logs`);
                    const data = await res.json();
                    if (!res.ok || !data.logs) {
                        toast(data.error || '获取日志失败', 'error');
                        return;
                    }
                    openDiscoveryLogModal(data);
                } catch (e) {
                    toast('获取日志失败，请稍后重试', 'error');
                }
            });
        });

        // 启用/禁用规则
        tbody.querySelectorAll('[data-discovery-toggle]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = btn.getAttribute('data-discovery-toggle');
                if (!id) return;
                const tr = btn.closest('tr');
                const currentlyEnabled = btn.textContent.trim() === '禁用';
                const newEnabled = !currentlyEnabled;
                btn.disabled = true;
                btn.textContent = newEnabled ? '禁用中...' : '启用中...';
                try {
                    const res = await fetch(`${API}/discovery/rules/${id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enabled: newEnabled }),
                    });
                    const data = await res.json();
                    if (!res.ok || !data.ok) {
                        toast(data.error || '更新规则状态失败', 'error');
                    } else {
                        toast(newEnabled ? '规则已启用' : '规则已禁用', 'success');
                        // 直接更新按钮文本，避免整表重载
                        btn.textContent = newEnabled ? '禁用' : '启用';
                    }
                } catch (e) {
                    toast('更新规则状态失败，请稍后重试', 'error');
                } finally {
                    btn.disabled = false;
                }
            });
        });

        // 初始加载一次状态，确保刷新或切换页面后仍能看到运行中的任务
        refreshDiscoveryStatuses();

        tbody.querySelectorAll('[data-discovery-delete]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = btn.getAttribute('data-discovery-delete');
                if (!id) return;
                if (!confirm('确定删除该规则吗？')) return;
                try {
                    const res = await fetch(`${API}/discovery/rules/${id}`, { method: 'DELETE' });
                    const data = await res.json();
                    if (!res.ok || !data.ok) {
                        toast(data.error || '删除规则失败', 'error');
                    } else {
                        toast('规则已删除', 'success');
                        await loadDiscoveryRules();
                    }
                } catch (e) {
                    toast('删除规则失败，请稍后重试', 'error');
                }
            });
        });

        // 编辑规则（弹窗）
        tbody.querySelectorAll('[data-discovery-edit]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = btn.getAttribute('data-discovery-edit');
                if (!id) return;
                // 优先使用接口返回的完整规则对象（包含 snmp_community 等字段）
                const full = rules.find(r => String(r.id) === String(id));
                if (full) {
                    openDiscoveryRuleModal(full);
                    return;
                }
                // 兜底：从当前行读取基础字段
                const tr = btn.closest('tr');
                const nameTd = tr?.children[0];
                const ipTd = tr?.children[1];
                const hostOidTd = tr?.children[2];
                const typeOidTd = tr?.children[3];
                const groupTd = tr?.children[4];
                const oldName = nameTd ? nameTd.textContent.trim() : '';
                const oldIp = ipTd ? ipTd.textContent : '';
                const oldHostOid = hostOidTd ? hostOidTd.textContent.trim() : '';
                const oldTypeOid = typeOidTd ? typeOidTd.textContent.trim() : '';
                const oldGroup = groupTd ? groupTd.textContent.trim() : '';

                openDiscoveryRuleModal({
                    id,
                    name: oldName,
                    ip_range: oldIp,
                    hostname_oid: oldHostOid,
                    device_type_oid: oldTypeOid,
                    device_group: oldGroup,
                });
            });
        });
    } catch (e) {
        const tbody = document.getElementById('discovery-rule-list');
        if (tbody) tbody.innerHTML = '<tr><td colspan="8">加载规则失败，请稍后重试。</td></tr>';
    }
}

let currentEditingDiscoveryRuleId = null;

function openDiscoveryRuleModal(rule) {
    const modal = document.getElementById('modal-discovery-rule');
    if (!modal) return;
    const titleEl = document.getElementById('modal-discovery-rule-title');
    const nameInput = document.getElementById('discovery-rule-name');
    const ipInput = document.getElementById('discovery-rule-ip-range');
    const commInput = document.getElementById('discovery-rule-community');
    const hostOidInput = document.getElementById('discovery-rule-hostname-oid');
    const typeOidInput = document.getElementById('discovery-rule-type-oid');
    const groupInput = document.getElementById('discovery-rule-group');

    currentEditingDiscoveryRuleId = rule && rule.id ? rule.id : null;

    if (titleEl) {
        titleEl.textContent = currentEditingDiscoveryRuleId ? '编辑自动发现规则' : '新建自动发现规则';
    }

    if (rule) {
        if (nameInput) nameInput.value = rule.name || '';
        if (ipInput) ipInput.value = rule.ip_range || '';
        if (commInput) commInput.value = rule.snmp_community || '';
        if (hostOidInput) hostOidInput.value = rule.hostname_oid || '';
        if (typeOidInput) typeOidInput.value = rule.device_type_oid || '';
        if (groupInput) groupInput.value = rule.device_group || '';
    } else {
        if (nameInput) nameInput.value = '';
        if (ipInput) ipInput.value = '';
        if (commInput) {
            // 默认带入全局 SNMP Community
            const globalComm = document.getElementById('setting-snmp-community');
            commInput.value = globalComm && globalComm.value ? globalComm.value : '';
        }
        if (hostOidInput) hostOidInput.value = '1.3.6.1.2.1.1.5.0';
        if (typeOidInput) typeOidInput.value = '1.3.6.1.2.1.1.1.0';
        if (groupInput) groupInput.value = '';
    }

    // 同步设备分组名称到分组输入框的 datalist
    try {
        const dl = document.getElementById('discovery-rule-group-datalist');
        if (dl) {
            fetch(`${API}/device-groups?from_devices=1`).then(r => r.json()).then(d => {
                const groups = d.groups || [];
                dl.innerHTML = groups.map(g => `<option value="${String(g).replace(/"/g, '&quot;')}">`).join('');
            }).catch(() => {
                dl.innerHTML = '';
            });
        }
    } catch (e) {}

    modal.classList.add('show');
}

function closeDiscoveryRuleModal() {
    const modal = document.getElementById('modal-discovery-rule');
    if (!modal) return;
    modal.classList.remove('show');
    currentEditingDiscoveryRuleId = null;
}

document.getElementById('btn-discovery-log-close')?.addEventListener('click', () => {
    const modal = document.getElementById('modal-discovery-log');
    if (modal) modal.classList.remove('show');
    if (_discoveryLogTimer) {
        clearInterval(_discoveryLogTimer);
        _discoveryLogTimer = null;
    }
    _currentDiscoveryLogRuleId = null;
});

function openDiscoveryLogModal(data) {
    const modal = document.getElementById('modal-discovery-log');
    if (!modal) return;
    const titleEl = document.getElementById('modal-discovery-log-title');
    const ruleInfoEl = document.getElementById('discovery-log-rule-info');
    const textEl = document.getElementById('discovery-log-text');
    const statusEl = document.getElementById('discovery-log-status');
    const rule = data.rule || {};
    const logs = data.logs || [];

    if (titleEl) {
        titleEl.textContent = `自动发现运行日志 - ${rule.name || ''}`;
    }
    if (ruleInfoEl) {
        const parts = [];
        if (rule.name) parts.push(`规则：${rule.name}`);
        if (rule.ip_range) parts.push(`IP 范围：${rule.ip_range}`);
        if (rule.snmp_community) parts.push(`Community：${rule.snmp_community}`);
        if (rule.device_group) parts.push(`分组：${rule.device_group}`);
        ruleInfoEl.textContent = parts.join(' | ') || '—';
    }
    if (statusEl) {
        statusEl.textContent = '正在加载状态…';
    }
    if (textEl) {
        if (!logs.length) {
            textEl.textContent = '暂无运行记录。请先点击「运行」执行一次自动发现规则。';
        } else {
            const lines = [];
            logs.forEach((log, idx) => {
                lines.push(`【第 ${logs.length - idx} 条】`);
                lines.push(`时间：${log.started_at || ''} ~ ${log.finished_at || ''}`);
                lines.push(`扫描 IP 数：${log.scanned || 0}，新增设备数：${log.added_count || 0}`);
                const added = log.added || [];
                if (added.length) {
                    lines.push('新增设备：');
                    added.forEach(d => {
                        lines.push(`  - ${d.ip || ''}  ${d.hostname || ''}  类型：${d.device_type || ''}`);
                    });
                }
                const skipped = log.skipped || [];
                if (skipped.length) {
                    lines.push('跳过 IP：');
                    skipped.forEach(s => {
                        const hostPart = (s.hostname && s.reason === 'exists') ? ` (${s.hostname})` : '';
                        lines.push(`  - ${s.ip || ''}${hostPart}  原因：${s.reason || ''}`);
                    });
                }
                lines.push('');
            });
            textEl.textContent = lines.join('\n');
        }
    }
    _currentDiscoveryLogRuleId = rule.id || null;
    if (_discoveryLogTimer) {
        clearInterval(_discoveryLogTimer);
        _discoveryLogTimer = null;
    }
    const refreshNow = async () => {
        const rid = _currentDiscoveryLogRuleId;
        if (!rid) return;
        try {
            const res = await fetch(`${API}/discovery/rules/${rid}/status`);
            if (!res.ok) {
                if (statusEl) statusEl.textContent = '状态获取失败，请稍后重试。';
                return;
            }
            const job = await res.json();
            if (!job || !job.status) {
                if (statusEl) statusEl.textContent = '暂无正在运行的任务。';
                return;
            }
            if (statusEl) {
                if (job.status === 'running') {
                    statusEl.textContent = `运行中… 已扫描 ${job.scanned || 0} 个 IP，新增 ${job.added_count || 0} 台设备`;
                } else if (job.status === 'success') {
                    statusEl.textContent = `已完成：扫描 ${job.scanned || 0} 个 IP，新增 ${job.added_count || 0} 台设备`;
                } else {
                    statusEl.textContent = `任务失败：${job.error || ''}`;
                }
            }
            if (job.status !== 'running') {
                // 任务完成后刷新一次完整日志
                try {
                    const logRes = await fetch(`${API}/discovery/rules/${rid}/logs`);
                    const logData = await logRes.json();
                    if (logRes.ok && logData.logs && textEl) {
                        const logs2 = logData.logs || [];
                        const lines2 = [];
                        logs2.forEach((log, idx) => {
                            lines2.push(`【第 ${logs2.length - idx} 条】`);
                            lines2.push(`时间：${log.started_at || ''} ~ ${log.finished_at || ''}`);
                            lines2.push(`扫描 IP 数：${log.scanned || 0}，新增设备数：${log.added_count || 0}`);
                            const added = log.added || [];
                            if (added.length) {
                                lines2.push('新增设备：');
                                added.forEach(d => {
                                    lines2.push(`  - ${d.ip || ''}  ${d.hostname || ''}  类型：${d.device_type || ''}`);
                                });
                            }
                            const skipped = log.skipped || [];
                            if (skipped.length) {
                                lines2.push('跳过 IP：');
                                skipped.forEach(s => {
                                    const hostPart = (s.hostname && s.reason === 'exists') ? ` (${s.hostname})` : '';
                                    lines2.push(`  - ${s.ip || ''}${hostPart}  原因：${s.reason || ''}`);
                                });
                            }
                            lines2.push('');
                        });
                        textEl.textContent = lines2.join('\n');
                    }
                } catch (_) {}
            }
        } catch (e) {
            if (statusEl) statusEl.textContent = '状态获取失败，请稍后重试。';
        }
    };
    refreshNow();
    _discoveryLogTimer = setInterval(refreshNow, 3000);
    modal.classList.add('show');
}

// 自动发现规则弹窗事件绑定
document.getElementById('btn-discovery-rule-cancel')?.addEventListener('click', () => {
    closeDiscoveryRuleModal();
});

document.getElementById('btn-discovery-rule-save')?.addEventListener('click', async () => {
    const nameInput = document.getElementById('discovery-rule-name');
    const ipInput = document.getElementById('discovery-rule-ip-range');
    const commInput = document.getElementById('discovery-rule-community');
    const hostOidInput = document.getElementById('discovery-rule-hostname-oid');
    const typeOidInput = document.getElementById('discovery-rule-type-oid');
    const groupInput = document.getElementById('discovery-rule-group');

    const name = nameInput?.value.trim() || '';
    const ipRange = ipInput?.value.trim() || '';
    if (!name) {
        toast('请填写规则名称', 'warn');
        nameInput?.focus();
        return;
    }
    if (!ipRange) {
        toast('请填写 IP 范围', 'warn');
        ipInput?.focus();
        return;
    }

    const body = {
        name,
        ip_range: ipRange,
        snmp_community: commInput?.value.trim() || '',
        hostname_oid: hostOidInput?.value.trim() || '',
        device_type_oid: typeOidInput?.value.trim() || '',
        device_group: groupInput?.value.trim() || '',
    };

    const isEdit = !!currentEditingDiscoveryRuleId;
    const url = isEdit ? `${API}/discovery/rules/${currentEditingDiscoveryRuleId}` : `${API}/discovery/rules`;
    const method = isEdit ? 'PUT' : 'POST';

    try {
        const res = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            toast(data.error || (isEdit ? '更新规则失败' : '创建规则失败'), 'error');
            return;
        }
        toast(isEdit ? '规则已更新' : '已创建自动发现规则', 'success');
        closeDiscoveryRuleModal();
        await loadDiscoveryRules();
    } catch (e) {
        toast(isEdit ? '更新规则失败，请稍后重试' : '创建规则失败，请稍后重试', 'error');
    }
});

document.getElementById('btn-discovery-log-close')?.addEventListener('click', () => {
    const modal = document.getElementById('modal-discovery-log');
    if (modal) modal.classList.remove('show');
});

// 设备列表
function escapeHtml(s) {
    if (!s) return '';
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}
let devicePage = 1;
let devicePerPage = 50;
let deviceTotal = 0;
let deviceSortBy = 'hostname';
let deviceSortDir = 'asc';
async function loadDevices(noCache) {
    const perPageEl = document.getElementById('device-per-page');
    if (perPageEl) devicePerPage = parseInt(perPageEl.value, 10) || 50;
    const search = document.getElementById('filter-search')?.value?.trim() || '';
    let url = `${API}/devices?page=${devicePage}&per_page=${devicePerPage}`;
    if (search) url += '&search=' + encodeURIComponent(search);
    if (deviceSortBy) {
        url += `&sort_by=${encodeURIComponent(deviceSortBy)}&sort_dir=${encodeURIComponent(deviceSortDir || 'asc')}`;
    }
    if (noCache) url += '&_t=' + Date.now();
    const res = await fetch(url);
    const data = await res.json();
    const list = data.items || [];
    const tbody = document.getElementById('device-list');
    if (!list.length) {
        deviceTotal = 0;
        tbody.innerHTML = '<tr><td colspan="8" class="empty-state">暂无设备，请添加或导入</td></tr>';
        const pagEl = document.getElementById('device-pagination');
        if (pagEl) pagEl.innerHTML = '<span>共 0 台</span>';

        // 同步更新批量删除/批量编辑按钮权限状态
        const canManage = (data.can_manage_devices === undefined) ? true : !!data.can_manage_devices;
        const batchDelBtn = document.getElementById('btn-batch-delete');
        const batchEditBtn = document.getElementById('btn-batch-edit');
        if (batchDelBtn) batchDelBtn.disabled = !canManage;
        if (batchEditBtn) batchEditBtn.disabled = !canManage;
        return;
    }
    deviceTotal = data.total || 0;
    const defaultConn = (data.default_connection_type || 'TELNET').toUpperCase();
    try { window.DEFAULT_CONN_TYPE = defaultConn; } catch (_) {}
    const connLabel = (c) => ((c && c !== '') ? c : defaultConn) === 'SSH' ? 'SSH' : 'Telnet';
    const groups = data.groups || [];
    const groupListEl = document.getElementById('device-group-list');
    if (groupListEl) groupListEl.innerHTML = groups.map(g => `<option value="${escapeHtml(g)}">`).join('');
    tbody.innerHTML = list.map(d => `
        <tr>
            <td><input type="checkbox" class="device-cb" value="${d.id}"></td>
            <td>${escapeHtml(d.hostname)}</td>
            <td>${escapeHtml(d.ip)}</td>
            <td>${escapeHtml(d.device_type)}</td>
            <td>${escapeHtml(d.group || '')}</td>
            <td>${connLabel(d.connection_type)}</td>
            <td>${d.enabled ? '是' : '否'}</td>
            <td class="device-actions">
                <button type="button" class="btn btn-sm btn-edit" data-action="edit" data-id="${d.id}">编辑</button>
                <button type="button" class="btn btn-sm btn-secondary" data-action="maintenance" data-id="${d.id}" data-hostname="${escapeHtml(d.hostname)}">维护</button>
                <button type="button" class="btn btn-sm btn-backup" data-action="backup" data-id="${d.id}">备份</button>
                <button type="button" class="btn btn-sm btn-test" data-action="test" data-id="${d.id}">测试</button>
                <button type="button" class="btn btn-sm btn-push" data-action="push" data-id="${d.id}" data-hostname="${escapeHtml(d.hostname)}">登录</button>
                <button type="button" class="btn btn-sm btn-delete" data-action="delete" data-id="${d.id}" data-hostname="${escapeHtml(d.hostname)}">删除</button>
            </td>
        </tr>
    `).join('');
    updateSelectAllState();
    const total = data.total || 0;
    const totalPages = Math.ceil(total / devicePerPage) || 1;
    const pagEl = document.getElementById('device-pagination');
    if (pagEl) {
        pagEl.innerHTML = `
            <span>共 ${total} 台</span>
            <button type="button" class="btn btn-secondary btn-sm" ${devicePage <= 1 ? 'disabled' : ''} data-device-prev>上一页</button>
            <span>${devicePage} / ${totalPages}</span>
            <button type="button" class="btn btn-secondary btn-sm" ${devicePage >= totalPages ? 'disabled' : ''} data-device-next>下一页</button>
        `;
        pagEl.querySelector('[data-device-prev]')?.addEventListener('click', () => { devicePage--; loadDevices(); });
        pagEl.querySelector('[data-device-next]')?.addEventListener('click', () => { devicePage++; loadDevices(); });
    }

    // 根据权限控制批量删除、批量编辑与单条删除按钮
    const canManage = (data.can_manage_devices === undefined) ? true : !!data.can_manage_devices;
    const batchDelBtn = document.getElementById('btn-batch-delete');
    const batchEditBtn = document.getElementById('btn-batch-edit');
    if (batchDelBtn) batchDelBtn.disabled = !canManage;
    if (batchEditBtn) batchEditBtn.disabled = !canManage;
    if (!canManage) {
        document.querySelectorAll('#device-list .btn-delete').forEach(btn => { btn.disabled = true; });
    }

    // 更新排序按钮的高亮状态
    updateDeviceSortUI(data.sort_by || deviceSortBy, data.sort_dir || deviceSortDir);
}
document.getElementById('device-select-all')?.addEventListener('change', function() {
    document.querySelectorAll('.device-cb').forEach(cb => { cb.checked = this.checked; });
});
function updateSelectAllState() {
    const all = document.querySelectorAll('.device-cb');
    const checked = document.querySelectorAll('.device-cb:checked');
    const head = document.getElementById('device-select-all');
    if (head) {
        head.checked = all.length > 0 && checked.length === all.length;
        head.indeterminate = checked.length > 0 && checked.length < all.length;
    }
}
document.addEventListener('change', function(e) {
    if (e.target.classList.contains('device-cb')) updateSelectAllState();
});
document.getElementById('device-per-page')?.addEventListener('change', () => { devicePage = 1; loadDevices(); });
document.getElementById('btn-device-search')?.addEventListener('click', () => { devicePage = 1; loadDevices(); });
document.getElementById('filter-search')?.addEventListener('keypress', e => { if (e.key === 'Enter') { devicePage = 1; loadDevices(); } });
document.getElementById('filter-search')?.addEventListener('input', debounce(() => { devicePage = 1; loadDevices(); }, 350));

// 设备列表列头排序
function setDeviceSort(by) {
    if (!by) return;
    if (deviceSortBy === by) {
        deviceSortDir = deviceSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        deviceSortBy = by;
        deviceSortDir = 'asc';
    }
    devicePage = 1;
    loadDevices();
}

function updateDeviceSortUI(currentBy, currentDir) {
    deviceSortBy = currentBy || deviceSortBy || 'hostname';
    deviceSortDir = (currentDir === 'desc') ? 'desc' : 'asc';
    document.querySelectorAll('.device-sort-btn').forEach(btn => {
        const by = btn.getAttribute('data-sort-by');
        btn.classList.remove('active', 'dir-asc', 'dir-desc');
        if (by === deviceSortBy) {
            btn.classList.add('active');
            btn.classList.add(deviceSortDir === 'desc' ? 'dir-desc' : 'dir-asc');
        }
    });
}

document.querySelectorAll('.device-sort-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const by = btn.getAttribute('data-sort-by');
        setDeviceSort(by);
    });
});

document.getElementById('btn-export-csv')?.addEventListener('click', () => {
    window.open(`${API}/devices/export`, '_blank');
    toast('已发起导出', 'success');
});
document.getElementById('device-list')?.addEventListener('click', function(e) {
    const btn = e.target.closest('button[data-action][data-id]');
    if (!btn) return;
    const action = btn.getAttribute('data-action');
    const id = parseInt(btn.getAttribute('data-id'), 10);
    const hostname = btn.getAttribute('data-hostname') || '';
    if (action === 'edit') { editDevice(id); return; }
    if (action === 'maintenance') { openMaintenanceModal(id, hostname); return; }
    if (action === 'backup') { backupOne(id, btn); return; }
    if (action === 'test') { testConn(id, btn); return; }
    if (action === 'delete') { showDeleteOneConfirm(id, hostname); return; }
    if (action === 'push') { openTerminalLoginModal(id, hostname); return; }
});

let _pendingDeleteId = null;
function showDeleteOneConfirm(id, name) {
    _pendingDeleteId = id;
    document.getElementById('modal-delete-one-msg').textContent = `确定删除设备「${name || id}」？`;
    document.getElementById('modal-delete-one').classList.add('show');
}
document.getElementById('btn-delete-one-cancel')?.addEventListener('click', () => {
    _pendingDeleteId = null;
    document.getElementById('modal-delete-one').classList.remove('show');
});
document.getElementById('btn-delete-one-confirm')?.addEventListener('click', async () => {
    const id = _pendingDeleteId;
    if (id == null) return;
    _pendingDeleteId = null;
    document.getElementById('modal-delete-one').classList.remove('show');
    const res = await fetch(`${API}/devices/${id}`, { method: 'DELETE' });
    if (res.ok) {
        toast('已删除', 'success');
        loadDevices();
        loadDashboard();
        loadConfigs();
    } else {
        const d = await res.json();
        toast(d.error || '删除失败', 'error');
    }
});

let _pendingBatchDeleteIds = [];
let _pendingBatchDeleteAll = false;
function showBatchDeleteConfirm(ids, isDeleteAll) {
    _pendingBatchDeleteIds = ids;
    _pendingBatchDeleteAll = !!isDeleteAll;
    const msgEl = document.getElementById('modal-batch-delete-msg');
    if (isDeleteAll && deviceTotal > 0) {
        msgEl.textContent = `当前已选本页全部，要删除全部共 ${deviceTotal} 台设备吗？请输入「删除」以确认。`;
    } else {
        msgEl.textContent = `确定删除选中的 ${ids.length} 台设备？请输入「删除」以确认。`;
    }
    const input = document.getElementById('batch-delete-confirm-input');
    const btn = document.getElementById('btn-batch-delete-confirm');
    input.value = '';
    btn.disabled = true;
    document.getElementById('modal-batch-delete').classList.add('show');
    input.focus();
}
document.getElementById('batch-delete-confirm-input')?.addEventListener('input', function() {
    document.getElementById('btn-batch-delete-confirm').disabled = this.value.trim() !== '删除';
});
document.getElementById('batch-delete-confirm-input')?.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && this.value.trim() === '删除') {
        document.getElementById('btn-batch-delete-confirm').click();
    }
});
document.getElementById('btn-batch-delete-cancel')?.addEventListener('click', () => {
    _pendingBatchDeleteIds = [];
    _pendingBatchDeleteAll = false;
    document.getElementById('batch-delete-confirm-input').value = '';
    document.getElementById('modal-batch-delete').classList.remove('show');
});
document.getElementById('btn-batch-delete-confirm')?.addEventListener('click', async () => {
    const ids = _pendingBatchDeleteIds;
    const isDeleteAll = _pendingBatchDeleteAll;
    _pendingBatchDeleteIds = [];
    _pendingBatchDeleteAll = false;
    document.getElementById('modal-batch-delete').classList.remove('show');
    document.getElementById('batch-delete-confirm-input').value = '';
    if (isDeleteAll) {
        const res = await fetch(`${API}/devices/delete-all`, { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.ok) {
            toast(`已删除 ${data.deleted} 台设备`, 'success');
            loadDevices();
            loadDashboard();
            loadConfigs();
        } else {
            toast(data.error || '删除失败', 'error');
        }
    } else {
        if (!ids.length) return;
        const res = await fetch(`${API}/devices/batch-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids })
        });
        const data = await res.json();
        if (res.ok) {
            toast(`已删除 ${data.deleted} 台设备`, 'success');
            loadDevices();
            loadDashboard();
            loadConfigs();
        } else {
            toast(data.error || '删除失败', 'error');
        }
    }
});

async function batchDelete() {
    const ids = Array.from(document.querySelectorAll('.device-cb:checked')).map(cb => parseInt(cb.value, 10));
    if (ids.length === 0) {
        toast('请先勾选要删除的设备', 'warn');
        return;
    }
    const currentPageCount = document.querySelectorAll('.device-cb').length;
    const isDeleteAll = (ids.length === currentPageCount && deviceTotal > currentPageCount);
    showBatchDeleteConfirm(ids, isDeleteAll);
}
document.getElementById('btn-batch-delete')?.addEventListener('click', batchDelete);

// 批量编辑
document.getElementById('btn-batch-edit')?.addEventListener('click', async () => {
    const ids = Array.from(document.querySelectorAll('.device-cb:checked')).map(cb => parseInt(cb.value, 10)).filter(n => !isNaN(n));
    if (!ids.length) {
        toast('请先勾选要编辑的设备', 'warn');
        return;
    }
    document.getElementById('batch-edit-device-type').value = '';
    document.getElementById('batch-edit-group').value = '';
    document.getElementById('batch-edit-connection-type').value = '__nochange__';
    document.getElementById('batch-edit-ssh-port').value = '';
    document.getElementById('batch-edit-telnet-port').value = '';
    try {
        const res = await fetch(`${API}/devices?per_page=1`);
        const data = await res.json();
        const groups = data.groups || [];
        const listEl = document.getElementById('batch-edit-group-list');
        if (listEl) listEl.innerHTML = groups.map(g => `<option value="${escapeHtml(g)}">`).join('');
    } catch (_) {}
    document.getElementById('modal-batch-edit').classList.add('show');
});
document.getElementById('btn-batch-edit-cancel')?.addEventListener('click', () => {
    document.getElementById('modal-batch-edit').classList.remove('show');
});
document.getElementById('btn-batch-edit-submit')?.addEventListener('click', async () => {
    const ids = Array.from(document.querySelectorAll('.device-cb:checked')).map(cb => parseInt(cb.value, 10)).filter(n => !isNaN(n));
    if (!ids.length) {
        toast('请先勾选要编辑的设备', 'warn');
        return;
    }
    const payload = { ids };
    const dt = (document.getElementById('batch-edit-device-type').value || '').trim();
    if (dt) payload.device_type = dt;
    const grp = (document.getElementById('batch-edit-group').value || '').trim();
    if (grp) payload.group = grp;
    const connRaw = document.getElementById('batch-edit-connection-type').value;
    if (connRaw !== '__nochange__' && connRaw !== undefined) payload.connection_type = (connRaw || '').trim();
    const portStr = (document.getElementById('batch-edit-ssh-port').value || '').trim();
    if (portStr) {
        const port = parseInt(portStr, 10);
        if (!isNaN(port) && port >= 1 && port <= 65535) payload.ssh_port = port;
    }
    const telnetPortStr = (document.getElementById('batch-edit-telnet-port').value || '').trim();
    if (telnetPortStr) {
        const port = parseInt(telnetPortStr, 10);
        if (!isNaN(port) && port >= 1 && port <= 65535) payload.telnet_port = port;
    }
    if (Object.keys(payload).length <= 1) {
        toast('请至少填写一项要修改的内容', 'warn');
        return;
    }
    try {
        const res = await fetch(`${API}/devices/batch-update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok && data.ok) {
            toast(`已更新 ${data.updated ?? ids.length} 台设备`, 'success');
            document.getElementById('modal-batch-edit').classList.remove('show');
            loadDevices();
        } else {
            toast(data.error || '批量更新失败', 'error');
        }
    } catch (e) {
        toast('批量更新失败，请稍后重试', 'error');
    }
});

// 远程登录终端
let _terminalSessionId = null;
let _terminalDeviceId = null;
let _terminalEventSource = null;
let _terminalXterm = null;
let _terminalFitAddon = null;
let _terminalResizeObserver = null;

function loadScript(src) {
    return new Promise((resolve, reject) => {
        if (document.querySelector('script[src="' + src + '"]')) {
            resolve();
            return;
        }
        const s = document.createElement('script');
        s.src = src;
        s.onload = resolve;
        s.onerror = reject;
        document.head.appendChild(s);
    });
}

function loadCss(href) {
    return new Promise((resolve, reject) => {
        if (document.querySelector('link[href="' + href + '"]')) {
            resolve();
            return;
        }
        const l = document.createElement('link');
        l.rel = 'stylesheet';
        l.href = href;
        l.onload = resolve;
        l.onerror = reject;
        document.head.appendChild(l);
    });
}

let _terminalLoginDeviceId = null;
let _terminalLoginHostname = '';
const TERMINAL_LOGIN_HINT = '已填入当前系统账号。你确定要用当前的用户名密码登录设备吗？可修改后再确定。';
async function openTerminalLoginModal(id, hostname) {
    _terminalLoginDeviceId = id;
    _terminalLoginHostname = hostname || '';
    document.getElementById('modal-terminal-login-title').textContent = '输入登录凭据' + (hostname ? ' - ' + hostname : '');
    document.getElementById('terminal-login-hint').textContent = TERMINAL_LOGIN_HINT;
    document.getElementById('terminal-login-username').value = '';
    document.getElementById('terminal-login-password').value = '';
    document.getElementById('modal-terminal-login').classList.add('show');
    try {
        const res = await fetch(`${API}/terminal-login-defaults`);
        if (res.ok) {
            const d = await res.json();
            if (d.username != null) document.getElementById('terminal-login-username').value = d.username;
            if (d.password != null) document.getElementById('terminal-login-password').value = d.password;
        }
    } catch (e) {}
    setTimeout(() => document.getElementById('terminal-login-username').focus(), 80);
}
document.getElementById('btn-terminal-login-cancel')?.addEventListener('click', () => {
    document.getElementById('modal-terminal-login').classList.remove('show');
    _terminalLoginDeviceId = null;
});
function confirmTerminalLogin() {
    const id = _terminalLoginDeviceId;
    const hostname = _terminalLoginHostname;
    const username = (document.getElementById('terminal-login-username').value || '').trim();
    const password = document.getElementById('terminal-login-password').value || '';
    if (!username) {
        alert('请输入用户名');
        return;
    }
    if (!password) {
        alert('请输入密码');
        return;
    }
    document.getElementById('modal-terminal-login').classList.remove('show');
    _terminalLoginDeviceId = null;
    openTerminalModal(id, hostname, username, password);
}
document.getElementById('btn-terminal-login-confirm')?.addEventListener('click', confirmTerminalLogin);
document.getElementById('terminal-login-password')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') confirmTerminalLogin();
});

async function openTerminalModal(id, hostname, username, password) {
    const modal = document.getElementById('modal-terminal');
    const container = document.getElementById('terminal-container');
    const titleEl = document.getElementById('modal-terminal-title');
    if (!modal || !container) return;
    if (titleEl) titleEl.textContent = '登录设备' + (hostname ? ' - ' + hostname : '');
    container.innerHTML = '<div style="padding:1rem;color:#aaa;">连接中...</div>';
    const win = document.getElementById('terminal-window');
    if (win) {
        win.classList.remove('terminal-maximized', 'terminal-minimized');
        win.style.left = win.style.top = win.style.width = win.style.height = win.style.transform = '';
        const bodyEl = document.getElementById('terminal-body');
        if (bodyEl) bodyEl.style.display = '';
        const btnMin = document.getElementById('btn-terminal-min');
        const btnMax = document.getElementById('btn-terminal-max');
        if (btnMin) btnMin.textContent = '最小化';
        if (btnMax) btnMax.textContent = '最大化';
    }
    modal.classList.add('show');

    try {
        await Promise.all([
            loadCss('https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css'),
            loadScript('https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js'),
        ]);
        await loadScript('https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js');
    } catch (e) {
        container.innerHTML = '<div style="padding:1rem;color:#f66;">加载终端组件失败，请检查网络。</div>';
        return;
    }

    let sessionId;
    const startBody = (username != null && password != null) ? { username: username, password: password } : {};
    try {
        const res = await fetch(`${API}/devices/${id}/terminal/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(startBody),
        });
        const data = await res.json();
        if (!res.ok) {
            container.innerHTML = '<div style="padding:1rem;color:#f66;">' + (data.error || '连接失败') + '</div>';
            return;
        }
        sessionId = data.session_id;
        _terminalSessionId = sessionId;
        _terminalDeviceId = id;
    } catch (e) {
        container.innerHTML = '<div style="padding:1rem;color:#f66;">请求失败，请稍后重试。</div>';
        return;
    }

    container.innerHTML = '';
    const { Terminal } = window;
    const term = new Terminal({
        cursorBlink: true,
        theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
        fontSize: 14,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
    });
    const FitAddonCtor = window.FitAddon
        || (window['xterm-addon-fit'] && (window['xterm-addon-fit'].FitAddon || window['xterm-addon-fit'].default || window['xterm-addon-fit']));
    if (typeof FitAddonCtor === 'function') {
        try {
            _terminalFitAddon = new FitAddonCtor();
            term.loadAddon(_terminalFitAddon);
        } catch (e) {
            _terminalFitAddon = null;
        }
    }
    term.open(container);
    _terminalXterm = term;
    term.write('正在连接终端流...\r\n');
    function doFit() {
        if (!_terminalXterm) return;
        if (_terminalFitAddon && _terminalXterm) {
            try {
                _terminalFitAddon.fit();
            } catch (err) {}
        } else if (container) {
            try {
                const w = container.clientWidth;
                const h = container.clientHeight;
                if (w > 0 && h > 0) {
                    var cw = 8.5, ch = 17;
                    if (term._core && term._core._renderService && term._core._renderService.dimensions) {
                        var d = term._core._renderService.dimensions;
                        if (d.css && d.css.cell) {
                            cw = d.css.cell.width || cw;
                            ch = d.css.cell.height || ch;
                        }
                    }
                    term.resize(Math.max(10, Math.floor(w / cw)), Math.max(5, Math.floor(h / ch)));
                }
            } catch (e) {}
        }
    }
    window._terminalDoFit = doFit;
    function scheduleFit() {
        setTimeout(doFit, 0);
        requestAnimationFrame(() => setTimeout(doFit, 50));
    }
    scheduleFit();
    setTimeout(scheduleFit, 120);
    if (typeof ResizeObserver !== 'undefined' && container) {
        _terminalResizeObserver = new ResizeObserver(() => doFit());
        _terminalResizeObserver.observe(container);
    }

    term.onData((data) => {
        if (!_terminalSessionId) return;
        fetch(`${API}/devices/${id}/terminal/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: _terminalSessionId, data: data }),
        }).catch(() => {});
    });

    function connectStream() {
        const streamUrl = `${API}/devices/${id}/terminal/stream?session_id=${encodeURIComponent(sessionId)}`;
        const es = new EventSource(streamUrl);
        _terminalEventSource = es;
        let firstMessage = true;
        es.onmessage = (ev) => {
            try {
                const b64 = (ev.data || '').replace(/\s/g, '');
                if (!b64) return;
                const raw = atob(b64);
                const bytes = new Uint8Array(raw.length);
                for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
                if (firstMessage) {
                    firstMessage = false;
                    doFit();
                }
                term.write(bytes);
            } catch (e) {
                console.warn('Terminal SSE decode error', e);
            }
        };
        es.onerror = () => {
            es.close();
            _terminalEventSource = null;
            try {
                term.writeln('');
                term.write('\r\n[终端流连接断开或出错]\r\n');
            } catch (e) {}
        };
    }
    setTimeout(connectStream, 180);
}

function closeTerminalModal() {
    const modal = document.getElementById('modal-terminal');
    const container = document.getElementById('terminal-container');
    if (_terminalResizeObserver && container) {
        _terminalResizeObserver.disconnect();
        _terminalResizeObserver = null;
    }
    _terminalFitAddon = null;
    if (_terminalEventSource) {
        _terminalEventSource.close();
        _terminalEventSource = null;
    }
    if (_terminalXterm) {
        _terminalXterm.dispose();
        _terminalXterm = null;
    }
    if (_terminalSessionId && _terminalDeviceId) {
        fetch(`${API}/devices/${_terminalDeviceId}/terminal/close`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: _terminalSessionId }),
        }).catch(() => {});
    }
    _terminalSessionId = null;
    _terminalDeviceId = null;
    window._terminalDoFit = null;
    if (container) container.innerHTML = '';
    if (modal) modal.classList.remove('show');
}

document.getElementById('btn-terminal-close')?.addEventListener('click', closeTerminalModal);

// 终端弹窗：拖拽、缩放、最大化/最小化、复制粘贴
(function initTerminalWindow() {
    const win = document.getElementById('terminal-window');
    const titlebar = document.getElementById('terminal-titlebar');
    const bodyEl = document.getElementById('terminal-body');
    const btnMin = document.getElementById('btn-terminal-min');
    const btnMax = document.getElementById('btn-terminal-max');
    const btnCopy = document.getElementById('btn-terminal-copy');
    const btnPaste = document.getElementById('btn-terminal-paste');
    const resizeE = document.getElementById('terminal-resize-e');
    const resizeS = document.getElementById('terminal-resize-s');
    const resizeSE = document.getElementById('terminal-resize-se');

    if (!win || !titlebar) return;

    const defaultWidth = 800;
    const defaultHeight = 520;

    function getWinRect() {
        const style = win.style;
        let w = parseInt(style.width, 10) || defaultWidth;
        let h = parseInt(style.height, 10) || defaultHeight;
        let left = style.left ? parseInt(style.left, 10) : NaN;
        let top = style.top ? parseInt(style.top, 10) : NaN;
        if (win.offsetWidth) w = win.offsetWidth;
        if (win.offsetHeight) h = win.offsetHeight;
        return { left: isNaN(left) ? null : left, top: isNaN(top) ? null : top, width: w, height: h };
    }

    function setWinRect(r) {
        if (r.left != null) win.style.left = r.left + 'px';
        if (r.top != null) win.style.top = r.top + 'px';
        if (r.width != null) win.style.width = Math.max(400, r.width) + 'px';
        if (r.height != null) win.style.height = Math.max(280, r.height) + 'px';
        if (r.left != null || r.top != null) win.style.transform = 'none';
    }

    titlebar.addEventListener('mousedown', function(e) {
        if (e.target.closest('button')) return;
        e.preventDefault();
        const startX = e.clientX;
        const startY = e.clientY;
        const rect = getWinRect();
        const startLeft = rect.left != null ? rect.left : (window.innerWidth / 2 - win.offsetWidth / 2);
        const startTop = rect.top != null ? rect.top : (window.innerHeight / 2 - win.offsetHeight / 2);
        setWinRect({ left: startLeft, top: startTop, width: rect.width, height: rect.height });
        function move(e2) {
            const dx = e2.clientX - startX;
            const dy = e2.clientY - startY;
            setWinRect({ left: startLeft + dx, top: startTop + dy, width: rect.width, height: rect.height });
        }
        function up() {
            document.removeEventListener('mousemove', move);
            document.removeEventListener('mouseup', up);
        }
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
    });

    function startResize(edge, e) {
        e.preventDefault();
        const rect = getWinRect();
        const startX = e.clientX;
        const startY = e.clientY;
        const startW = rect.width;
        const startH = rect.height;
        const startLeft = rect.left;
        const startTop = rect.top;
        function move(e2) {
            const dx = e2.clientX - startX;
            const dy = e2.clientY - startY;
            let w = startW, h = startH;
            if (edge === 'e' || edge === 'se') w = Math.max(400, startW + dx);
            if (edge === 's' || edge === 'se') h = Math.max(280, startH + dy);
            setWinRect({ left: startLeft, top: startTop, width: w, height: h });
        }
        function up() {
            document.removeEventListener('mousemove', move);
            document.removeEventListener('mouseup', up);
            if (window._terminalDoFit) window._terminalDoFit();
        }
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
    }
    resizeE?.addEventListener('mousedown', (e) => startResize('e', e));
    resizeS?.addEventListener('mousedown', (e) => startResize('s', e));
    resizeSE?.addEventListener('mousedown', (e) => startResize('se', e));

    btnMin?.addEventListener('click', () => {
        win.classList.toggle('terminal-minimized');
        btnMin.textContent = win.classList.contains('terminal-minimized') ? '还原' : '最小化';
    });

    btnMax?.addEventListener('click', () => {
        win.classList.toggle('terminal-maximized');
        btnMax.textContent = win.classList.contains('terminal-maximized') ? '还原' : '最大化';
        setTimeout(() => { if (window._terminalDoFit) window._terminalDoFit(); }, 150);
    });

    btnCopy?.addEventListener('click', () => {
        if (!_terminalXterm) return;
        const sel = typeof _terminalXterm.getSelection === 'function' ? _terminalXterm.getSelection() : '';
        if (sel && navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(sel).then(() => {
                if (typeof toast === 'function') toast('已复制到剪贴板', 'success');
            }).catch(() => {});
        }
    });

    btnPaste?.addEventListener('click', () => {
        if (!_terminalXterm || !_terminalSessionId || !_terminalDeviceId) return;
        if (navigator.clipboard && navigator.clipboard.readText) {
            navigator.clipboard.readText().then((text) => {
                if (!text) return;
                _terminalXterm.write(text);
                fetch(`${API}/devices/${_terminalDeviceId}/terminal/send`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: _terminalSessionId, data: text }),
                }).catch(() => {});
            }).catch(() => {});
        }
    });
})();

async function backupOne(deviceId, btnEl) {
    if (btnEl) { btnEl.disabled = true; btnEl.textContent = '备份中...'; }
    try {
        const res = await fetch(`${API}/backup/run/device/${deviceId}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            toast(data.message || '已加入备份队列', 'success');
            // 单台备份成功启动后，自动跳转到「执行备份」页签查看任务进度
            try { showTab('backup'); } catch (_) {}
            loadLogs();
            loadBackupStatus();
            pollBackupStatus();
        } else {
            toast(data.error || '备份失败', 'error');
        }
    } finally {
        if (btnEl) { btnEl.disabled = false; btnEl.textContent = '备份'; }
    }
}
async function testConn(deviceId, btnEl) {
    if (btnEl) { btnEl.disabled = true; btnEl.textContent = '测试中...'; }
    try {
        const res = await fetch(`${API}/backup/test/${deviceId}`, { method: 'POST' });
        const data = await res.json();
        alert(data.ok ? '✓ ' + data.message : '✗ ' + data.message);
    } finally {
        if (btnEl) { btnEl.disabled = false; btnEl.textContent = '测试'; }
    }
}
async function showHistory(deviceId) {
    const res = await fetch(`${API}/devices/${deviceId}/history?page=1&per_page=10`);
    const data = await res.json();
    const tz = data.timezone || 'Asia/Shanghai';
    const tbody = document.getElementById('history-list');
    document.getElementById('modal-history-title').textContent = (data.hostname || '') + ' - 备份历史';
    tbody.innerHTML = (data.items || []).map(l => {
        const time = l.created_at ? formatInTimezone(l.created_at, tz) : '-';
        const link = l.config_path ? `<a href="${API}/configs/${l.config_path}" target="_blank">查看</a>` : '-';
        return `<tr>
            <td>${time}</td>
            <td class="${l.status === 'OK' ? 'status-ok' : 'status-fail'}">${l.status}</td>
            <td>${l.duration_seconds != null ? l.duration_seconds + 's' : '-'}</td>
            <td>${link}</td>
        </tr>`;
    }).join('') || '<tr><td colspan="4">暂无记录</td></tr>';
    document.getElementById('modal-history').classList.add('show');
}
document.getElementById('btn-history-close')?.addEventListener('click', () => {
    document.getElementById('modal-history').classList.remove('show');
});

// 已移除「仅启用」过滤控件，默认显示全部设备

// 添加/编辑设备
let editingId = null;
async function openDeviceModal(id) {
    editingId = id;
    document.getElementById('modal-device-title').textContent = id ? '编辑设备' : '添加设备';
    document.getElementById('device-ip').value = '';
    document.getElementById('device-hostname').value = '';
    // 打开弹窗前，确保设备类型下拉选项已从后端加载
    try {
        if (!window.__deviceTypesLoaded) {
            // 同步请求一次列表（下方真正填充值仍走异步逻辑）
            // 若失败则保持 HTML 中的默认选项
            refreshDeviceTypeOptions().catch(() => {});
        }
    } catch (e) {}
    // 默认设备类型改为 Cisco（与数据库中规范化后的值一致）
    const typeSel = document.getElementById('device-type');
    if (typeSel) {
        typeSel.value = 'Cisco';
    }
    const connSel = document.getElementById('device-connection-type');
    if (id) {
        // 编辑时保持原有连接方式，由后续 fetch 填充
        if (connSel) connSel.value = '';
    } else {
        // 新增设备时默认选用全局连接方式（如全局为 SSH，这里也自动选 SSH）
        const globalConn = (typeof window !== 'undefined' && window.DEFAULT_CONN_TYPE) ? String(window.DEFAULT_CONN_TYPE).toUpperCase() : '';
        if (connSel) connSel.value = (globalConn === 'SSH' || globalConn === 'TELNET') ? globalConn : '';
    }
    toggleDevicePortRows();
    document.getElementById('device-enabled').checked = true;
    document.getElementById('device-username').value = '';
    document.getElementById('device-password').value = '';
    const groupEl = document.getElementById('device-group');
    const sshPortEl = document.getElementById('device-ssh-port');
    const telnetPortEl = document.getElementById('device-telnet-port');
    if (groupEl) groupEl.value = '';
    if (sshPortEl) sshPortEl.value = '';
    if (telnetPortEl) telnetPortEl.value = '';
    if (id) {
        try {
            const r = await fetch(`${API}/devices/${id}?_t=${Date.now()}`);
            const d = await r.json();
            if (!r.ok) {
                toast(d.error || '加载设备失败', 'error');
                return;
            }
            document.getElementById('device-ip').value = d.ip || '';
            document.getElementById('device-hostname').value = d.hostname || '';
            document.getElementById('device-type').value = d.device_type || 'Cisco';
            if (groupEl) groupEl.value = d.group || '';
            document.getElementById('device-connection-type').value = d.connection_type || '';
            if (sshPortEl) sshPortEl.value = (d.ssh_port != null && d.ssh_port !== '') ? String(d.ssh_port) : '';
            if (telnetPortEl) telnetPortEl.value = (d.telnet_port != null && d.telnet_port !== '') ? String(d.telnet_port) : '';
            document.getElementById('device-enabled').checked = d.enabled;
            document.getElementById('device-username').value = d.username || '';
            document.getElementById('device-password').value = '';
        } catch (e) {
            toast('加载设备失败，请稍后重试', 'error');
            return;
        }
    }
    toggleDevicePortRows();
    document.getElementById('modal-device').classList.add('show');
}

function toggleDevicePortRows() {
    const conn = (document.getElementById('device-connection-type')?.value || '').trim().toUpperCase();
    const sshRow = document.getElementById('device-ssh-port-row');
    const telnetRow = document.getElementById('device-telnet-port-row');
    if (sshRow) sshRow.style.display = conn === 'TELNET' ? 'none' : '';
    if (telnetRow) telnetRow.style.display = conn === 'SSH' ? 'none' : '';
}

function editDevice(id) { openDeviceModal(id); }
document.getElementById('btn-add-device').addEventListener('click', () => openDeviceModal(null));
document.getElementById('device-connection-type')?.addEventListener('change', toggleDevicePortRows);

// 维护窗口弹窗
let _maintenanceDeviceId = null;
async function openMaintenanceModal(id, hostname) {
    _maintenanceDeviceId = id;
    const titleEl = document.getElementById('modal-maintenance-title');
    if (titleEl) titleEl.textContent = '维护' + (hostname ? ' - ' + hostname : '');
    document.getElementById('maintenance-start').value = '';
    document.getElementById('maintenance-end').value = '';
    try {
        const r = await fetch(`${API}/devices/${id}?_t=${Date.now()}`);
        const d = await r.json();
        if (r.ok) {
            document.getElementById('maintenance-start').value = d.maintenance_start || '';
            document.getElementById('maintenance-end').value = d.maintenance_end || '';
        }
    } catch (_) {}
    document.getElementById('modal-maintenance').classList.add('show');
}
document.getElementById('btn-maintenance-cancel')?.addEventListener('click', () => {
    document.getElementById('modal-maintenance').classList.remove('show');
    _maintenanceDeviceId = null;
});
document.getElementById('btn-maintenance-save')?.addEventListener('click', async () => {
    if (_maintenanceDeviceId == null) return;
    const start = (document.getElementById('maintenance-start')?.value || '').trim().slice(0, 8) || null;
    const end = (document.getElementById('maintenance-end')?.value || '').trim().slice(0, 8) || null;
    try {
        const res = await fetch(`${API}/devices/batch-update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: [_maintenanceDeviceId], maintenance_start: start, maintenance_end: end })
        });
        const data = await res.json();
        if (res.ok && data.ok) {
            document.getElementById('modal-maintenance').classList.remove('show');
            _maintenanceDeviceId = null;
            loadDevices(true);
            if (typeof toast === 'function') toast('维护已保存', 'success');
        } else {
            alert(data.error || '保存失败');
        }
    } catch (e) {
        alert('网络错误，保存失败');
    }
});

document.getElementById('btn-modal-cancel').addEventListener('click', () => {
    document.getElementById('modal-device').classList.remove('show');
});

document.getElementById('btn-modal-save').addEventListener('click', async () => {
    const sshPortRaw = (document.getElementById('device-ssh-port')?.value || '').trim();
    const telnetPortRaw = (document.getElementById('device-telnet-port')?.value || '').trim();
    let sshPort = null;
    if (sshPortRaw) {
        const n = parseInt(sshPortRaw, 10);
        if (!isNaN(n) && n >= 1 && n <= 65535) sshPort = n;
    }
    let telnetPort = null;
    if (telnetPortRaw) {
        const n = parseInt(telnetPortRaw, 10);
        if (!isNaN(n) && n >= 1 && n <= 65535) telnetPort = n;
    }
    const connectionTypeSelect = document.getElementById('device-connection-type');
    const connectionTypeVal = connectionTypeSelect ? (connectionTypeSelect.value || '').trim() : '';
    // 确保 connection_type 字段始终被发送（空字符串转为 null，非空则发送原值）
    const connectionTypeToSend = connectionTypeVal ? connectionTypeVal : null;
    const data = {
        ip: document.getElementById('device-ip').value.trim(),
        hostname: document.getElementById('device-hostname').value.trim(),
        device_type: document.getElementById('device-type').value,
        group: document.getElementById('device-group')?.value.trim() || null,
        connection_type: connectionTypeToSend,
        ssh_port: sshPort,
        telnet_port: telnetPort,
        enabled: document.getElementById('device-enabled').checked,
        username: document.getElementById('device-username').value.trim() || null,
        password: document.getElementById('device-password').value || null
    };
    if (!data.ip || !data.hostname) {
        alert('请填写 IP 和主机名');
        return;
    }
    const idForPut = editingId != null && editingId !== '' && !isNaN(Number(editingId)) ? Number(editingId) : null;
    // 单设备编辑使用 PUT /api/devices/:id，保证所有字段正确落库
    const url = idForPut ? `${API}/devices/${idForPut}` : `${API}/devices`;
    const method = idForPut ? 'PUT' : 'POST';
    const payload = data;
    try {
        const res = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            document.getElementById('modal-device').classList.remove('show');
            loadDevices(true);
            loadConfigs();
            if (typeof toast === 'function') toast('保存成功', 'success');
        } else {
            let msg = '保存失败';
            try {
                const err = await res.json();
                if (err && err.error) msg = err.error;
            } catch (_) {}
            alert(msg);
        }
    } catch (e) {
        alert('网络错误，保存失败');
    }
});


// 导入
// 设备分组：弹窗内展示已有分组 + 新建
async function refreshDeviceGroupDatalist() {
    const listEl = document.getElementById('device-group-list');
    if (!listEl) return;
    try {
        const res = await fetch(`${API}/device-groups`);
        const data = await res.json();
        const groups = data.groups || [];
        listEl.innerHTML = groups.map(g => `<option value="${escapeHtml(g)}">`).join('');
    } catch (_) {}
}

async function loadDeviceGroupModalList() {
    const container = document.getElementById('device-group-list-container');
    const emptyHint = document.getElementById('device-group-list-empty');
    if (!container) return;
    try {
        const res = await fetch(`${API}/device-groups`);
        const data = await res.json();
        const groups = data.groups || [];
        emptyHint.style.display = groups.length ? 'none' : 'block';
        container.innerHTML = groups.map(g => {
            const safe = escapeHtml(g);
            return `<span class="device-group-tag" data-group-name="${safe}">${safe}<button type="button" class="btn btn-sm btn-tag-delete" data-group-name="${safe}" title="从预定义列表中移除">删除</button></span>`;
        }).join('');
        container.querySelectorAll('.btn-tag-delete').forEach(btn => {
            btn.addEventListener('click', async () => {
                const name = btn.getAttribute('data-group-name');
                if (!name) return;
                const enc = encodeURIComponent(name);
                try {
                    const r = await fetch(`${API}/device-groups/${enc}`, { method: 'DELETE' });
                    const d = await r.json();
                    if (r.ok && d.ok) {
                        toast('已移除该分组', 'success');
                        loadDeviceGroupModalList();
                        refreshDeviceGroupDatalist();
                        loadDevices();
                    } else {
                        toast(d.error || '操作失败', 'error');
                    }
                } catch (e) {
                    toast('操作失败', 'error');
                }
            });
        });
    } catch (_) {
        emptyHint.style.display = 'block';
        container.innerHTML = '';
    }
}

document.getElementById('btn-create-device-group')?.addEventListener('click', () => {
    document.getElementById('device-group-name').value = '';
    loadDeviceGroupModalList();
    document.getElementById('modal-device-group').classList.add('show');
});
document.getElementById('btn-device-group-cancel')?.addEventListener('click', () => {
    document.getElementById('modal-device-group').classList.remove('show');
});
document.getElementById('btn-device-group-create')?.addEventListener('click', async () => {
    const name = (document.getElementById('device-group-name').value || '').trim();
    if (!name) {
        toast('请输入分组名称', 'warn');
        return;
    }
    try {
        const res = await fetch(`${API}/device-groups`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        const data = await res.json();
        if (res.ok && data.ok) {
            toast('分组已创建', 'success');
            document.getElementById('device-group-name').value = '';
            loadDeviceGroupModalList();
            refreshDeviceGroupDatalist();
            loadDevices();
        } else {
            toast(data.error || '创建失败', 'error');
        }
    } catch (e) {
        toast('创建失败，请稍后重试', 'error');
    }
});

document.getElementById('btn-discover')?.addEventListener('click', () => {
    // 设备发现功能已迁移到「系统设置 → 自动发现」，此处点击时切换到对应页签
    try {
        showTab('settings');
        const navBtn = document.querySelector('.settings-nav-item[data-settings-section="discovery"]');
        navBtn?.click();
    } catch (e) {}
});

document.getElementById('btn-import').addEventListener('click', () => {
    document.getElementById('import-text').value = '';
    document.getElementById('modal-import').classList.add('show');
});
document.getElementById('btn-import-cancel').addEventListener('click', () => {
    document.getElementById('modal-import').classList.remove('show');
});
document.getElementById('btn-import-submit').addEventListener('click', async () => {
    const text = document.getElementById('import-text').value;
    const res = await fetch(`${API}/devices/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: text
    });
    const r = await res.json();
    document.getElementById('modal-import').classList.remove('show');
    toast(`导入 ${r.imported} 台设备`, 'success');
    loadDevices();
    loadDashboard();
    loadConfigs();
});

// 备份任务列表展示（支持分页 + 搜索）
let backupJobsPage = 1;
let backupJobsPerPage = 50;
function renderBackupJobs(d) {
    const list = d.jobs || [];
    const current = d.current || null;
    const tbody = document.getElementById('backup-jobs-list');
    const hint = document.getElementById('backup-jobs-hint');
    const perPageEl = document.getElementById('backup-jobs-per-page');
    const pagInfoEl = document.getElementById('backup-jobs-pagination');
    const searchEl = document.getElementById('backup-jobs-search');
    if (!tbody) return;

    const tz = d.timezone || 'Asia/Shanghai';
    const fmtTime = (s) => (s ? formatInTimezone(s, tz) : '-');
    const fmtDuration = (startIso, endIso) => {
        if (!startIso || !endIso) return '-';
        const a = new Date(startIso);
        const b = new Date(endIso);
        const sec = Math.round((b - a) / 1000);
        if (sec < 60) return sec + ' 秒';
        const m = Math.floor(sec / 60);
        const s = sec % 60;
        return m + ' 分' + (s ? ' ' + s + ' 秒' : '');
    };
    const fmtRunType = (runType) => (runType === 'scheduled' ? '自动执行' : '手动执行');
    const fmtExecutor = (j) => (j.executor || (j.run_type === 'scheduled' ? 'System' : '-'));
    const fmtSingleDevice = (j) => {
        const sd = j.single_device;
        if (!sd) return '';
        const name = (sd.hostname || '').trim();
        const ip = (sd.ip || '').trim();
        if (name && ip) return `（单台：${escapeHtml(name)} ${escapeHtml(ip)}）`;
        if (name) return `（单台：${escapeHtml(name)}）`;
        if (ip) return `（单台：${escapeHtml(ip)}）`;
        return '（单台备份）';
    };

    // 确定每页条数（默认 50）
    if (perPageEl) {
        const v = parseInt(perPageEl.value, 10);
        backupJobsPerPage = (!isNaN(v) && v > 0) ? v : 50;
    } else {
        backupJobsPerPage = 50;
    }

    const searchKw = (searchEl && searchEl.value) ? searchEl.value.trim().toLowerCase() : '';

    // 组成一个扁平列表（当前任务 + 历史任务），当前运行任务始终排在最前
    let flat = [];
    if (current && current.status === 'running') {
        flat.push({ __current: true, data: current });
    }
    list.forEach(j => {
        if (j.status === 'running' && current && j.id === current.id) return;
        flat.push({ __current: false, data: j });
    });

    if (searchKw) {
        flat = flat.filter(item => {
            const j = item.data;
            const executor = (fmtExecutor(j) || '').toLowerCase();
            const runType = fmtRunType(j.run_type) || '';
            return executor.includes(searchKw) || runType.toLowerCase().includes(searchKw);
        });
    }

    const total = flat.length;
    const totalPages = Math.max(1, Math.ceil(total / backupJobsPerPage));
    if (backupJobsPage > totalPages) backupJobsPage = totalPages;
    if (backupJobsPage < 1) backupJobsPage = 1;
    const start = (backupJobsPage - 1) * backupJobsPerPage;
    const end = start + backupJobsPerPage;
    const pageItems = flat.slice(start, end);

    const rows = pageItems.map(item => {
        const j = item.data;
        if (item.__current) {
            return `
                <tr class="backup-job-running">
                    <td>${fmtTime(j.start_time)}</td>
                    <td>${fmtRunType(j.run_type)}${fmtSingleDevice(j)}</td>
                    <td>${escapeHtml(fmtExecutor(j))}</td>
                    <td><span class="badge badge-warn">进行中</span></td>
                    <td>${j.done || 0} / ${j.total || 0}</td>
                    <td class="status-ok">${j.ok || 0}</td>
                    <td class="status-fail">${j.fail || 0}</td>
                    <td>-</td>
                </tr>
            `;
        }
        const statusBadge = j.status === 'completed'
            ? '<span class="badge badge-ok">已完成</span>'
            : (j.status === 'running' ? '<span class="badge badge-warn">进行中</span>' : '<span class="badge">' + escapeHtml(j.status || '') + '</span>');
        const progress = j.status === 'running'
            ? (j.done || 0) + ' / ' + (j.total || 0)
            : (j.total || 0) + ' 台';
        const duration = j.end_time ? fmtDuration(j.start_time, j.end_time) : '-';
        return `
            <tr>
                <td>${fmtTime(j.start_time)}</td>
                <td>${fmtRunType(j.run_type)}${fmtSingleDevice(j)}</td>
                <td>${escapeHtml(fmtExecutor(j))}</td>
                <td>${statusBadge}</td>
                <td>${progress}</td>
                <td class="status-ok">${j.ok ?? '-'}</td>
                <td class="status-fail">${j.fail ?? '-'}</td>
                <td>${duration}</td>
            </tr>
        `;
    });

    tbody.innerHTML = rows.length ? rows.join('') : '';
    if (hint) hint.style.display = total > 0 ? 'none' : 'block';

    if (pagInfoEl) {
        if (total === 0) {
            pagInfoEl.innerHTML = '';
        } else {
            pagInfoEl.innerHTML = `
                <span>共 ${total} 条</span>
                <button type="button" class="btn btn-secondary btn-sm" ${backupJobsPage <= 1 ? 'disabled' : ''} data-backup-prev>上一页</button>
                <span>${backupJobsPage} / ${totalPages}</span>
                <button type="button" class="btn btn-secondary btn-sm" ${backupJobsPage >= totalPages ? 'disabled' : ''} data-backup-next>下一页</button>
            `;
            pagInfoEl.querySelector('[data-backup-prev]')?.addEventListener('click', () => {
                if (backupJobsPage > 1) {
                    backupJobsPage--;
                    renderBackupJobs(d);
                }
            });
            pagInfoEl.querySelector('[data-backup-next]')?.addEventListener('click', () => {
                if (backupJobsPage < totalPages) {
                    backupJobsPage++;
                    renderBackupJobs(d);
                }
            });
        }
    }
}

function updateBackupProgress(d) {
    const wrap = document.getElementById('backup-status');
    const fillEl = document.getElementById('backup-progress-fill');
    const textEl = document.getElementById('backup-progress-text');
    const pctEl = document.getElementById('backup-progress-pct');
    if (!wrap || !fillEl || !textEl || !pctEl) return;
    const runningJob = d.running && d.current ? d.current : (d.jobs && d.jobs[0] && d.jobs[0].status === 'running' ? d.jobs[0] : null);
    const completedJob = !d.running && d.jobs && d.jobs[0] && d.jobs[0].status === 'completed' ? d.jobs[0] : null;
    const job = runningJob || completedJob;
    const total = job ? (job.total || 1) : 0;
    const done = job ? (job.done || 0) : 0;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    const isRunning = !!d.running || (runningJob && runningJob.status === 'running');
    wrap.classList.remove('running', 'idle', 'completed');
    if (isRunning) {
        wrap.classList.add('running');
        fillEl.style.width = pct + '%';
        textEl.textContent = `备份进行中 ${done}/${total} 台`;
        pctEl.textContent = pct + '%';
        pctEl.style.display = '';
    } else if (completedJob && done > 0) {
        wrap.classList.add('completed');
        fillEl.style.width = '100%';
        textEl.textContent = `已完成 ${(completedJob.ok || 0)} 成功 / ${(completedJob.fail || 0)} 失败`;
        pctEl.textContent = '100%';
        pctEl.style.display = '';
    } else {
        wrap.classList.add('idle');
        fillEl.style.width = '0%';
        textEl.textContent = '就绪';
        pctEl.textContent = '';
        pctEl.style.display = 'none';
    }
}

let _lastBackupJobsData = null;
async function loadBackupStatus() {
    const res = await fetch(`${API}/backup/status`);
    const d = await res.json();
    _lastBackupJobsData = d;
    const btn = document.getElementById('btn-run-backup');
    updateBackupProgress(d);
    if (btn) {
        const canRun = (d.can_run_backup === undefined) ? true : !!d.can_run_backup;
        const isRunning = !!d.running || (d.jobs && d.jobs[0] && d.jobs[0].status === 'running');
        btn.disabled = isRunning || !canRun;
    }
    renderBackupJobs(d);
    if (!!d.running || (d.jobs && d.jobs[0] && d.jobs[0].status === 'running')) {
        pollBackupStatus();
    }
}

function applyBackupJobsSearch() {
    backupJobsPage = 1;
    if (_lastBackupJobsData) renderBackupJobs(_lastBackupJobsData);
}
document.getElementById('backup-jobs-search')?.addEventListener('input', debounce(applyBackupJobsSearch, 350));
document.getElementById('backup-jobs-search')?.addEventListener('keypress', e => { if (e.key === 'Enter') applyBackupJobsSearch(); });
document.getElementById('btn-backup-jobs-search')?.addEventListener('click', applyBackupJobsSearch);
document.getElementById('backup-jobs-per-page')?.addEventListener('change', () => { backupJobsPage = 1; if (_lastBackupJobsData) renderBackupJobs(_lastBackupJobsData); });

// 备份
document.getElementById('btn-run-backup').addEventListener('click', async () => {
    const btn = document.getElementById('btn-run-backup');
    if (btn.disabled) return;

    // 与首页一致：若今天已有成功备份，先弹出确认提示
    try {
        const todayOk = window.__dashboard_today_ok || 0;
        if (todayOk > 0) {
            const ok = window.confirm('今天已经有成功备份记录，你确定还要手动执行全量备份吗？');
            if (!ok) return;
        }
    } catch (_) {}

    btn.disabled = true;
    updateBackupProgress({ running: true, current: { total: 1, done: 0 }, jobs: [] });
    const res = await fetch(`${API}/backup/run`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
        // 已经在执行备份面板，无需切换，只需刷新任务列表和进度
        loadBackupStatus();
        pollBackupStatus();
    } else {
        updateBackupProgress({});
        const wrap = document.getElementById('backup-status');
        const textEl = document.getElementById('backup-progress-text');
        if (textEl) textEl.textContent = data.error || '启动失败';
        btn.disabled = false;
    }
});

let _backupPollTimer = null;

function pollBackupStatus() {
    if (_backupPollTimer) return;
    const btn = document.getElementById('btn-run-backup');
    const tick = () => {
        fetch(`${API}/backup/status`).then(r => r.json()).then(d => {
            _lastBackupJobsData = d;
            renderBackupJobs(d);
            updateBackupProgress(d);
            const isRunning = !!d.running || (d.jobs && d.jobs[0] && d.jobs[0].status === 'running');
            if (isRunning) {
                _backupPollTimer = setTimeout(tick, 1200);
            } else {
                _backupPollTimer = null;
                if (btn) {
                    const canRun = (d.can_run_backup === undefined) ? true : !!d.can_run_backup;
                    btn.disabled = !canRun;
                }
                loadLogs();
                loadDashboard();
            }
        }).catch(() => {
            _backupPollTimer = null;
            if (btn) btn.disabled = false;
            updateBackupProgress({});
        });
    };
    tick();
}

// 日志
let logPage = 1;
let logPerPage = 50;
let logSortBy = 'created_at';
let logSortDir = 'desc';
async function loadLogs() {
    const search = (document.getElementById('log-search') || document.getElementById('log-hostname'))?.value?.trim() || '';
    const perPageEl = document.getElementById('log-per-page');
    if (perPageEl) logPerPage = parseInt(perPageEl.value, 10) || 50;
    let url = `${API}/logs?page=${logPage}&per_page=${logPerPage}&sort_by=${encodeURIComponent(logSortBy)}&sort_dir=${encodeURIComponent(logSortDir)}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    const res = await fetch(url);
    const data = await res.json();
    const tz = data.timezone || 'Asia/Shanghai';
    updateLogSortUI(data.sort_by || logSortBy, data.sort_dir || logSortDir);
    const tbody = document.getElementById('log-list');
    tbody.innerHTML = data.items.map(l => {
        let configCell = '-';
        if (l.config_path) {
            const safeName = l.config_path.split('/').pop() || l.config_path;
            configCell = `<a href="${API}/configs/${l.config_path}" target="_blank" rel="noopener">${escapeHtml(safeName)}</a>`;
        }
        const isOk = l.status === 'OK';
        const statusText = l.status || '';
        // 仅在失败时使用 title 显示完整原因，例如：Fail: 未知设备类型或驱动加载失败
        const fullReason = (!isOk && l.message) ? `${statusText}: ${l.message}` : '';
        const statusTitleAttr = fullReason ? ` title="${escapeHtml(fullReason)}"` : '';
        return `
        <tr>
            <td>${l.created_at ? formatInTimezone(l.created_at, tz) : '-'}</td>
            <td>${escapeHtml(l.hostname)}</td>
            <td>${escapeHtml(l.ip)}</td>
            <td>${escapeHtml(l.device_type)}</td>
            <td class="${isOk ? 'status-ok' : 'status-fail'}"><span${statusTitleAttr}>${escapeHtml(statusText)}</span></td>
            <td>${l.duration_seconds != null ? l.duration_seconds + 's' : '-'}</td>
            <td>${configCell}</td>
        </tr>
    `;
    }).join('');
    const pag = document.getElementById('log-pagination');
    const totalPages = Math.ceil(data.total / data.per_page) || 1;
    if (pag) {
        pag.innerHTML = `
            <span>共 ${data.total} 条</span>
            <button type="button" class="btn btn-secondary btn-sm" ${logPage <= 1 ? 'disabled' : ''} data-log-prev>上一页</button>
            <span>${logPage} / ${totalPages}</span>
            <button type="button" class="btn btn-secondary btn-sm" ${logPage >= totalPages ? 'disabled' : ''} data-log-next>下一页</button>
        `;
        pag.querySelector('[data-log-prev]')?.addEventListener('click', () => { if (logPage > 1) { logPage--; loadLogs(); } });
        pag.querySelector('[data-log-next]')?.addEventListener('click', () => { if (logPage < totalPages) { logPage++; loadLogs(); } });
    }
}
function setLogSort(by) {
    if (logSortBy === by) logSortDir = logSortDir === 'asc' ? 'desc' : 'asc';
    else { logSortBy = by; logSortDir = by === 'status' ? 'asc' : 'desc'; }
    logPage = 1;
    loadLogs();
}
function updateLogSortUI(currentBy, currentDir) {
    logSortBy = currentBy || logSortBy || 'created_at';
    logSortDir = (currentDir === 'desc') ? 'desc' : 'asc';
    document.querySelectorAll('.log-sort-btn').forEach(btn => {
        const by = btn.getAttribute('data-sort-by');
        btn.classList.remove('active', 'dir-asc', 'dir-desc');
        if (by === logSortBy) {
            btn.classList.add('active');
            btn.classList.add(logSortDir === 'desc' ? 'dir-desc' : 'dir-asc');
        }
    });
}
document.getElementById('btn-refresh-logs').addEventListener('click', () => { logPage = 1; loadLogs(); });
const logSearchEl = document.getElementById('log-search') || document.getElementById('log-hostname');
if (logSearchEl) {
    logSearchEl.addEventListener('keypress', e => { if (e.key === 'Enter') loadLogs(); });
    logSearchEl.addEventListener('input', debounce(() => { logPage = 1; loadLogs(); }, 350));
}
document.getElementById('log-per-page')?.addEventListener('change', () => { logPage = 1; loadLogs(); });
document.querySelectorAll('.log-sort-btn').forEach(btn => {
    btn.addEventListener('click', () => setLogSort(btn.getAttribute('data-sort-by')));
});

// 已备份配置：按设备列表展示（分页，默认每页 50）
let configPage = 1;
let configPerPage = 50;
let configSortBy = 'hostname';
let configSortDir = 'asc';

async function loadConfigs() {
    const search = document.getElementById('config-filter-search')?.value?.trim() || '';
    let url = `${API}/configs/devices?page=${configPage}&per_page=${configPerPage}`;
    if (search) url += '&search=' + encodeURIComponent(search);
    url += `&sort_by=${encodeURIComponent(configSortBy)}&sort_dir=${encodeURIComponent(configSortDir)}`;
    const res = await fetch(url);
    if (res.status === 401) {
        window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname || '/');
        return;
    }
    const data = await res.json();
    const devices = data.devices || [];
    const total = data.total || 0;
    const perPage = data.per_page || configPerPage;
    const page = data.page || configPage;
    const totalPages = Math.ceil(total / perPage) || 1;

    const tbody = document.getElementById('config-device-list');
    if (!tbody) return;
    const canDeleteBackups = (data.can_delete_backups === undefined) ? true : !!data.can_delete_backups;
    tbody.innerHTML = devices.map(dev => {
        const zeroFiles = (dev.file_count || 0) === 0;
        const rowClass = zeroFiles ? 'config-device-zero-files' : '';
        const p = (dev.prefix || '');
        const h = (dev.hostname || '');
        return `<tr${rowClass ? ' class="' + rowClass + '"' : ''}>
            <td>${escapeHtml(dev.display_hostname || dev.hostname)}</td>
            <td>${escapeHtml(dev.ip || '')}</td>
            <td>${escapeHtml(dev.device_type || '')}</td>
            <td>${dev.file_count || 0}</td>
            <td class="col-actions-cell">
                <div class="device-actions">
                    <a href="#" class="btn btn-sm btn-history" data-config-device data-prefix="${escapeHtml(p)}" data-hostname="${escapeHtml(h)}">历史备份</a>
                    <a href="#" class="btn btn-sm btn-compare" data-config-device data-prefix="${escapeHtml(p)}" data-hostname="${escapeHtml(h)}">对比配置</a>
                    <button type="button"
                        class="btn btn-sm btn-delete"
                        data-action="delete-backups"
                        data-prefix="${encodeURIComponent(dev.prefix)}"
                        data-hostname="${encodeURIComponent(dev.hostname)}"
                        data-hostname-raw="${escapeHtml(dev.hostname)}"
                        ${canDeleteBackups ? '' : 'disabled'}>
                        删除备份
                    </button>
                </div>
            </td>
        </tr>`;
    }).join('') || '<tr><td colspan="5">暂无备份</td></tr>';

    const infoEl = document.getElementById('config-pagination-info');
    if (infoEl) infoEl.textContent = `共 ${total} 条`;
    const prevBtn = document.getElementById('config-prev');
    const nextBtn = document.getElementById('config-next');
    const pageNumsEl = document.getElementById('config-page-nums');
    if (prevBtn) prevBtn.disabled = page <= 1;
    if (nextBtn) nextBtn.disabled = page >= totalPages;
    if (pageNumsEl) pageNumsEl.textContent = `${page} / ${totalPages}`;
    updateConfigSortUI(data.sort_by || configSortBy, data.sort_dir || configSortDir);
}

function setConfigSort(by) {
    if (!by) return;
    if (configSortBy === by) {
        configSortDir = configSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        configSortBy = by;
        configSortDir = 'asc';
    }
    configPage = 1;
    loadConfigs();
}

function updateConfigSortUI(currentBy, currentDir) {
    configSortBy = currentBy || configSortBy || 'hostname';
    configSortDir = (currentDir === 'desc') ? 'desc' : 'asc';
    document.querySelectorAll('.config-device-sort-btn').forEach(btn => {
        const by = btn.getAttribute('data-sort-by');
        btn.classList.remove('active', 'dir-asc', 'dir-desc');
        if (by === configSortBy) {
            btn.classList.add('active');
            btn.classList.add(configSortDir === 'desc' ? 'dir-desc' : 'dir-asc');
        }
    });
}

document.getElementById('config-filter-search')?.addEventListener('input', debounce(() => { configPage = 1; loadConfigs(); }, 350));
document.getElementById('config-filter-search')?.addEventListener('keypress', e => { if (e.key === 'Enter') { configPage = 1; loadConfigs(); } });
document.getElementById('btn-config-filter-search')?.addEventListener('click', () => { configPage = 1; loadConfigs(); });
document.querySelectorAll('.config-device-sort-btn').forEach(btn => {
    btn.addEventListener('click', () => setConfigSort(btn.getAttribute('data-sort-by')));
});
document.getElementById('config-prev')?.addEventListener('click', () => { configPage--; loadConfigs(); });
document.getElementById('config-next')?.addEventListener('click', () => { configPage++; loadConfigs(); });
document.getElementById('config-per-page')?.addEventListener('change', function() {
    configPerPage = parseInt(this.value, 10) || 50;
    configPage = 1;
    loadConfigs();
});

// 单设备配置面板（历史备份/对比）：从「已备份配置」点入，不跳转独立页
let _configDevicePrefix = '';
let _configDeviceHostname = '';
function openConfigDevicePanel(prefix, hostname) {
    _configDevicePrefix = prefix || '';
    _configDeviceHostname = hostname || '';
    const titleEl = document.getElementById('config-device-title');
    const currentEl = document.getElementById('config-device-current-hostname');
    if (titleEl) titleEl.textContent = _configDeviceHostname;
    if (currentEl) currentEl.textContent = _configDeviceHostname;
    try { sessionStorage.setItem('vconfig_tab', 'config-device'); } catch (_) {}
    location.hash = '#config-device/' + encodeURIComponent(_configDevicePrefix) + '/' + encodeURIComponent(_configDeviceHostname);
    showTab('config-device');
    loadConfigDevicePanelData();
}
document.body.addEventListener('click', function(e) {
    const a = e.target.closest('a[data-config-device]');
    if (!a) return;
    e.preventDefault();
    const prefix = a.getAttribute('data-prefix') || '';
    const hostname = a.getAttribute('data-hostname') || '';
    openConfigDevicePanel(prefix, hostname);
});
document.getElementById('config-device-back')?.addEventListener('click', function(e) {
    e.preventDefault();
    location.hash = '';
    try { sessionStorage.setItem('vconfig_tab', 'configs'); } catch (_) {}
    showTab('configs');
});

function loadConfigDevicePanelData() {
    const prefix = _configDevicePrefix;
    const hostname = _configDeviceHostname;
    const filesUrl = `${API}/configs/devices/${encodeURIComponent(prefix)}/${encodeURIComponent(hostname)}`;
    const basePath = `${API}/configs/${encodeURIComponent(prefix)}/${encodeURIComponent(hostname)}`;
    const listEl = document.getElementById('config-files-list');
    const loadingEl = document.getElementById('config-files-loading');
    const emptyEl = document.getElementById('config-files-empty');
    const selectA = document.getElementById('diff-file-a');
    const selectB = document.getElementById('diff-file-b');
    const diffLoading = document.getElementById('diff-loading');
    const diffResult = document.getElementById('diff-result');
    const diffHint = document.getElementById('diff-hint');
    const complianceLoading = document.getElementById('compliance-loading');
    const complianceResult = document.getElementById('compliance-result');
    const complianceEmpty = document.getElementById('compliance-empty');
    if (!listEl || !loadingEl) return;
    loadingEl.classList.remove('hidden');
    if (emptyEl) emptyEl.classList.add('hidden');
    if (complianceLoading) complianceLoading.classList.remove('hidden');
    if (complianceResult) { complianceResult.classList.add('hidden'); complianceResult.innerHTML = ''; }
    if (complianceEmpty) complianceEmpty.classList.add('hidden');
    if (diffResult) { diffResult.classList.add('hidden'); diffResult.innerHTML = ''; }
    if (diffHint) diffHint.classList.remove('hidden');
    _configDeviceFiles = [];
    fetch(filesUrl).then(r => r.json()).then(data => {
        const raw = data.files || [];
        _configDeviceFiles = raw.map(f => typeof f === 'string' ? { name: f, size: null } : { name: f.name || '', size: typeof f.size === 'number' ? f.size : null }).filter(f => f.name);
        loadingEl.classList.add('hidden');
        if (!_configDeviceFiles.length) {
            if (emptyEl) emptyEl.classList.remove('hidden');
            if (complianceLoading) complianceLoading.classList.add('hidden');
            if (complianceEmpty) complianceEmpty.classList.remove('hidden');
            if (selectA) selectA.innerHTML = '<option value="">选择版本 A</option>';
            if (selectB) selectB.innerHTML = '<option value="">选择版本 B</option>';
            return;
        }
        function renderFilesList(list) {
            if (!list.length) { listEl.innerHTML = ''; if (emptyEl) emptyEl.classList.remove('hidden'); return; }
            if (emptyEl) emptyEl.classList.add('hidden');
            listEl.innerHTML = list.map(f => {
                const path = basePath + '/' + encodeURIComponent(f.name);
                const downloadUrl = path + '?download=1';
                return `<li class="config-file-row"><a href="${path}" target="_blank" rel="noopener">${escapeHtml(f.name)}</a><span class="config-file-size"></span><button type="button" class="btn btn-sm btn-secondary config-file-download" data-url="${escapeHtml(downloadUrl)}" data-name="${escapeHtml(f.name)}" title="下载">下载</button></li>`;
            }).join('');
            listEl.querySelectorAll('.config-file-download').forEach(btn => {
                btn.addEventListener('click', function() {
                    const url = this.getAttribute('data-url');
                    fetch(url).then(r => r.blob()).then(blob => {
                        const a = document.createElement('a');
                        a.href = URL.createObjectURL(blob);
                        a.download = (this.getAttribute('data-name') || 'config.txt').endsWith('.txt') ? this.getAttribute('data-name') : this.getAttribute('data-name') + '.txt';
                        a.click();
                        URL.revokeObjectURL(a.href);
                    }).catch(() => toast('下载失败', 'error'));
                });
            });
        }
        renderFilesList(_configDeviceFiles);
        if (selectA) selectA.innerHTML = '<option value="">选择版本 A</option>' + _configDeviceFiles.map(f => `<option value="${escapeHtml(f.name)}">${escapeHtml(f.name)}</option>`).join('');
        if (selectB) selectB.innerHTML = '<option value="">选择版本 B</option>' + _configDeviceFiles.map(f => `<option value="${escapeHtml(f.name)}">${escapeHtml(f.name)}</option>`).join('');
        if (_configDeviceFiles.length >= 2) { selectA.value = _configDeviceFiles[0].name; selectB.value = _configDeviceFiles[1].name; }
        fetch(`${API}/compliance/${encodeURIComponent(prefix)}/${encodeURIComponent(hostname)}`).then(r => r.json()).then(data => {
            if (complianceLoading) complianceLoading.classList.add('hidden');
            if (!data || !data.status) { if (complianceResult) { complianceResult.innerHTML = '<p class="hint">未获取到合规检查结果。</p>'; complianceResult.classList.remove('hidden'); } return; }
            const statusMap = { ok: { label: '通过', cls: 'status-ok' }, warn: { label: '存在风险', cls: 'status-warn' }, fail: { label: '不合规', cls: 'status-fail' } };
            const overall = statusMap[data.status] || statusMap.ok;
            const rules = Array.isArray(data.rules) ? data.rules : [];
            const items = rules.map(r => { const m = statusMap[r.level] || statusMap.ok; return `<div class="compliance-rule"><span class="compliance-tag ${m.cls}">${m.label}</span><span class="compliance-text">${escapeHtml(r.message || '')}</span></div>`; }).join('') || '<p class="hint">暂无具体规则结果。</p>';
            if (complianceResult) { complianceResult.innerHTML = `<div class="compliance-overall"><span class="label">总体结果：</span><span class="value ${overall.cls}">${overall.label}</span>${data.latest_file ? '<span class="meta">（基于最近备份：' + escapeHtml(data.latest_file) + '）</span>' : ''}</div><div class="compliance-rules">${items}</div>`; complianceResult.classList.remove('hidden'); }
        }).catch(() => { if (complianceLoading) complianceLoading.classList.add('hidden'); if (complianceEmpty) complianceEmpty.classList.remove('hidden'); });
    }).catch(() => { loadingEl.classList.add('hidden'); if (emptyEl) emptyEl.classList.remove('hidden'); if (complianceLoading) complianceLoading.classList.add('hidden'); });
}
let _configDeviceFiles = [];
let _configDeviceOnlyChanged = false;
function initConfigDevicePanelHandlers() {
    const selectA = document.getElementById('diff-file-a');
    const selectB = document.getElementById('diff-file-b');
    const diffLoading = document.getElementById('diff-loading');
    const diffResult = document.getElementById('diff-result');
    const diffHint = document.getElementById('diff-hint');
    document.getElementById('btn-diff')?.addEventListener('click', function() {
        const fa = selectA?.value; const fb = selectB?.value;
        if (!fa || !fb || fa === fb) { toast('请选择两个不同的版本', 'warn'); return; }
        const fileContentBase = `${API}/configs/${encodeURIComponent(_configDevicePrefix)}/${encodeURIComponent(_configDeviceHostname)}`;
        diffHint?.classList.add('hidden');
        diffLoading?.classList.remove('hidden');
        diffResult?.classList.add('hidden');
        diffResult.innerHTML = '';
        Promise.all([
            fetch(fileContentBase + '/' + encodeURIComponent(fa)).then(r => { if (!r.ok) throw new Error(r.status); return r.text(); }),
            fetch(fileContentBase + '/' + encodeURIComponent(fb)).then(r => { if (!r.ok) throw new Error(r.status); return r.text(); })
        ]).then(([textA, textB]) => {
            const linesA = textA.split(/\r?\n/); const linesB = textB.split(/\r?\n/);
            const maxLen = Math.max(linesA.length, linesB.length);
            const left = []; const right = [];
            for (let i = 0; i < maxLen; i++) {
                const a = linesA[i] ?? ''; const b = linesB[i] ?? '';
                const changed = a !== b; const cls = changed ? 'diff-line changed' : 'diff-line';
                left.push('<div class="' + cls + '">' + (a ? escapeHtml(a) : '&nbsp;') + '</div>');
                right.push('<div class="' + cls + '">' + (b ? escapeHtml(b) : '&nbsp;') + '</div>');
            }
            diffResult.innerHTML = '<div class="diff-side-by-side"><div class="diff-pane"><div class="diff-pane-header">版本 A（较新）</div><div class="diff-pane-body">' + left.join('') + '</div></div><div class="diff-pane"><div class="diff-pane-header">版本 B（较旧）</div><div class="diff-pane-body">' + right.join('') + '</div></div>';
            diffResult.classList.remove('hidden');
            if (_configDeviceOnlyChanged) diffResult.classList.add('diff-only-changed');
            diffLoading.classList.add('hidden');
        }).catch(() => { diffResult.innerHTML = '<p class="error">加载或对比失败</p>'; diffResult.classList.remove('hidden'); diffLoading.classList.add('hidden'); });
    });
    document.getElementById('btn-diff-toggle-changed')?.addEventListener('click', function() {
        _configDeviceOnlyChanged = !_configDeviceOnlyChanged;
        this.textContent = _configDeviceOnlyChanged ? '显示全部' : '显示不同';
        if (diffResult && !diffResult.classList.contains('hidden')) diffResult.classList.toggle('diff-only-changed', _configDeviceOnlyChanged);
    });
    document.getElementById('config-files-search')?.addEventListener('input', function() {
        const kw = (this.value || '').trim().toLowerCase();
        const listEl = document.getElementById('config-files-list');
        const fileContentBase = `${API}/configs/${encodeURIComponent(_configDevicePrefix)}/${encodeURIComponent(_configDeviceHostname)}`;
        const filtered = kw ? _configDeviceFiles.filter(f => (f.name || '').toLowerCase().includes(kw)) : _configDeviceFiles;
        if (!listEl) return;
        if (!filtered.length) { listEl.innerHTML = ''; return; }
        listEl.innerHTML = filtered.map(f => `<li class="config-file-row"><a href="${fileContentBase}/${encodeURIComponent(f.name)}" target="_blank" rel="noopener">${escapeHtml(f.name)}</a><span class="config-file-size"></span><button type="button" class="btn btn-sm btn-secondary config-file-download" data-url="${escapeHtml(fileContentBase + '/' + encodeURIComponent(f.name) + '?download=1')}" data-name="${escapeHtml(f.name)}" title="下载">下载</button></li>`).join('');
    });
    document.getElementById('btn-config-files-search')?.addEventListener('click', () => document.getElementById('config-files-search')?.dispatchEvent(new Event('input')));
}

// 配置全文搜索（基础版）
async function searchConfigs() {
    const input = document.getElementById('config-search-text');
    const box = document.getElementById('config-search-result');
    if (!input || !box) return;
    const q = (input.value || '').trim();
    if (!q) {
        box.classList.add('config-search-result--empty');
        box.innerHTML = '';
        return;
    }
    box.classList.remove('config-search-result--empty');
    box.innerHTML = '<div class="loading-state">搜索中...</div>';
    try {
        const res = await fetch(`${API}/search/configs?q=${encodeURIComponent(q)}&limit=50`);
        const data = await res.json();
        if (!res.ok) {
            box.classList.remove('config-search-result--empty');
            box.innerHTML = `<div class="empty-state">${escapeHtml(data.error || '搜索失败')}</div>`;
            return;
        }
        const items = data.items || [];
        if (!items.length) {
            box.classList.remove('config-search-result--empty');
            box.innerHTML = '<div class="empty-state">未在任何配置中找到匹配内容。</div>';
            return;
        }
        box.classList.remove('config-search-result--empty');
        box.innerHTML = items.map(it => {
            const p = escapeHtml(it.prefix || '');
            const h = escapeHtml(it.hostname || '');
            const configFileUrl = `${API}/configs/${encodeURIComponent(it.prefix)}/${encodeURIComponent(it.hostname)}/${encodeURIComponent(it.filename)}`;
            const hostLabel = it.ip ? `${it.hostname} (${it.ip})` : it.hostname;
            const fileName = it.filename || '';
            const lineInfo = `第 ${it.line_no || 0} 行`;
            return `
                <div class="config-search-item">
                    <div class="config-search-item-main">
                        <a href="#" class="config-search-host" data-config-device data-prefix="${p}" data-hostname="${h}">${escapeHtml(hostLabel || '')}</a>
                        <a href="${configFileUrl}" target="_blank" class="config-search-file">${escapeHtml(fileName)}</a>
                        <span class="config-search-line">${escapeHtml(lineInfo)}</span>
                    </div>
                    <div class="config-search-snippet">${escapeHtml(it.line || '')}</div>
                </div>
            `;
        }).join('');
    } catch (e) {
        box.classList.remove('config-search-result--empty');
        box.innerHTML = '<div class="empty-state">搜索失败，请稍后重试。</div>';
    }
}

document.getElementById('btn-config-search')?.addEventListener('click', searchConfigs);
document.getElementById('config-search-text')?.addEventListener('keypress', e => {
    if (e.key === 'Enter') searchConfigs();
});

// 已备份配置：删除备份确认与操作
let _pendingDeleteBackups = { prefix: null, hostname: null, hostnameRaw: '' };
document.getElementById('config-device-list')?.addEventListener('click', function(e) {
    const btn = e.target.closest('button[data-action="delete-backups"]');
    if (!btn) return;
    const prefix = btn.getAttribute('data-prefix');
    const hostname = btn.getAttribute('data-hostname');
    const hostnameRaw = btn.getAttribute('data-hostname-raw') || '';
    _pendingDeleteBackups = { prefix, hostname, hostnameRaw };
    const msgEl = document.getElementById('modal-delete-backups-msg');
    const inputEl = document.getElementById('delete-backups-confirm-input');
    const confirmBtn = document.getElementById('btn-delete-backups-confirm');
    if (msgEl) msgEl.textContent = `确定删除设备「${hostnameRaw}」的全部备份配置文件？此操作不可恢复。`;
    if (inputEl) inputEl.value = '';
    if (confirmBtn) confirmBtn.disabled = true;
    document.getElementById('modal-delete-backups')?.classList.add('show');
});

document.getElementById('delete-backups-confirm-input')?.addEventListener('input', function() {
    const btn = document.getElementById('btn-delete-backups-confirm');
    if (btn) btn.disabled = this.value.trim() !== '删除';
});

document.getElementById('delete-backups-confirm-input')?.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && this.value.trim() === '删除') {
        document.getElementById('btn-delete-backups-confirm')?.click();
    }
});

document.getElementById('btn-delete-backups-cancel')?.addEventListener('click', () => {
    _pendingDeleteBackups = { prefix: null, hostname: null, hostnameRaw: '' };
    const inputEl = document.getElementById('delete-backups-confirm-input');
    if (inputEl) inputEl.value = '';
    document.getElementById('modal-delete-backups')?.classList.remove('show');
});

document.getElementById('btn-delete-backups-confirm')?.addEventListener('click', async () => {
    const { prefix, hostname, hostnameRaw } = _pendingDeleteBackups || {};
    if (!prefix || !hostname) return;
    document.getElementById('modal-delete-backups')?.classList.remove('show');
    const inputEl = document.getElementById('delete-backups-confirm-input');
    if (inputEl) inputEl.value = '';
    _pendingDeleteBackups = { prefix: null, hostname: null, hostnameRaw: '' };
    try {
        const url = `${API}/configs/devices/${prefix}/${hostname}/delete`;
        const res = await fetch(url, { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.ok) {
            toast(`已删除设备「${hostnameRaw || ''}」的 ${data.deleted || 0} 个备份文件`, 'success');
            loadConfigs();
        } else {
            toast(data.error || '删除备份失败', 'error');
        }
    } catch (_) {
        toast('删除备份失败，请稍后重试', 'error');
    }
});

// 设置 - 备份频率人性化
const BACKUP_FREQ_OPTIONS = ['none', 'hourly', 'daily', 'weekly', 'twice_daily', 'custom'];
function getBackupFrequencySaveValue() {
    const sel = document.getElementById('setting-backup-frequency');
    const customInput = document.getElementById('setting-custom-cron');
    const v = sel?.value || 'none';
    if (v === 'custom') return customInput?.value?.trim() || 'none';
    return v;
}
function setBackupFrequencyDisplay(val) {
    const sel = document.getElementById('setting-backup-frequency');
    const wrap = document.getElementById('setting-custom-cron-wrap');
    const customInput = document.getElementById('setting-custom-cron');
    if (!sel) return;
    if (BACKUP_FREQ_OPTIONS.includes(val)) {
        sel.value = val;
        if (wrap) wrap.style.display = val === 'custom' ? 'block' : 'none';
        if (customInput && val !== 'custom') customInput.value = '';
    } else {
        sel.value = 'custom';
        if (wrap) wrap.style.display = 'block';
        if (customInput) customInput.value = val || '';
    }
}
document.getElementById('setting-backup-frequency')?.addEventListener('change', function() {
    const wrap = document.getElementById('setting-custom-cron-wrap');
    if (wrap) wrap.style.display = this.value === 'custom' ? 'block' : 'none';
});

// 设置 - 发现频率（与备份频率选项统一）
const DISCOVERY_FREQ_OPTIONS = ['none', 'hourly', 'twice_daily', 'daily', 'weekly', 'custom'];
function getDiscoveryFrequencySaveValue() {
    const sel = document.getElementById('setting-discovery-frequency');
    const customInput = document.getElementById('setting-discovery-custom-cron');
    const v = sel?.value || 'none';
    if (v === 'custom') return customInput?.value?.trim() || 'none';
    return v;
}
function setDiscoveryFrequencyDisplay(val) {
    if (val === 'every_8_hours') val = 'twice_daily';
    const sel = document.getElementById('setting-discovery-frequency');
    const wrap = document.getElementById('setting-discovery-custom-cron-wrap');
    const customInput = document.getElementById('setting-discovery-custom-cron');
    if (!sel) return;
    if (DISCOVERY_FREQ_OPTIONS.includes(val)) {
        sel.value = val;
        if (wrap) wrap.style.display = val === 'custom' ? 'block' : 'none';
        if (customInput && val !== 'custom') customInput.value = '';
    } else {
        sel.value = 'custom';
        if (wrap) wrap.style.display = 'block';
        if (customInput) customInput.value = val || '';
    }
}
document.getElementById('setting-discovery-frequency')?.addEventListener('change', function() {
    const wrap = document.getElementById('setting-discovery-custom-cron-wrap');
    if (wrap) wrap.style.display = this.value === 'custom' ? 'block' : 'none';
});

// ----- 用户管理（基础版：仅管理员可用） -----
let _userListCache = [];

async function loadUsers() {
    const tbody = document.getElementById('user-list');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="9">加载中...</td></tr>';
    try {
        const res = await fetch(`${API}/users`);
        const data = await res.json();
        if (!res.ok) {
            const msg = data && (data.error || data.message) || '无权查看用户列表或加载失败。';
            tbody.innerHTML = `<tr><td colspan="9">${escapeHtml(msg)}</td></tr>`;
            return;
        }
        const items = data.items || [];
        _userListCache = items;
        const canEdit = (data.can_edit_settings === undefined) ? true : !!data.can_edit_settings;
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="9">暂无用户记录。用户成功登录后会自动出现在此列表。</td></tr>';
            return;
        }
        const roleLabel = {
            admin: '管理员',
            ops: '运维用户',
            viewer: '只读用户'
        };
        tbody.innerHTML = items.map(u => {
            const status = u.is_active === false ? '已禁用' : '正常';
            const roleText = roleLabel[u.role] || u.role || '未知';
            const src = u.source === 'ldap' ? 'LDAP' : '本地';
            const created = (u.created_at || '').slice(0, 16).replace('T', ' ');
            const isLdap = (u.source || '') === 'ldap';
            const isLocal = !isLdap;
            const isSuperAdmin = (u.username || '') === 'admin';
            const actions = [];
            if (canEdit) {
                actions.push('<button type="button" class="btn btn-sm btn-secondary" data-action="edit-user">编辑</button>');
                // 仅本地账号支持复制
                if (isLocal) {
                    actions.push('<button type="button" class="btn btn-sm btn-secondary" data-action="copy-user">复制</button>');
                }
                // 删除按钮规则：
                // - 超级管理员 admin：不显示删除按钮
                // - 其他本地账号 与 所有 LDAP 账号：显示删除按钮
                if (!isSuperAdmin) {
                    actions.push('<button type="button" class="btn btn-sm btn-delete" data-action="delete-user">删除</button>');
                }
            }
            return `
                <tr data-user-id="${u.id}">
                    <td>${escapeHtml(u.username || '')}</td>
                    <td>${escapeHtml(u.display_name || '')}</td>
                    <td>${escapeHtml(u.email || '')}</td>
                    <td>${escapeHtml(u.phone || '')}</td>
                    <td>${escapeHtml(src)}</td>
                    <td>${escapeHtml(roleText)}</td>
                    <td>${escapeHtml(status)}</td>
                    <td>${escapeHtml(created)}</td>
                    <td style="white-space: nowrap;">${actions.join(' ')}</td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="9">加载失败，请稍后重试。</td></tr>';
    }
}

let _editingUserId = null;
let _userEditMode = 'edit'; // edit | create | clone

async function fillUserAllowedGroupsOptions() {
    const datalist = document.getElementById('user-edit-allowed-groups-datalist');
    const chipsEl = document.getElementById('user-edit-allowed-groups-chips');
    const inputEl = document.getElementById('user-edit-allowed-groups');
    if (!datalist || !chipsEl || !inputEl) return;
    try {
        const res = await fetch(`${API}/device-groups?from_devices=1`);
        const data = await res.json();
        const groups = data.groups || [];
        datalist.innerHTML = groups.map(g => `<option value="${String(g).replace(/"/g, '&quot;')}">`).join('');
        chipsEl.innerHTML = groups.length
            ? '选择分组：' + groups.map(g => `<button type="button" class="btn-chip" data-group="${String(g).replace(/"/g, '&quot;')}">${escapeHtml(g)}</button>`).join('')
            : '';
        chipsEl.querySelectorAll('.btn-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                const group = btn.getAttribute('data-group') || '';
                const current = (inputEl.value || '').split(',').map(s => s.trim()).filter(Boolean);
                if (group && !current.includes(group)) {
                    current.push(group);
                    inputEl.value = current.join(', ');
                }
            });
        });
    } catch (e) {
        datalist.innerHTML = '';
        chipsEl.innerHTML = '';
    }
}

document.getElementById('user-list')?.addEventListener('click', e => {
    const actionBtn = e.target.closest('button[data-action]');
    if (!actionBtn) return;
    const action = actionBtn.getAttribute('data-action');
    const tr = actionBtn.closest('tr');
    const id = tr ? Number(tr.getAttribute('data-user-id')) : NaN;
    if (!id && action !== 'create-user') return;
    const user = _userListCache.find(u => u.id === id);

    const nameStatic = document.getElementById('user-edit-username');
    const nameInput = document.getElementById('user-edit-username-input');
    const dispEl = document.getElementById('user-edit-display-name');
    const emailEl = document.getElementById('user-edit-email');
    const phoneEl = document.getElementById('user-edit-phone');
    const allowedGrpsEl = document.getElementById('user-edit-allowed-groups');
    const srcEl = document.getElementById('user-edit-source');
    const roleEl = document.getElementById('user-edit-role');
    const activeEl = document.getElementById('user-edit-active');
    const pwdEl = document.getElementById('user-edit-password');

    if (action === 'edit-user') {
        if (!user) return;
        _userEditMode = 'edit';
        _editingUserId = id;
        if (nameStatic) {
            nameStatic.style.display = '';
            nameStatic.textContent = user.username || '';
        }
        if (nameInput) {
            nameInput.style.display = 'none';
            nameInput.value = '';
        }
        if (dispEl) dispEl.value = user.display_name || '';
        if (emailEl) emailEl.value = user.email || '';
        if (phoneEl) phoneEl.value = user.phone || '';
        if (allowedGrpsEl) allowedGrpsEl.value = user.allowed_groups || '';
        if (srcEl) srcEl.textContent = user.source === 'ldap' ? 'LDAP' : '本地账号';
        if (roleEl) roleEl.value = user.role || 'viewer';
        if (activeEl) activeEl.checked = user.is_active !== false;
        if (pwdEl) pwdEl.value = '';
        const titleEl = document.getElementById('modal-user-title');
        if (titleEl) titleEl.textContent = '编辑用户';
        fillUserAllowedGroupsOptions();
        document.getElementById('modal-user')?.classList.add('show');
        return;
    }

    if (action === 'copy-user') {
        if (!user) return;
        _userEditMode = 'clone';
        _editingUserId = null;
        if (nameStatic) {
            nameStatic.style.display = 'none';
            nameStatic.textContent = '';
        }
        if (nameInput) {
            nameInput.style.display = '';
            nameInput.value = '';
        }
        if (dispEl) dispEl.value = user.display_name || '';
        if (emailEl) emailEl.value = user.email || '';
        if (phoneEl) phoneEl.value = user.phone || '';
        if (allowedGrpsEl) allowedGrpsEl.value = user.allowed_groups || '';
        if (srcEl) srcEl.textContent = '本地账号';
        if (roleEl) roleEl.value = user.role || 'viewer';
        if (activeEl) activeEl.checked = user.is_active !== false;
        if (pwdEl) pwdEl.value = '';
        const titleElCopy = document.getElementById('modal-user-title');
        if (titleElCopy) titleElCopy.textContent = '创建用户';
        fillUserAllowedGroupsOptions();
        document.getElementById('modal-user')?.classList.add('show');
        return;
    }

    if (action === 'delete-user') {
        if (!user) return;
        if (!window.confirm(`确定删除用户「${user.username || ''}」吗？此操作不可恢复。`)) {
            return;
        }
        (async () => {
            try {
                const res = await fetch(`${API}/users/${user.id}`, { method: 'DELETE' });
                const data = await res.json();
                if (res.ok && data.ok) {
                    toast('用户已删除', 'success');
                    loadUsers();
                } else {
                    toast(data.error || '删除失败', 'error');
                }
            } catch (e) {
                toast('删除失败，请稍后重试', 'error');
            }
        })();
    }
});

document.getElementById('btn-user-new')?.addEventListener('click', () => {
    _userEditMode = 'create';
    _editingUserId = null;
    const nameStatic = document.getElementById('user-edit-username');
    const nameInput = document.getElementById('user-edit-username-input');
    const dispEl = document.getElementById('user-edit-display-name');
    const emailEl = document.getElementById('user-edit-email');
    const phoneEl = document.getElementById('user-edit-phone');
    const srcEl = document.getElementById('user-edit-source');
    const roleEl = document.getElementById('user-edit-role');
    const activeEl = document.getElementById('user-edit-active');
    const pwdEl = document.getElementById('user-edit-password');
    if (nameStatic) {
        nameStatic.style.display = 'none';
        nameStatic.textContent = '';
    }
    if (nameInput) {
        nameInput.style.display = '';
        nameInput.value = '';
    }
    if (dispEl) dispEl.value = '';
    if (emailEl) emailEl.value = '';
    if (phoneEl) phoneEl.value = '';
    const allowedGrpsNewEl = document.getElementById('user-edit-allowed-groups');
    if (allowedGrpsNewEl) allowedGrpsNewEl.value = '';
    if (srcEl) srcEl.textContent = '本地账号';
    if (roleEl) roleEl.value = 'viewer';
    if (activeEl) activeEl.checked = true;
    if (pwdEl) pwdEl.value = '';
    const titleElNew = document.getElementById('modal-user-title');
    if (titleElNew) titleElNew.textContent = '创建用户';
    fillUserAllowedGroupsOptions();
    document.getElementById('modal-user')?.classList.add('show');
});

document.getElementById('btn-user-cancel')?.addEventListener('click', () => {
    _editingUserId = null;
    _userEditMode = 'edit';
    document.getElementById('modal-user')?.classList.remove('show');
});

function generateRandomPassword() {
    const upper = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
    const lower = 'abcdefghjkmnpqrstuvwxyz';
    const digit = '23456789';
    const special = '!@#$%&*_+-=';
    const pool = upper + lower + digit + special;
    const pick = (s, n) => Array.from({ length: n }, () => s[Math.floor(Math.random() * s.length)]).join('');
    const part = [
        pick(upper, 1),
        pick(lower, 1),
        pick(digit, 1),
        pick(special, 1),
        pick(pool, 10)
    ];
    return part.sort(() => Math.random() - 0.5).join('');
}

document.getElementById('btn-user-password-generate')?.addEventListener('click', () => {
    const pwdEl = document.getElementById('user-edit-password');
    if (!pwdEl) return;
    pwdEl.value = generateRandomPassword();
    pwdEl.type = 'text';
    toast('已生成随机密码，请复制保存后勿泄露', 'success');
});

document.getElementById('btn-user-save')?.addEventListener('click', async () => {
    const roleEl = document.getElementById('user-edit-role');
    const activeEl = document.getElementById('user-edit-active');
    const dispEl = document.getElementById('user-edit-display-name');
    const emailEl = document.getElementById('user-edit-email');
    const phoneEl = document.getElementById('user-edit-phone');
    const nameInput = document.getElementById('user-edit-username-input');
    const pwdEl = document.getElementById('user-edit-password');
    const payload = {
        role: roleEl ? roleEl.value : 'viewer',
        is_active: !!(activeEl && activeEl.checked),
        display_name: dispEl ? dispEl.value : '',
        email: emailEl ? (emailEl.value || '').trim() : '',
        phone: phoneEl ? (phoneEl.value || '').trim() : '',
        allowed_groups: document.getElementById('user-edit-allowed-groups')?.value.trim() || ''
    };

    // 新建/复制：调用 POST /api/users
    if (_userEditMode === 'create' || _userEditMode === 'clone') {
        const username = (nameInput && nameInput.value) ? nameInput.value.trim() : '';
        if (!username) {
            toast('请输入用户名', 'error');
            return;
        }
        const pwd = (pwdEl && pwdEl.value) ? pwdEl.value : '';
        if (!pwd) {
            toast('请为本地账号设置登录密码', 'error');
            return;
        }
        const body = Object.assign({}, payload, { username, password: pwd });
        try {
            const res = await fetch(`${API}/users`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (res.ok) {
                toast('本地账号已保存', 'success');
                document.getElementById('modal-user')?.classList.remove('show');
                _editingUserId = null;
                _userEditMode = 'edit';
                loadUsers();
            } else {
                toast(data.error || '保存失败', 'error');
            }
        } catch (e) {
            toast('保存失败，请稍后重试', 'error');
        }
        return;
    }

    // 编辑：必须有 _editingUserId
    if (!_editingUserId) return;
    try {
        const res = await fetch(`${API}/users/${_editingUserId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.assign({}, payload, {
            // 编辑时如果填写了密码，则一并提交给后端重置密码
            password: (pwdEl && pwdEl.value) ? pwdEl.value : undefined
        }))
        });
        const data = await res.json();
        if (res.ok) {
            toast('用户信息已更新', 'success');
            document.getElementById('modal-user')?.classList.remove('show');
            _editingUserId = null;
            loadUsers();
        } else {
            toast(data.error || '更新失败', 'error');
        }
    } catch (e) {
        toast('更新失败，请稍后重试', 'error');
    }
});
async function loadSettings() {
    const res = await fetch(`${API}/settings`);
    const d = await res.json();
    const draft = readSettingsDraft();
    const pick = (key, fallback) => (draft[key] !== undefined ? draft[key] : fallback);
    const sysNameEl = document.getElementById('setting-system-name');
    if (sysNameEl) sysNameEl.value = pick('system_name', d.system_name || '配置备份中心');
    document.getElementById('setting-username').value = pick('username', d.username || '');
    document.getElementById('setting-password').value = pick('password', d.password || '');
    const connEl = document.getElementById('setting-default-connection-type');
    if (connEl) connEl.value = (pick('default_connection_type', d.default_connection_type || 'TELNET') || 'TELNET').toUpperCase();
    const retentionEl = document.getElementById('setting-retention-days');
    if (retentionEl) retentionEl.value = pick('backup_retention_days', d.backup_retention_days ?? '');
    const footerEl = document.getElementById('setting-footer-text');
    if (footerEl) footerEl.value = pick('footer_text', d.footer_text || '');
    const sessionTimeoutEl = document.getElementById('setting-session-timeout');
    if (sessionTimeoutEl) sessionTimeoutEl.value = pick('session_timeout_minutes', d.session_timeout_minutes ?? '0');
    const lockoutAttemptsEl = document.getElementById('setting-login-lockout-attempts');
    if (lockoutAttemptsEl) lockoutAttemptsEl.value = pick('login_lockout_attempts', d.login_lockout_attempts ?? '0');
    const lockoutMinutesEl = document.getElementById('setting-login-lockout-minutes');
    if (lockoutMinutesEl) lockoutMinutesEl.value = pick('login_lockout_minutes', d.login_lockout_minutes ?? '15');
    const pwdMinLenEl = document.getElementById('setting-password-min-length');
    if (pwdMinLenEl) pwdMinLenEl.value = pick('password_min_length', d.password_min_length ?? '6');
    const pwdDigitEl = document.getElementById('setting-password-require-digit');
    if (pwdDigitEl) pwdDigitEl.checked = (pick('password_require_digit', d.password_require_digit || '0') === '1');
    const pwdUpperEl = document.getElementById('setting-password-require-upper');
    if (pwdUpperEl) pwdUpperEl.checked = (pick('password_require_upper', d.password_require_upper || '0') === '1');
    const pwdLowerEl = document.getElementById('setting-password-require-lower');
    if (pwdLowerEl) pwdLowerEl.checked = (pick('password_require_lower', d.password_require_lower || '0') === '1');
    const pwdSpecialEl = document.getElementById('setting-password-require-special');
    if (pwdSpecialEl) pwdSpecialEl.checked = (pick('password_require_special', d.password_require_special || '0') === '1');
    const devicePerPageEl = document.getElementById('setting-device-per-page');
    if (devicePerPageEl) devicePerPageEl.value = pick('device_per_page_default', d.device_per_page_default || '50');
    const logPerPageEl = document.getElementById('setting-log-per-page');
    if (logPerPageEl) logPerPageEl.value = pick('log_per_page_default', d.log_per_page_default || '50');
    const backupTimeoutEl = document.getElementById('setting-backup-timeout');
    if (backupTimeoutEl) backupTimeoutEl.value = pick('backup_timeout_seconds', d.backup_timeout_seconds ?? '30');
    const backupReadTimeoutEl = document.getElementById('setting-backup-read-timeout');
    if (backupReadTimeoutEl) backupReadTimeoutEl.value = pick('backup_read_timeout_seconds', d.backup_read_timeout_seconds ?? '30');
    const backupThreadEl = document.getElementById('setting-backup-thread-num');
    if (backupThreadEl) backupThreadEl.value = pick('backup_thread_num', d.backup_thread_num ?? '10');
    const sshPortEl = document.getElementById('setting-ssh-port');
    if (sshPortEl) sshPortEl.value = pick('ssh_port', d.ssh_port ?? '22');
    const telnetPortEl = document.getElementById('setting-telnet-port');
    if (telnetPortEl) telnetPortEl.value = pick('telnet_port', d.telnet_port ?? '23');
    const webhookEl = document.getElementById('setting-backup-failure-webhook');
    if (webhookEl) webhookEl.value = pick('backup_failure_webhook', d.backup_failure_webhook || '');
    const apiTokensEl = document.getElementById('setting-api-tokens');
    if (apiTokensEl) apiTokensEl.value = pick('api_tokens', d.api_tokens || '');
    const ldapEnabledEl = document.getElementById('setting-ldap-enabled');
    if (ldapEnabledEl) ldapEnabledEl.checked = (pick('ldap_enabled', d.ldap_enabled || '0') === '1');
    const ldapServerEl = document.getElementById('setting-ldap-server');
    if (ldapServerEl) ldapServerEl.value = pick('ldap_server', d.ldap_server || '');
    const ldapBaseDnEl = document.getElementById('setting-ldap-base-dn');
    if (ldapBaseDnEl) ldapBaseDnEl.value = pick('ldap_base_dn', d.ldap_base_dn || '');
    const ldapBindDnEl = document.getElementById('setting-ldap-bind-dn');
    if (ldapBindDnEl) ldapBindDnEl.value = pick('ldap_bind_dn', d.ldap_bind_dn || '');
    const ldapBindPwdEl = document.getElementById('setting-ldap-bind-password');
    if (ldapBindPwdEl) ldapBindPwdEl.value = pick('ldap_bind_password', d.ldap_bind_password || '');
    const ldapUserFilterEl = document.getElementById('setting-ldap-user-filter');
    if (ldapUserFilterEl) ldapUserFilterEl.value = pick('ldap_user_filter', d.ldap_user_filter || '');
    // SNMP / 自动发现
    const snmpVerEl = document.getElementById('setting-snmp-version');
    if (snmpVerEl) snmpVerEl.value = pick('snmp_version', d.snmp_version || '2c');
    const snmpCommEl = document.getElementById('setting-snmp-community');
    if (snmpCommEl) snmpCommEl.value = pick('snmp_community', d.snmp_community || 'public');
    const snmpTimeoutEl = document.getElementById('setting-snmp-timeout');
    if (snmpTimeoutEl) snmpTimeoutEl.value = pick('snmp_timeout_ms', d.snmp_timeout_ms || '2000');
    const snmpRetriesEl = document.getElementById('setting-snmp-retries');
    if (snmpRetriesEl) snmpRetriesEl.value = pick('snmp_retries', d.snmp_retries || '1');
    const discoveryFreqVal = pick('discovery_frequency', d.discovery_frequency || 'none') || 'none';
    setDiscoveryFrequencyDisplay(discoveryFreqVal);
    renderDiscoveryTypeKeywordsRows(pick('discovery_type_keywords', d.discovery_type_keywords || '')).catch(() => {});
    const hostnameSplitEl = document.getElementById('setting-discovery-hostname-split');
    const hostnameSegEl = document.getElementById('setting-discovery-hostname-segment');
    if (hostnameSplitEl) {
        // 若未设置则使用空字符串，placeholder 仅作为「常见按 . 分段」提示
        const splitChar = pick('discovery_hostname_split_char', d.discovery_hostname_split_char || '');
        hostnameSplitEl.value = splitChar;
        if (hostnameSegEl) {
            // 当分隔符为空时，「分几段」无效，输入框也置空
            hostnameSegEl.value = splitChar ? (pick('discovery_hostname_segment_index', d.discovery_hostname_segment_index || '1') || '1') : '';
        }
    } else if (hostnameSegEl) {
        hostnameSegEl.value = pick('discovery_hostname_segment_index', d.discovery_hostname_segment_index || '1');
    }
    const discoveryUniqueByEl = document.getElementById('setting-discovery-unique-by');
    if (discoveryUniqueByEl) {
        const u = (pick('discovery_unique_by', d.discovery_unique_by || 'hostname') || 'hostname').toLowerCase();
        discoveryUniqueByEl.value = (u === 'ip' ? 'ip' : 'hostname');
    }
    const tzEl = document.getElementById('setting-timezone');
    if (tzEl) {
        const tz = (pick('timezone', d.timezone || 'Asia/Shanghai') || 'Asia/Shanghai').trim();
        tzEl.value = tz;
        if (![].find.call(tzEl.options, o => o.value === tz)) {
            const opt = new Option(tz, tz);
            tzEl.add(opt);
            tzEl.value = tz;
        }
    }
    const langEl = document.getElementById('setting-language');
    if (langEl) {
        const lang = (pick('language', d.language || 'zh') || 'zh').toLowerCase();
        langEl.value = (lang === 'en' ? 'en' : 'zh');
    }
    if (d.language !== undefined) {
        const lang = (d.language || 'zh').toLowerCase();
        const next = (lang === 'en' ? 'en' : 'zh');
        if (window.__LANG !== next) {
            window.__LANG = next;
            try { window.localStorage.setItem('vconfig_lang', next); } catch (_) {}
            if (window.applyI18n) window.applyI18n();
        }
    }
    const freq = pick('backup_frequency', d.backup_frequency || 'none') || 'none';
    setBackupFrequencyDisplay(freq);
    if (freq === 'custom') {
        const customInput = document.getElementById('setting-custom-cron');
        if (customInput && draft.backup_custom_cron !== undefined) {
            customInput.value = draft.backup_custom_cron;
        }
    }

    // Logo 预览
    const logoPreviewImg = document.getElementById('setting-logo-preview');
    const logoDefaultHint = document.getElementById('setting-logo-default-hint');
    if (logoPreviewImg && logoDefaultHint) {
        if (d.logo_enabled) {
            const base = API.replace(/\/api$/, '');
            logoPreviewImg.src = `${base}/logo?ts=${Date.now()}`;
            logoPreviewImg.classList.remove('hidden');
            logoDefaultHint.style.display = 'none';
        } else {
            logoPreviewImg.src = '';
            logoPreviewImg.classList.add('hidden');
            logoDefaultHint.style.display = 'inline';
        }
    }

    // 是否有权限修改设置：仅允许使用全局用户名的本地登录账号
    const canEdit = (d.can_edit_settings === undefined) ? true : !!d.can_edit_settings;
    const saveBtn = document.getElementById('btn-save-settings');
    if (saveBtn) {
        saveBtn.disabled = !canEdit;
        if (!canEdit) {
            saveBtn.textContent = '无权修改';
        } else {
            saveBtn.textContent = '保存';
        }
    }
    if (!canEdit) {
        const ids = [
            'setting-username',
            'setting-password',
            'setting-retention-days',
            'setting-default-connection-type',
            'setting-timezone',
            'setting-language',
            'setting-footer-text',
            'setting-backup-frequency',
            'setting-custom-cron',
            'setting-discovery-frequency',
            'setting-discovery-custom-cron',
            'setting-discovery-unique-by',
            'setting-session-timeout',
            'setting-login-lockout-attempts',
            'setting-login-lockout-minutes',
            'setting-password-min-length',
            'setting-password-require-digit',
            'setting-password-require-upper',
            'setting-password-require-lower',
            'setting-password-require-special',
            'setting-device-per-page',
            'setting-log-per-page',
            'setting-backup-timeout',
            'setting-backup-read-timeout',
            'setting-backup-thread-num',
            'setting-ssh-port',
            'setting-telnet-port',
            'setting-backup-failure-webhook',
            'setting-api-tokens',
            'setting-ldap-enabled',
            'setting-ldap-server',
            'setting-ldap-base-dn',
            'setting-ldap-bind-dn',
            'setting-ldap-bind-password',
            'setting-ldap-user-filter',
            'ldap-test-username',
            'ldap-test-password',
            'setting-logo-file',
            'ssl-cert-file',
            'ssl-key-file',
            'btn-logo-upload',
            'btn-logo-reset',
            'btn-upload-ssl-cert',
            'btn-update-ssl-cert',
            'btn-reset-settings-defaults',
            'btn-restart-service',
            'btn-db-backup',
            'btn-db-restore',
            'db-restore-file',
            'btn-discovery-rule-new',
            'btn-device-type-new',
            'btn-user-new',
            'setting-snmp-version',
            'setting-snmp-community',
            'setting-snmp-timeout',
            'setting-snmp-retries',
            'discovery-quick-ip-range',
            'btn-discovery-quick-scan',
            'btn-discovery-type-keyword-add',
            'setting-discovery-hostname-split',
            'setting-discovery-hostname-segment',
        ];
        ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.disabled = true;
        });
        const testBtn = document.getElementById('btn-test-ldap');
        if (testBtn) testBtn.disabled = true;
        // 供 discovery/users/device-types 子模块判断是否可编辑
        try { window.__canEditSettings = false; } catch (e) {}
    } else {
        try { window.__canEditSettings = true; } catch (e) {}
    }
}
document.getElementById('btn-reset-settings-defaults')?.addEventListener('click', async () => {
    if (!confirm('确定要将所有系统设置恢复为系统默认参数值吗？当前自定义设置（含备份账号、备份频率、Logo 等）将被覆盖。')) return;
    try {
        const res = await fetch(`${API}/settings/reset-defaults`, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
            toast(data.error || '恢复默认设置失败，请稍后重试。', 'error');
            return;
        }
        toast('已恢复为系统默认设置', 'success');
        loadSettings();
    } catch (e) {
        toast('恢复默认设置失败，请检查网络后重试。', 'error');
    }
});

document.getElementById('btn-restart-service')?.addEventListener('click', async () => {
    if (!confirm('确定要重启服务吗？重启后页面将短暂不可用，请稍候刷新。')) return;
    try {
        const res = await fetch(`${API}/settings/restart`, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
            toast(data.error || '重启失败，请检查服务运行方式。', 'error');
            return;
        }
        toast('重启已触发，请稍候刷新页面。', 'success');
        setTimeout(() => { window.location.reload(); }, 3000);
    } catch (e) {
        toast('重启请求失败，请稍后重试。', 'error');
    }
});

document.getElementById('btn-db-backup')?.addEventListener('click', () => {
    const modal = document.getElementById('modal-db-backup');
    if (modal) modal.classList.add('show');
});
document.getElementById('btn-db-backup-cancel')?.addEventListener('click', () => {
    const modal = document.getElementById('modal-db-backup');
    if (modal) modal.classList.remove('show');
});
document.getElementById('btn-db-backup-confirm')?.addEventListener('click', () => {
    const modal = document.getElementById('modal-db-backup');
    if (modal) modal.classList.remove('show');
    window.location.href = `${API}/settings/db/backup`;
    toast('正在下载数据库备份…', 'success');
});

document.getElementById('btn-db-restore')?.addEventListener('click', async () => {
    const input = document.getElementById('db-restore-file');
    if (!input?.files?.length) {
        toast('请先选择要恢复的数据库备份文件。', 'warn');
        return;
    }
    if (!confirm('确定要恢复数据库吗？当前数据库将先备份，恢复后会触发服务重启。')) return;
    const formData = new FormData();
    formData.append('file', input.files[0]);
    try {
        const res = await fetch(`${API}/settings/db/restore`, { method: 'POST', body: formData });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
            toast(data.error || '恢复失败，请稍后重试。', 'error');
            return;
        }
        toast('数据库已恢复，服务将重启，请稍候刷新页面。', 'success');
        input.value = '';
        setTimeout(() => { window.location.reload(); }, 3000);
    } catch (e) {
        toast('恢复请求失败，请稍后重试。', 'error');
    }
});

document.getElementById('btn-save-settings')?.addEventListener('click', async () => {
    const retentionEl = document.getElementById('setting-retention-days');
    const retentionVal = retentionEl ? parseInt(retentionEl.value, 10) : 30;
    const res = await fetch(`${API}/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            system_name: document.getElementById('setting-system-name')?.value || '',
            username: document.getElementById('setting-username').value,
            password: document.getElementById('setting-password').value,
            backup_frequency: getBackupFrequencySaveValue(),
            default_connection_type: document.getElementById('setting-default-connection-type').value,
            backup_retention_days: isNaN(retentionVal) ? 30 : Math.max(0, Math.min(3650, retentionVal)),
            timezone: document.getElementById('setting-timezone').value || 'Asia/Shanghai',
            language: (document.getElementById('setting-language') && document.getElementById('setting-language').value) || 'zh',
            footer_text: (document.getElementById('setting-footer-text') ? document.getElementById('setting-footer-text').value : '') + '',
            session_timeout_minutes: parseInt(document.getElementById('setting-session-timeout')?.value, 10) || 0,
            login_lockout_attempts: parseInt(document.getElementById('setting-login-lockout-attempts')?.value, 10) || 0,
            login_lockout_minutes: parseInt(document.getElementById('setting-login-lockout-minutes')?.value, 10) || 15,
            password_min_length: document.getElementById('setting-password-min-length')?.value || '',
            password_require_digit: document.getElementById('setting-password-require-digit')?.checked || false,
            password_require_upper: document.getElementById('setting-password-require-upper')?.checked || false,
            password_require_lower: document.getElementById('setting-password-require-lower')?.checked || false,
            password_require_special: document.getElementById('setting-password-require-special')?.checked || false,
            device_per_page_default: document.getElementById('setting-device-per-page')?.value || '50',
            log_per_page_default: document.getElementById('setting-log-per-page')?.value || '50',
            backup_timeout_seconds: parseInt(document.getElementById('setting-backup-timeout')?.value, 10) || 30,
            backup_read_timeout_seconds: parseInt(document.getElementById('setting-backup-read-timeout')?.value, 10) || 30,
            backup_thread_num: parseInt(document.getElementById('setting-backup-thread-num')?.value, 10) || 10,
            ssh_port: parseInt(document.getElementById('setting-ssh-port')?.value, 10) || 22,
            telnet_port: parseInt(document.getElementById('setting-telnet-port')?.value, 10) || 23,
            backup_failure_webhook: (document.getElementById('setting-backup-failure-webhook')?.value || '').trim(),
            api_tokens: (document.getElementById('setting-api-tokens')?.value || '').trim(),
            // 自动发现 / SNMP 全局设置
            snmp_version: document.getElementById('setting-snmp-version')?.value || '2c',
            snmp_community: (document.getElementById('setting-snmp-community')?.value || '').trim() || 'public',
            snmp_timeout_ms: parseInt(document.getElementById('setting-snmp-timeout')?.value, 10) || 2000,
            snmp_retries: parseInt(document.getElementById('setting-snmp-retries')?.value, 10) || 1,
            discovery_frequency: getDiscoveryFrequencySaveValue(),
            discovery_type_keywords: getDiscoveryTypeKeywordsFromRows(),
            // 允许清空：为空时不再强制回退为 '.'
            discovery_hostname_split_char: (document.getElementById('setting-discovery-hostname-split')?.value || '').trim(),
            discovery_hostname_segment_index: parseInt(document.getElementById('setting-discovery-hostname-segment')?.value, 10) || 1,
            discovery_unique_by: (document.getElementById('setting-discovery-unique-by')?.value || 'hostname').trim().toLowerCase() || 'hostname',
            ldap_enabled: document.getElementById('setting-ldap-enabled')?.checked ? '1' : '0',
            ldap_server: document.getElementById('setting-ldap-server')?.value || '',
            ldap_base_dn: document.getElementById('setting-ldap-base-dn')?.value || '',
            ldap_bind_dn: document.getElementById('setting-ldap-bind-dn')?.value || '',
            ldap_bind_password: document.getElementById('setting-ldap-bind-password')?.value || '',
            ldap_user_filter: document.getElementById('setting-ldap-user-filter')?.value || ''
        })
    });
    if (res.ok) {
        const lang = (document.getElementById('setting-language') && document.getElementById('setting-language').value) || 'zh';
        try { window.localStorage.setItem('vconfig_lang', lang); } catch (_) {}
        toast(window.t ? window.t('toast_settings_saved') : '设置已保存', 'success');
        loadFooterInfo();
        try { window.localStorage.removeItem(SETTINGS_DRAFT_KEY); } catch (_) {}
        if (window.__LANG !== lang) {
            window.__LANG = lang;
            window.location.reload();
        }
    }
});

// Logo 上传与重置
document.getElementById('btn-logo-upload')?.addEventListener('click', async () => {
    const input = document.getElementById('setting-logo-file');
    if (!input || !input.files || !input.files.length) {
        toast('请先选择要上传的 Logo 图片文件。', 'warn');
        return;
    }
    const file = input.files[0];
    const formData = new FormData();
    formData.append('file', file);
    try {
        const res = await fetch(`${API}/settings/logo`, {
            method: 'POST',
            body: formData,
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            toast(data.error || 'Logo 上传失败，请稍后重试。', 'error');
            return;
        }
        toast('Logo 已更新', 'success');
        // 重新加载设置以刷新预览
        loadSettings();
    } catch (e) {
        toast('Logo 上传失败，请检查网络后重试。', 'error');
    } finally {
        if (input) input.value = '';
    }
});

document.getElementById('btn-logo-reset')?.addEventListener('click', async () => {
    if (!confirm('确定要重置为默认图标吗？')) return;
    try {
        const res = await fetch(`${API}/settings/logo`, {
            method: 'DELETE',
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            toast(data.error || '重置 Logo 失败，请稍后重试。', 'error');
            return;
        }
        toast('已重置为默认图标', 'success');
        loadSettings();
    } catch (e) {
        toast('重置 Logo 失败，请检查网络后重试。', 'error');
    }
});

document.getElementById('btn-upload-ssl-cert')?.addEventListener('click', async () => {
    const certInput = document.getElementById('ssl-cert-file');
    const keyInput = document.getElementById('ssl-key-file');
    if (!certInput?.files?.length || !keyInput?.files?.length) {
        toast('请先选择证书文件和私钥文件', 'error');
        return;
    }
    const formData = new FormData();
    formData.append('cert', certInput.files[0]);
    formData.append('key', keyInput.files[0]);
    const btn = document.getElementById('btn-upload-ssl-cert');
    if (btn) btn.disabled = true;
    try {
        const res = await fetch(`${API}/settings/upload-ssl-cert`, {
            method: 'POST',
            body: formData,
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
            toast(data.message || '证书已上传，请重启服务后生效。', 'success');
            if (certInput) certInput.value = '';
            if (keyInput) keyInput.value = '';
        } else {
            toast(data.error || '上传证书失败', 'error');
        }
    } catch (e) {
        toast('请求失败：' + (e && e.message ? e.message : '网络错误'), 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
});

document.getElementById('btn-update-ssl-cert')?.addEventListener('click', async () => {
    if (!confirm('确定要重新生成 HTTPS 自签名证书吗？旧证书将被替换，重启服务后生效。')) return;
    const btn = document.getElementById('btn-update-ssl-cert');
    if (btn) btn.disabled = true;
    try {
        const res = await fetch(`${API}/settings/update-ssl-cert`, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
            toast(data.message || 'SSL 证书已重新生成，请重启服务后生效。', 'success');
        } else {
            toast(data.error || '更新证书失败', 'error');
        }
    } catch (e) {
        toast('请求失败：' + (e && e.message ? e.message : '网络错误'), 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
});

// 密码显示/隐藏
document.getElementById('btn-toggle-password').addEventListener('click', function() {
    const input = document.getElementById('setting-password');
    const isPass = input.type === 'password';
    input.type = isPass ? 'text' : 'password';
    this.textContent = isPass ? '🙈' : '👁';
    this.setAttribute('aria-label', isPass ? '隐藏密码' : '显示密码');
});

// LDAP Bind 密码显示/隐藏
document.getElementById('btn-toggle-ldap-bind-password')?.addEventListener('click', function() {
    const input = document.getElementById('setting-ldap-bind-password');
    if (!input) return;
    const isPass = input.type === 'password';
    input.type = isPass ? 'text' : 'password';
    this.textContent = isPass ? '🙈' : '👁';
    this.setAttribute('aria-label', isPass ? '隐藏密码' : '显示密码');
});

// 设备类型管理弹窗事件
document.getElementById('btn-device-type-new')?.addEventListener('click', () => {
    openDeviceTypeModal(null);
});
document.getElementById('btn-device-type-cancel')?.addEventListener('click', () => {
    const modal = document.getElementById('modal-device-type');
    if (modal) modal.classList.remove('show');
});
document.getElementById('btn-device-type-save')?.addEventListener('click', () => {
    saveDeviceTypeFromModal().catch(() => {});
});
document.getElementById('device-type-list')?.addEventListener('click', e => {
    const tr = e.target.closest('tr[data-id]');
    if (!tr) return;
    const id = tr.getAttribute('data-id');
    if (e.target.closest('[data-device-type-edit]')) {
        // 编辑：优先从缓存中找到完整配置（含 JSON），保证弹窗中能显示真实命令
        let item = null;
        if (Array.isArray(_deviceTypeCache)) {
            item = _deviceTypeCache.find(it => String(it.id) === String(id)) || null;
        }
        if (!item) {
            // 兜底：从当前行读取基础字段（不含 JSON）
            const tds = tr.querySelectorAll('td');
            const type_code = tds[0]?.textContent.trim() || '';
            const display_name = tds[1]?.textContent.trim() || '';
            const driver_type_text = tds[2]?.textContent.trim() || '';
            let driver_type = 'generic';
            if (driver_type_text.indexOf('内置') !== -1) driver_type = 'builtin';
            else if (driver_type_text.indexOf('自定义') !== -1) driver_type = 'custom';
            const enabled_text = tds[3]?.textContent.trim() || '';
            const enabled = enabled_text.indexOf('已启用') !== -1;
            const sort_order = parseInt(tds[4]?.textContent.trim(), 10) || 0;
            item = {
                id,
                type_code,
                display_name,
                driver_type,
                enabled,
                sort_order,
                backup_config: null,
                connection_config: null,
                driver_module: null,
            };
        }
        openDeviceTypeModal(item);
    } else if (e.target.closest('[data-device-type-toggle]')) {
        // 启用/禁用切换：简化为调用更新接口
        (async () => {
            try {
                const res = await fetch(`${API}/device-types?include_disabled=1`);
                const data = await res.json();
                const items = Array.isArray(data.items) ? data.items : [];
                const item = items.find(it => String(it.id) === String(id));
                if (!item) return;
                const payload = {
                    display_name: item.display_name,
                    driver_type: item.driver_type,
                    driver_module: item.driver_module,
                    sort_order: item.sort_order,
                    enabled: !item.enabled,
                    backup_config: item.backup_config,
                    connection_config: item.connection_config,
                };
                const r = await fetch(`${API}/device-types/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const resp = await r.json().catch(() => ({}));
                if (!r.ok) {
                    alert(resp.error || '操作失败');
                    return;
                }
                await loadDeviceTypes(true);
                refreshDeviceTypeOptions().catch(() => {});
            } catch (err) {
                console.warn('toggle device type failed', err);
                alert('操作失败，请稍后重试。');
            }
        })();
    } else if (e.target.closest('[data-device-type-delete]')) {
        // 删除设备类型（非内置，且必须没有设备使用）
        (async () => {
            if (!window.confirm('确定要删除该设备类型吗？仅当没有设备使用该类型时才允许删除。')) return;
            try {
                const r = await fetch(`${API}/device-types/${id}`, { method: 'DELETE' });
                const resp = await r.json().catch(() => ({}));
                if (!r.ok) {
                    alert(resp.error || '删除失败');
                    return;
                }
                await loadDeviceTypes(true);
                refreshDeviceTypeOptions().catch(() => {});
            } catch (err) {
                console.warn('delete device type failed', err);
                alert('删除失败，请稍后重试。');
            }
        })();
    }
});

// 系统设置：左侧导航切换 通用 / 备份 / 认证 / 用户
function showSettingsSection(name) {
    const sections = document.querySelectorAll('.settings-section');
    sections.forEach(sec => {
        if (!sec.id) return;
        const id = sec.id.replace('settings-section-', '');
        sec.classList.toggle('active', id === name);
    });
    // 左侧导航高亮
    document.querySelectorAll('.settings-nav-item').forEach(btn => {
        const n = btn.getAttribute('data-settings-section');
        btn.classList.toggle('active', n === name);
    });
}

document.querySelectorAll('.settings-nav-item')?.forEach(btn => {
    btn.addEventListener('click', () => {
        const name = btn.getAttribute('data-settings-section') || 'general';
        showSettingsSection(name);
        try { sessionStorage.setItem('vconfig_settings_section', name); } catch (e) {}
        if (name === 'users') {
            loadUsers();
        } else if (name === 'device-types') {
            loadDeviceTypes(false);
        }
    });
});

// 测试 LDAP 登录
document.getElementById('btn-test-ldap')?.addEventListener('click', async () => {
    const uEl = document.getElementById('ldap-test-username');
    const pEl = document.getElementById('ldap-test-password');
    const resEl = document.getElementById('ldap-test-result');
    if (!uEl || !pEl || !resEl) return;
    const username = (uEl.value || '').trim();
    const password = pEl.value || '';
    resEl.textContent = '';
    resEl.classList.remove('ldap-test-ok', 'ldap-test-error');
    if (!username || !password) {
        resEl.textContent = '请输入测试用户名和密码。';
        resEl.classList.add('ldap-test-error');
        return;
    }
    try {
        const res = await fetch(`${API}/ldap/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (res.ok && data.ok) {
            resEl.textContent = data.message || 'LDAP 登录测试成功。';
            resEl.classList.add('ldap-test-ok');
        } else {
            resEl.textContent = data.message || data.error || 'LDAP 登录测试失败。';
            resEl.classList.add('ldap-test-error');
        }
    } catch (e) {
        resEl.textContent = '测试请求失败：' + (e && e.message ? e.message : e);
        resEl.classList.add('ldap-test-error');
    }
});

// 测试备份失败告警 Webhook
document.getElementById('btn-test-webhook')?.addEventListener('click', async () => {
    const inputEl = document.getElementById('setting-backup-failure-webhook');
    const btn = document.getElementById('btn-test-webhook');
    if (!inputEl || !btn) return;
    const url = (inputEl.value || '').trim();
    if (!url) {
        toast('请先填写 Webhook URL', 'warn');
        return;
    }
    btn.disabled = true;
    try {
        const res = await fetch(`${API}/settings/test-webhook`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
            toast(data.message || '已发送测试消息', 'success');
        } else {
            toast(data.error || '测试失败，请检查 URL 是否可达', 'error');
        }
    } catch (e) {
        toast('请求失败：' + (e && e.message ? e.message : '网络错误'), 'error');
    } finally {
        btn.disabled = false;
    }
});

// 备份日志表：可拖拽调整列宽
(function initLogTableResize() {
    const table = document.getElementById('log-table');
    if (!table) return;
    table.addEventListener('mousedown', function(e) {
        const handle = e.target.closest('.resize-handle');
        if (!handle) return;
        e.preventDefault();
        const th = handle.closest('th');
        if (!th) return;
        const startX = e.pageX;
        const startW = th.offsetWidth;
        function move(e2) {
            const dx = e2.pageX - startX;
            const newW = Math.max(60, startW + dx);
            th.style.minWidth = newW + 'px';
            th.style.width = newW + 'px';
        }
        function up() {
            document.removeEventListener('mousemove', move);
            document.removeEventListener('mouseup', up);
        }
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
    });
})();

// 监听设置页输入，实时保存草稿到 localStorage，避免刷新后丢失未保存内容
function initSettingsDraftWatchers() {
    const map = {
        'setting-username': 'username',
        'setting-password': 'password',
        'setting-retention-days': 'backup_retention_days',
        'setting-default-connection-type': 'default_connection_type',
        'setting-timezone': 'timezone',
        'setting-language': 'language',
        'setting-footer-text': 'footer_text',
        'setting-system-name': 'system_name',
        'setting-ldap-server': 'ldap_server',
        'setting-ldap-base-dn': 'ldap_base_dn',
        'setting-ldap-bind-dn': 'ldap_bind_dn',
        'setting-ldap-bind-password': 'ldap_bind_password',
        'setting-ldap-user-filter': 'ldap_user_filter',
        'setting-api-tokens': 'api_tokens',
        'setting-backup-failure-webhook': 'backup_failure_webhook',
        'setting-ssh-port': 'ssh_port',
        'setting-telnet-port': 'telnet_port',
        'setting-discovery-hostname-split': 'discovery_hostname_split_char',
        'setting-discovery-hostname-segment': 'discovery_hostname_segment_index',
        'setting-discovery-unique-by': 'discovery_unique_by',
    };
    Object.keys(map).forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const key = map[id];
        const handler = () => writeSettingsDraft({ [key]: el.value });
        el.addEventListener('input', handler);
        el.addEventListener('change', handler);
    });
    const ldapEnabledEl = document.getElementById('setting-ldap-enabled');
    if (ldapEnabledEl) {
        const h = () => writeSettingsDraft({ ldap_enabled: ldapEnabledEl.checked ? '1' : '0' });
        ldapEnabledEl.addEventListener('change', h);
    }
    const freqEl = document.getElementById('setting-backup-frequency');
    const cronEl = document.getElementById('setting-custom-cron');
    if (freqEl) {
        freqEl.addEventListener('change', () => {
            writeSettingsDraft({ backup_frequency: freqEl.value });
        });
    }
    if (cronEl) {
        const h = () => writeSettingsDraft({ backup_custom_cron: cronEl.value });
        cronEl.addEventListener('input', h);
        cronEl.addEventListener('change', h);
    }

    // 自动发现 / SNMP 设置
    const snmpVerEl = document.getElementById('setting-snmp-version');
    const snmpCommEl = document.getElementById('setting-snmp-community');
    const snmpTimeoutEl = document.getElementById('setting-snmp-timeout');
    const snmpRetriesEl = document.getElementById('setting-snmp-retries');
    const discoveryFreqEl2 = document.getElementById('setting-discovery-frequency');
    if (snmpVerEl) {
        const h = () => writeSettingsDraft({ snmp_version: snmpVerEl.value });
        snmpVerEl.addEventListener('change', h);
    }
    if (snmpCommEl) {
        const h = () => writeSettingsDraft({ snmp_community: snmpCommEl.value });
        snmpCommEl.addEventListener('input', h);
        snmpCommEl.addEventListener('change', h);
    }
    if (snmpTimeoutEl) {
        const h = () => writeSettingsDraft({ snmp_timeout_ms: snmpTimeoutEl.value });
        snmpTimeoutEl.addEventListener('input', h);
        snmpTimeoutEl.addEventListener('change', h);
    }
    if (snmpRetriesEl) {
        const h = () => writeSettingsDraft({ snmp_retries: snmpRetriesEl.value });
        snmpRetriesEl.addEventListener('input', h);
        snmpRetriesEl.addEventListener('change', h);
    }
    if (discoveryFreqEl2) {
        const h = () => writeSettingsDraft({ discovery_frequency: getDiscoveryFrequencySaveValue() });
        discoveryFreqEl2.addEventListener('change', h);
    }
    const discoveryCronEl = document.getElementById('setting-discovery-custom-cron');
    if (discoveryCronEl) {
        const h2 = () => writeSettingsDraft({ discovery_frequency: getDiscoveryFrequencySaveValue() });
        discoveryCronEl.addEventListener('input', h2);
        discoveryCronEl.addEventListener('change', h2);
    }
    const discoveryTypeKeywordsContainer = document.getElementById('discovery-type-keywords-rows');
    if (discoveryTypeKeywordsContainer) {
        const h = () => writeSettingsDraft({ discovery_type_keywords: getDiscoveryTypeKeywordsFromRows() });
        discoveryTypeKeywordsContainer.addEventListener('input', h);
        discoveryTypeKeywordsContainer.addEventListener('change', h);
    }
}
