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

## 安装部署（一键脚本）

### 1. 运行环境

- **操作系统**：主流 x86_64 Linux（如 Ubuntu 20.04+/22.04+/24.04、Debian 10+、CentOS Stream 8/9、RHEL 8/9、Rocky/Alma 等），支持 systemd。
- **权限要求**：建议使用具有 sudo 权限的账号部署（通常安装到 `/opt`）。
- **网络要求**：部署机器需能通过 **SSH 访问 GitHub**（本仓库为私有仓库）。

一键脚本会自动检测并安装以下依赖：OpenSSL、Python 3.8+ 及虚拟环境与 pip、SNMP 客户端工具 `snmpwalk`（用于手工测试 SNMP）。

### 2. 在 GitHub 配置 SSH 访问（私有仓库必读）

在部署机器上克隆代码前，须先在 GitHub 添加 SSH Key，并在该机器上完成 GitHub 认证。

**步骤 1：在部署机器上生成 SSH 密钥（如已有可跳过）**

```bash
ssh-keygen -t rsa
```

按提示一路回车即可，默认生成在 `~/.ssh/id_rsa`。

**步骤 2：查看并复制公钥**

```bash
cat ~/.ssh/id_rsa.pub
```

将输出内容完整复制备用。

**步骤 3：在 GitHub 上添加 SSH Key**

1. 登录 GitHub，进入 **Settings → SSH and GPG keys → New SSH key**。
2. 填写标题，将上一步复制的公钥粘贴到 Key 框中并保存。

**步骤 4：在部署机器上验证 GitHub SSH 连接**

```bash
ssh -T git@github.com
```

若看到类似 `Hi xxx! You've successfully authenticated, but GitHub does not provide shell access.` 即表示配置成功。

### 3. 克隆仓库

在部署机器上执行：

```bash
cd /opt
git clone git@github.com:dangleungfai/vConfig.git
cd vConfig
```

如需部署到其他目录，将 `/opt` 替换为目标路径即可。

### 4. 一键部署（run.sh）

在代码目录下执行：

```bash
sudo ./run.sh
```

脚本将自动完成：检查并安装 OpenSSL、Python3、SNMP 客户端等依赖；创建虚拟环境 `./venv` 并安装 `requirements.txt`；初始化数据库（如不存在）并重置管理员密码；生成 HTTPS 自签名证书（存放于 `data/certs/`）；注册并启动 systemd 服务 `vconfig`；询问监听端口（默认 443）并输出访问 URL。

部署完成后，使用以下命令管理服务：

```bash
sudo systemctl status vconfig    # 查看状态
sudo systemctl start vconfig     # 启动
sudo systemctl stop vconfig      # 停止
sudo systemctl restart vconfig   # 重启
```

> vConfig 默认启用 HTTPS，并在 80 端口提供 HTTP→HTTPS 跳转。若本机 80 端口已被 Nginx 等占用，请先停止或调整后再部署。

### 5. 首次登录

使用脚本结束时输出的 URL 访问（自签名证书需在浏览器中接受），默认管理员账号：

- 用户名：`admin`
- 密码：`admin123`

登录后请尽快在「系统设置 → 用户管理」中修改管理员密码，并视需要创建运维账号与只读账号。

---

## 目录结构

```
vConfig/
├── vconfig.service     # systemd 服务单元（Linux 自动安装）
├── app.py              # Flask 应用入口与路由
├── config.py           # 配置（数据目录、数据库、默认账号等）
├── models.py           # 数据模型
├── backup_service.py   # 备份执行逻辑（Telnet/SSH）
├── compliance.py       # 合规检查
├── device_drivers/     # 设备类型驱动（Cisco、Juniper 等）
├── requirements.txt
├── templates/          # 页面模板
├── static/             # 前端静态资源
├── run.sh              # 一键部署与启动脚本
└── data/               # 数据目录
    ├── configs/        # 备份的配置文件
    ├── log/            # 日志
    └── vconfig.db      # SQLite 数据库（若使用默认配置）
```

---

## 定时备份

在「系统设置 → 备份设置」中可配置自动备份频率，内置调度器按设定时间执行，无需单独配置 crontab。支持每天凌晨 02:00、每周日凌晨 02:00、每 12 小时及自定义 Cron 表达式（如 `0 2 * * *`）。

---

## 文档与接口

- **API 说明**：见 [API.md](API.md)，含认证方式、常用接口及备份失败 Webhook 格式。
- **代码恢复**：见 [RESTORE.md](RESTORE.md)，用于从备份标签或提交恢复代码版本。
