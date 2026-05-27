# 星云社生产部署指南

这份文档用于把星云社从本地工具整理成可上线服务。推荐使用 Docker 部署，数据和账号会保存在 `.runtime-cache` 对应的持久化卷里。

## 生产部署

1. 准备生产环境变量：

```bash
cp .env.production.example .env.production
```

编辑 `.env.production`，至少确认这些配置：

```bash
XINGYUN_PUBLIC_BASE_URL=https://your-domain.com
XINGYUN_COOKIE_SECURE=1
XINGYUN_EXPOSE_DEV_EMAIL_CODE=0
XINGYUN_EXPOSE_DEV_PHONE_CODE=0
XINGYUN_DISABLE_DESKTOP_ALERT=1
EMAIL_SMTP_HOST=...
EMAIL_SMTP_FROM=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
DEEPSEEK_API_KEY=...
```

Google OAuth 回调地址配置为：

```text
https://your-domain.com/api/auth/google/callback
```

2. 启动服务：

```bash
.\docker-start.ps1
```

3. 检查健康状态：

```bash
curl http://127.0.0.1:8765/api/health
```

返回 `ok: true` 说明后端、数据库和运行缓存目录可用。

## 反向代理

建议在服务器上使用 Caddy、Nginx 或 Cloudflare Tunnel 提供 HTTPS。Nginx 示例：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

上线后务必使用 HTTPS，并保持 `XINGYUN_COOKIE_SECURE=1`。

## 数据持久化

Docker Compose 默认把运行数据放在 `xingyunshe_runtime` 卷中，里面包括：

- 用户、会话、TodoList、X 追踪配置
- RSS/公众号缓存
- 桌面弹窗去重记录
- 榜单和 DeepSeek 解析缓存

备份时导出这个卷即可。不要把 `.runtime-cache` 或 `.env.production` 提交到 GitHub。

## 本地功能和线上限制

这些能力适合留在本机使用：

- Windows 右下角/左下角桌面弹窗
- AIcoin 桌面客户端 DevTools 数据
- OKX 桌面客户端本地代理数据

云端部署默认关闭桌面弹窗：`XINGYUN_DISABLE_DESKTOP_ALERT=1`。如果需要上线后仍然通知，可以后续接入邮件、Telegram、Discord Webhook 或浏览器通知。

## 安全清单

- `.env.production` 不入库。
- 生产环境不要加载 `.env.example`，保持 `XINGYUN_LOAD_ENV_EXAMPLE=0`。
- 验证码不要回显，保持 `XINGYUN_EXPOSE_DEV_EMAIL_CODE=0` 和 `XINGYUN_EXPOSE_DEV_PHONE_CODE=0`。
- 管理员账号使用强密码。
- 开启 HTTPS 后启用 `XINGYUN_COOKIE_SECURE=1`。
- 定期备份运行数据卷。
- 如果开放后台管理，建议在反向代理层增加 IP 白名单或二次认证。

## 常用命令

```bash
docker compose ps
docker compose logs -f
docker compose restart
docker compose pull
.\docker-start.ps1
```
