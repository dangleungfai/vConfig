# vConfig — 配置备份管理系统

vConfig 是一套面向网络设备的配置备份与变更管理 Web 系统，支持 Telnet/SSH 登录、多厂商设备类型、配置对比与变动展示、告警 Webhook 等。

---

## 系统功能

- **设备管理**：添加/编辑/删除设备，支持分组、设备类型、批量导入（主机名 IP 类型 [分组]）
- **备份执行**：全量备份与单台立即备份，多线程并发，支持 Telnet / SSH，可配置超时与端口
- **备份日志**：按时间查看每次备份结果（成功/失败及原因）
- **已备份配置**：按设备浏览配置文件列表，支持下载、配置对比（最新 vs 上一份）、单设备历史与 diff
- **配置变动**：集中展示最近一次备份中各设备的配置变动（新增/删除命令），支持按设备查看明细
- **配置全文搜索**：在已备份配置中按关键字搜索
- **合规检查**：基于规则的配置合规检测（可选）
- **系统设置**：默认 Telnet/SSH 账号、备份超时与线程数、备份失败告警 Webhook（支持测试）、API Token、时区、LDAP 等
- **用户与权限**：多用户、角色（管理员/运维/只读）、分组权限
- **自动发现**：基于 SNMP 的自动发现规则（可选）
- **仪表盘**：备份统计、未备份设备、配置变动概览、最近登录与安全审计

**支持设备类型**：Cisco、Juniper、Huawei、H3C、RouterOS 等（可扩展设备类型与驱动）。

---

## 部署步骤

### 1. 环境要求

- Python 3.8+
- 建议使用虚拟环境

### 2. 安装依赖

```bash
cd config_backup_web
pip install -r requirements.txt
```

### 3. 配置环境变量（可选）

| 变量 | 说明 | 默认 |
|------|------|------|
| CONFIG_BACKUP_ROOT | 数据根目录（configs、log、数据库等） | 项目内 `data/` |
| DATABASE_URL | 数据库连接 | `sqlite:///config_backup.db`（相对项目目录） |
| BACKUP_USERNAME | 默认 Telnet/SSH 用户名 | 见 config.py |
| BACKUP_PASSWORD | 默认 Telnet/SSH 密码 | 见 config.py |
| BACKUP_CONNECTION_TYPE | 默认连接方式 | `TELNET` 或 `SSH` |
| BACKUP_THREAD_NUM | 备份并发线程数 | `10` |
| SECRET_KEY | Flask 会话密钥 | 生产环境**必须**设置为随机字符串 |
| FLASK_PORT | 监听端口 | `443`（HTTPS 时）/ `80`（HTTP 时） |
| FLASK_HTTPS | 是否启用 HTTPS（自签名证书） | `1`（默认启用） |

**HTTPS 自签名证书**：默认启用时，首次启动会在 `data/certs/` 下自动生成自签名证书（有效期 100 年），需系统已安装 `openssl`。设置 `FLASK_HTTPS=0` 可禁用 HTTPS 改用 HTTP。

生产环境示例：

```bash
export CONFIG_BACKUP_ROOT=/opt/vconfig/data
export DATABASE_URL=sqlite:////opt/vconfig/data/config_backup.db
export SECRET_KEY=your-random-secret-key
```

### 4. 初始化数据库

```bash
flask --app app init-db
```

### 5. 启动服务

**HTTPS（默认，端口 443）**：内置自签名证书，首次启动自动生成（有效期 100 年）：

```bash
# 直接运行（默认 HTTPS 443；Linux/Mac 绑定 443 常需 root：sudo python app.py）
python app.py

# 无 root 时改用其它端口
FLASK_PORT=8443 python app.py
```

**HTTP（端口 80）**：禁用 HTTPS 时：

```bash
FLASK_HTTPS=0 python app.py
# 或
FLASK_HTTPS=0 gunicorn -w 4 -b 0.0.0.0:80 app:app
```

**生产环境**：若使用 Gunicorn，需配合 HTTPS 需额外配置；或仍建议用 Nginx/Caddy 反向代理做 SSL 终结，代理到本机 80。

访问 `https://<服务器IP>`（自签名证书需在浏览器中接受/信任），或 `http://<服务器IP>`（若已设置 `FLASK_HTTPS=0`），使用初始化后的管理员账号登录（或通过「系统设置」创建用户）。

### 6. 首次使用建议

1. 登录后进入「系统设置」→「备份设置」，配置默认 Telnet/SSH 账号、超时、线程数等。
2. 在「设备管理」中添加设备，或使用「批量导入」按「主机名 IP 设备类型 [分组]」格式导入。
3. 在「执行备份」中执行全量备份或单台备份，在「备份日志」与「已备份配置」中查看结果。
4. 可选：在「系统设置」中配置备份失败告警 Webhook、API Token、LDAP 等。

---

## 目录结构

```
config_backup_web/
├── app.py              # Flask 应用入口与路由
├── config.py            # 配置（数据目录、数据库、默认账号等）
├── models.py            # 数据模型
├── backup_service.py    # 备份执行逻辑（Telnet/SSH）
├── compliance.py        # 合规检查
├── device_drivers/      # 设备类型驱动（Cisco、Juniper 等）
├── requirements.txt
├── templates/           # 页面模板
├── static/              # 前端静态资源
├── run.sh / backup.sh   # 启动与备份脚本（可选）
└── data/                # 数据目录（可由 CONFIG_BACKUP_ROOT 指定）
    ├── configs/         # 备份的配置文件
    ├── log/             # 日志
    └── config_backup.db # SQLite 数据库（若使用默认 DATABASE_URL）
```

---

## 定时备份

在「系统设置」→「备份设置」中可配置自动备份频率，内置调度器按设定时间执行，无需单独配置 crontab。支持：

- 每天凌晨 02:00  
- 每周日凌晨 02:00  
- 每 12 小时（0 点和 12 点）  
- 自定义 Cron 表达式（如 `0 2 * * *` 表示每天 02:00）

使用 Gunicorn 多进程时，调度器在主进程中运行，不会重复触发。

---

## 文档与接口

- **API 说明**：见 [API.md](API.md)，含认证方式、常用接口及备份失败 Webhook 格式。
- **代码恢复**：见 [RESTORE.md](RESTORE.md)，用于从备份标签或提交恢复代码版本。
