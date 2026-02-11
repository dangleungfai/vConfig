# vConfig 配置备份管理系统 — API 说明

## 认证方式

### 1. 会话认证（浏览器）

登录页 `/login` 使用用户名密码登录后，会话 Cookie 用于后续请求。

### 2. API Token（脚本 / 集成）

在 **系统设置 → 备份设置** 中配置 **API Token**（多个用英文逗号分隔）。请求时在 Header 中携带：

```
Authorization: Bearer <你的 token>
```

通过 Token 认证的请求拥有**管理员**权限（可执行备份、修改设置、管理设备等）。

示例：

```bash
curl -k -H "Authorization: Bearer your-token-here" "https://localhost/api/dashboard"
curl -k -X POST -H "Authorization: Bearer your-token-here" -H "Content-Type: application/json" "https://localhost/api/backup/run"
```

（默认 HTTPS 端口 443，自签名证书需加 `-k` 跳过校验；若使用 HTTP 或其它地址请替换。）

未认证或 Token 错误时，接口返回 `401`，body 为 `{"error": "unauthorized"}`。

---

## 常用接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/dashboard | 仪表盘数据（统计、趋势、未备份数等） |
| GET | /api/dashboard/export-no-backup-24h | 导出 24h 未备份设备 CSV |
| GET | /api/devices | 设备列表（分页，支持 site/device_type/group/search） |
| GET | /api/devices/export | 导出设备列表 CSV |
| POST | /api/devices | 新增设备 |
| PUT | /api/devices/:id | 更新设备 |
| DELETE | /api/devices/:id | 删除设备 |
| POST | /api/devices/import | 批量导入（文本：主机名 IP 类型 [分组]） |
| POST | /api/backup/run | 执行全量备份 |
| GET | /api/backup/status | 当前备份任务状态 |
| GET | /api/logs | 备份日志（分页） |
| GET | /api/settings | 获取系统设置 |
| PUT | /api/settings | 更新系统设置（需管理员） |

---

## 备份失败 Webhook

在 **系统设置 → 备份设置** 中配置 **备份失败告警 Webhook URL**。当备份任务结束且存在失败设备时，系统会向该 URL 发送一次 POST 请求（失败时自动重试 3 次）：

- **Content-Type**: `application/json; charset=utf-8`
- **Body 示例**:
```json
{
  "event": "backup_failure",
  "job_id": "20260206120000",
  "total": 100,
  "ok": 98,
  "fail": 2,
  "end_time": "2026-02-06T12:05:00Z"
}
```
