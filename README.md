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

- **Python 3.8+**
- **OpenSSL**（必须）：用于生成 HTTPS 自签名证书，未安装则默认 HTTPS 无法启用
- 建议使用虚拟环境

**安装 OpenSSL**（按系统选择其一）：

```bash
# Debian/Ubuntu
sudo apt-get update && sudo apt-get install -y openssl

# CentOS/RHEL
sudo yum install -y openssl openssl-libs

# macOS（通常已预装，缺失时）
brew install openssl
```

### 2. 安装依赖

```bash
cd vConfig
pip install -r requirements.txt
```

### 3. 配置环境变量（可选）

| 变量 | 说明 | 默认 |
|------|------|------|
| CONFIG_BACKUP_ROOT | 数据根目录（configs、log、数据库等） | 项目内 `data/` |
| DATABASE_URL | 数据库连接 | `sqlite:///vconfig.db`（相对项目目录） |
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
export DATABASE_URL=sqlite:////opt/vconfig/data/vconfig.db
export SECRET_KEY=your-random-secret-key
```

### 4. 初始化数据库

```bash
flask --app app init-db
```

### 5. 一键部署（推荐）

**Linux**（自动安装 systemd 服务，开机自启）：

```bash
cd vConfig
sudo ./run.sh
```

脚本会自动安装 OpenSSL、Python3、Nginx（可选），创建虚拟环境，初始化数据库，并注册为 systemd 服务 `vconfig`。部署完成后使用 systemctl 管理：

```bash
sudo systemctl status vconfig   # 查看状态
sudo systemctl start vconfig   # 启动
sudo systemctl stop vconfig    # 停止
sudo systemctl restart vconfig # 重启
```

**macOS 或非 systemd 系统**：运行 `./run.sh` 将以前台方式启动，Ctrl+C 停止。

### 6. 手动启动

**HTTPS（默认，端口 443）**：

```bash
python app.py
FLASK_PORT=8443 python app.py   # 无 root 时
```

**HTTP**：`FLASK_HTTPS=0 python app.py`

访问 `https://<服务器IP>`（自签名证书需在浏览器中接受/信任），使用初始化后的管理员账号（admin / admin123）登录。

### 7. 首次使用建议

1. 登录后进入「系统设置」→「备份设置」，配置默认 Telnet/SSH 账号、超时、线程数等。
2. 在「设备管理」中添加设备，或使用「批量导入」按「主机名 IP 设备类型 [分组]」格式导入。
3. 在「执行备份」中执行全量备份或单台备份，在「备份日志」与「已备份配置」中查看结果。
4. 可选：在「系统设置」中配置备份失败告警 Webhook、API Token、LDAP 等。

---

## 目录结构

```
vConfig/
├── vconfig.service     # systemd 服务单元（Linux 自动安装）
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
    └── vconfig.db       # SQLite 数据库（若使用默认 DATABASE_URL）
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
