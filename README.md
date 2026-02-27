# vConfig — 网络设备配置备份与变更管理系统

vConfig 是一套面向企业网络运维场景的 **配置备份与变更管理** Web 平台。通过 Telnet/SSH 连接主流厂商网络设备，实现配置的自动拉取、版本留存、差异对比与变动告警，支持多用户与分组权限、定时备份、Webhook 告警及 SNMP 自动发现，便于集中管控与审计。

---

## 功能特性

### 设备与备份

| 功能 | 说明 |
|------|------|
| **设备管理** | 添加/编辑/删除设备，支持分组、设备类型、批量导入（格式：主机名 IP 类型 [分组]） |
| **备份执行** | 全量备份与单台立即备份，多线程并发，支持 Telnet/SSH，可配置超时、端口及连接方式 |
| **备份日志** | 按时间查看每次备份任务的结果（成功/失败及失败原因） |
| **支持设备类型** | Cisco、Juniper、Huawei、H3C、RouterOS 等（内置驱动，可扩展自定义类型与驱动） |

### 配置管理与检索

| 功能 | 说明 |
|------|------|
| **已备份配置** | 按设备浏览配置文件列表，支持下载、版本对比（最新 vs 上一份）、单设备历史与 diff |
| **配置变动** | 集中展示最近一次备份中各设备的配置变动（新增/删除命令），支持按设备查看明细 |
| **配置全文搜索** | 在已备份配置中按关键字搜索 |
| **合规检查** | 基于规则的配置合规检测（可选模块） |

### 安全与权限

| 功能 | 说明 |
|------|------|
| **多用户与角色** | 支持管理员、运维、只读三种角色，可按设备分组限制可见与可操作范围 |
| **认证方式** | 本地账号与 LDAP 可选，支持 API Token 调用接口 |

### 运维与发现

| 功能 | 说明 |
|------|------|
| **仪表盘** | 备份统计、未备份设备、配置变动概览、最近登录与安全审计 |
| **自动发现** | 基于 SNMP 的自动发现规则，可按 IP 段扫描并自动加入设备列表（需配置主机名与系统类型 OID） |

### 系统设置

| 功能 | 说明 |
|------|------|
| **备份与告警** | 默认 Telnet/SSH 账号、备份超时与线程数、备份失败 Webhook（企业微信/钉钉/飞书/Slack/Teams 等，支持测试） |
| **系统参数** | 时区、会话超时、登录锁定、密码复杂度、每页条数等 |
| **证书与访问** | HTTPS 自签名或自有域名证书上传，80 端口 HTTP 自动跳转 HTTPS |

---

## 系统架构

### 技术栈

- **后端**：Python 3.8+，Flask，SQLAlchemy（SQLite）
- **前端**：原生 HTML/CSS/JavaScript，无前端框架依赖
- **设备连接**：Paramiko（SSH）、Telnetlib（Telnet）
- **可选**：PySNMP（自动发现）、LDAP 集成

### 架构概览

```
                    ┌─────────────────────────────────────────┐
                    │              浏览器（Web UI）              │
                    └─────────────────────┬───────────────────┘
                                          │ HTTPS
                    ┌─────────────────────▼───────────────────┐
                    │              Flask 应用 (app.py)           │
                    │  路由 / API / 认证 / 权限 / 定时任务调度    │
                    └─────┬──────────────┬──────────────┬───────┘
                          │              │              │
            ┌─────────────▼──┐  ┌────────▼────────┐  ┌──▼─────────────┐
            │   backup_      │  │  device_drivers │  │  models /      │
            │   service.py   │  │  (Cisco/Juniper │  │  SQLite 数据库  │
            │  SSH/Telnet    │  │  /Huawei/...)   │  │  data/configs  │
            └───────────────┘  └─────────────────┘  └────────────────┘
```

- **Web 层**：提供仪表盘、设备管理、备份任务、日志、配置浏览与对比、系统设置等页面及 REST API。
- **业务层**：Flask 应用负责会话与权限、设备 CRUD、备份任务调度、Webhook 通知、用户与设置管理。
- **备份与驱动层**：`backup_service` 通过 SSH/Telnet 连接设备，按 `device_drivers` 中各厂商的登录流程与命令执行配置拉取。
- **数据层**：SQLite 存储设备、用户、设置、备份任务与日志；配置文件按设备落盘至 `data/configs/`。

