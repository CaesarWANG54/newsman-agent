# 本地开发与运行

## 端口与配置

Compose 默认网页端口 3300、API 端口 8300。修改 `.env` 中 `NEWSMAN_WEB_PORT` / `NEWSMAN_API_PORT` 后，用 `docker compose up --build -d` 重建前端；浏览器使用的 API 地址在构建时写入。

PostgreSQL 和 Redis 只在 Compose 网络内访问，没有映射宿主机端口。服务端渲染使用 `NEWSMAN_API_INTERNAL_URL=http://api:8000`，浏览器使用 `NEXT_PUBLIC_NEWSMAN_API_BASE_URL=http://localhost:8300`。修改网址时应同时保持 CORS 的 `NEWSMAN_FRONTEND_ORIGIN` 一致。

只绑定本机并不等于具备多人鉴权。该版本不面向远程多用户部署。

## 查看状态

```bash
docker compose ps
docker compose logs --tail=100 api worker frontend
curl http://localhost:8300/health
curl http://localhost:8300/api/runtime/status
```

`/health` 表示 API 进程存活；`/api/runtime/status` 区分数据库、队列与 worker 状态。Docker 调度器未接入原生进程登记时，界面可能显示调度未观测；可从 `docker compose --profile schedule logs beat` 确认具体执行，不把“已配置”当作“已运行”。

来源只有在当前链接和正文检查通过后才能启用。请求失败、过期内容和缺少翻译会保留对应状态；不会用样例新闻填补真实日报。

## 本机开发

先按 README 安装后端和前端依赖。准备独立的 PostgreSQL 16（含 pgvector）与 Redis，再在项目根目录 `.env` 中设置连接：

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/newsman
REDIS_URL=redis://localhost:6379/0
NEWSMAN_FRONTEND_ORIGIN=http://localhost:3300
NEWSMAN_DAILY_UPDATE_ENABLED=false
NEWSMAN_LLM_PROVIDER=none
```

从 `backend/` 运行：

```bash
python -m newsman.cli.init_db
uvicorn newsman.main:app --host 127.0.0.1 --port 8300
```

另开一个已激活后端环境的终端启动 worker：

```bash
celery -A newsman.celery_app worker --loglevel=INFO --pool=solo
```

在 `frontend/.env.local` 中设置 `NEXT_PUBLIC_NEWSMAN_API_BASE_URL=http://localhost:8300`，再从 `frontend/` 运行：

```bash
npm run dev -- --hostname 127.0.0.1 --port 3300
```

`.env`、日报、运行日志和本机数据库不属于源码。不要把它们加入提交或问题附件。

## 数据与导出

- PostgreSQL 保存来源、文章、任务、阅读偏好和日报版本。
- Redis 保存队列；Compose 开启 AOF，数据位于独立 volume。
- 日报位于容器 `/app/digests` 的共享 volume。快照按日期与版本保存。
- 备份应包含 PostgreSQL 和日报 volume。此版本没有自动生产备份服务。

## 范围

仓库交付浏览器工作台和通用容器部署。原个人电脑上的 WSL 快捷方式、原生桌面壳与系统计划任务不属于此版本的安装流程。邮件、Notion 和飞书推送没有实现。
