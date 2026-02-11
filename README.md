# 配置备份 Web 管理

将原 `telneter.py` 命令行备份脚本改造为 Web 界面管理，支持设备管理、一键备份、日志查看、配置文件浏览。

## 功能

- **设备管理**：添加/编辑/删除设备，支持批量导入 `ip_list` 格式
- **备份执行**：Web 点击立即备份，多线程并发（默认 10 线程）
- **备份日志**：查看每次备份结果（OK / Fail_Network / Fail_Login / Fail）
- **配置浏览**：按站点/主机浏览已备份的配置文件
- **全局设置**：默认 Telnet 用户名/密码，支持设备独立账号
- **支持设备类型**：Cisco、Juniper、Huawei、H3C、RouterOS

## 部署

### 1. 安装依赖

```bash
cd config_backup_web
pip install -r requirements.txt
```

### 2. 配置环境变量（可选）

| 变量 | 说明 | 默认 |
|------|------|------|
| CONFIG_BACKUP_ROOT | 数据根目录（configs、log） | 项目内 data/ |
| DATABASE_URL | 数据库连接 | sqlite:///config_backup.db |
| BACKUP_USERNAME | 默认 Telnet 用户名 | coniadmin |
| BACKUP_PASSWORD | 默认 Telnet 密码 | C0niC1Oud@auth |
| BACKUP_THREAD_NUM | 并发线程数 | 10 |

生产环境建议：

```bash
export CONFIG_BACKUP_ROOT=/home/config_backup
export DATABASE_URL=sqlite:////home/config_backup/config_backup.db
```

### 3. 初始化并导入设备

```bash
# 初始化数据库
flask --app app init-db

# 从 ip_list 导入设备（需在项目目录或 /home/config_backup 下存在 ip_list）
flask --app app import-ip-list
```

### 4. 启动

**开发：**

```bash
python app.py
# 或
flask --app app run --host 0.0.0.0 --port 5000
```

**生产（gunicorn）：**

```bash
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

访问 `http://服务器IP:5000` 即可使用。

## 目录结构

```
config_backup_web/
├── app.py           # Flask 应用
├── config.py        # 配置
├── models.py        # 数据模型
├── backup_service.py# Telnet 备份逻辑
├── ip_list          # 示例设备列表
├── requirements.txt
├── templates/
├── static/
└── data/            # 数据目录（自动创建）
    ├── configs/     # 备份的配置文件
    ├── log/         # 日志
    └── config_backup.db
```

## 定时备份

在「系统设置」→「备份设置」中可配置自动备份频率，内置调度器会按设定时间自动执行备份，无需配置 crontab。支持：
- 每天凌晨 02:00
- 每周日凌晨 02:00
- 每 12 小时（0 点和 12 点）
- 自定义 Cron 表达式（如 `0 2 * * *` 表示每天 02:00）

使用 gunicorn 多进程时，调度器在主进程中运行，不会重复触发。

## 从原 telneter.py 迁移

1. 将原 `ip_list` 复制到项目目录或 `/home/config_backup/`
2. 执行 `flask --app app import-ip-list` 导入设备
3. 在 Web 设置中配置 Telnet 账号
4. 点击「立即备份」测试
5. 原排除规则（OOB/4G/LTM/NTA/SSL）已保留在 `config.py` 的 `EXCLUDE_PATTERNS`