### 项目目录结构

```
vConfig/
├── app.py              # Flask 应用入口、路由与 API
├── config.py           # 默认配置（数据目录、数据库、默认备份账号等）
├── models.py           # 数据模型（设备、用户、设置、备份任务等）
├── backup_service.py   # 备份执行逻辑（SSH/Telnet 连接与命令执行）
├── compliance.py       # 合规检查模块
├── device_drivers/     # 设备类型驱动（Cisco、Juniper、Huawei、H3C、RouterOS 及通用/自定义）
├── requirements.txt    # Python 依赖
├── run.sh              # 一键部署与启动脚本
├── vconfig.service     # systemd 服务单元（run.sh 自动安装）
├── templates/          # 页面模板
├── static/             # 前端静态资源（JS/CSS）
└── data/               # 数据目录（可由环境变量指定）
    ├── configs/        # 各设备备份配置文件
    ├── log/            # 日志
    └── vconfig.db      # SQLite 数据库（默认）
```

---

## 安装部署（一键脚本）

### 1. 运行环境

- **操作系统**：推荐 x86_64 Linux（如 Ubuntu 20.04+/22.04+/24.04、Debian 10+、CentOS Stream 8/9、RHEL 8/9、Rocky/Alma 等），支持 systemd。
- **权限**：建议使用具备 sudo 权限的账号，安装目录通常为 `/opt`。
- **网络**：部署机器需能访问 GitHub 以克隆仓库（公网即可，无需 SSH 配置即可使用 HTTPS 克隆）。

一键脚本会自动检测并安装：OpenSSL、Python 3.8+ 及虚拟环境与 pip、SNMP 客户端工具 `snmpwalk`（可选，用于手工测试 SNMP）。

### 2. 安装 Git 并克隆仓库

在部署机器上先更新包索引并安装 git（若已安装可跳过）：

```bash
apt update
apt install -y git
```

然后克隆仓库：

```bash
cd /opt
git clone https://github.com/dangleungfai/vConfig.git
cd vConfig
```

使用 SSH 克隆（需已在 GitHub 配置 SSH Key）：

```bash
git clone git@github.com:dangleungfai/vConfig.git
```

### 3. 一键部署

在代码目录下执行：

```bash
sudo ./run.sh
```

脚本将自动完成：检查并安装 OpenSSL、Python3、SNMP 客户端等依赖；创建虚拟环境 `./venv` 并安装 `requirements.txt`；初始化数据库（如不存在）并重置管理员密码；生成 HTTPS 自签名证书（存放于 `data/certs/`）；注册并启动 systemd 服务 `vconfig`；询问监听端口（默认 443）并输出访问 URL。

部署完成后，使用以下命令管理服务：

```bash
sudo systemctl status vconfig    # 查看状态
sudo systemctl start vconfig    # 启动
sudo systemctl stop vconfig     # 停止
sudo systemctl restart vconfig  # 重启
```

> vConfig 默认启用 HTTPS，并在 80 端口提供 HTTP→HTTPS 跳转。若本机 80 端口已被 Nginx 等占用，请先停止或调整后再部署。

### 4. 首次登录

使用脚本结束时输出的 URL 访问（自签名证书需在浏览器中接受）。默认管理员账号：

- 用户名：`admin`
- 密码：`admin123`

登录后请尽快在「系统设置 → 用户管理」中修改管理员密码，并视需要创建运维账号与只读账号。

---

## 定时备份

在「系统设置 → 备份设置」中可配置自动备份频率，内置调度器按设定时间执行，无需单独配置 crontab。支持：每天凌晨 02:00、每周日凌晨 02:00、每 12 小时及自定义 Cron 表达式（如 `0 2 * * *`）。

---

## 文档与接口

- **[API.md](API.md)**：REST API 说明，含认证方式、常用接口及备份失败 Webhook 格式。
- **[RESTORE.md](RESTORE.md)**：从备份标签或提交恢复代码版本的说明。
