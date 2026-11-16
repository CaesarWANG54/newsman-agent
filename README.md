# NewsMan Agent

把分散的 AI 新闻整理成可追溯、可回看的中英双语日报。

NewsMan 采集已启用的来源，将同一事件的报道归组，保留原文链接，并保存日报版本。适合需要持续阅读 AI 产品、研究和行业动态的个人。

## 能做什么

- 采集 RSS 与网页来源，检查链接和正文可用性。
- 归并相关报道，按时间、主题和重要性组织阅读。
- 展示英文在上、中文在下的摘要，并单独保留原文入口。
- 查询历史日期和版本，搜索、收藏、比较与回退阅读快照。
- 导出 Markdown 和 JSON；支持手动更新及可选的每日调度。

未配置模型时使用来源摘录，缺少的中文翻译保持待生成状态。[合成日报样例](examples/sample-digest.md)展示输出结构。

## 本地启动

需要 Docker Engine 和 Docker Compose v2；Windows 可使用 Docker Desktop 的 Linux 容器模式。

```bash
git clone https://github.com/CaesarWANG54/newsman-agent.git
cd newsman-agent
cp .env.example .env
docker compose up --build -d
```

Windows PowerShell 可用 `Copy-Item .env.example .env` 复制配置。

打开 [阅读工作台](http://localhost:3300)，接口说明在 [API 文档](http://localhost:8300/docs)。首次启动会初始化数据库与来源目录，全部来源默认停用。

1. 在来源列表点击“检查”，通过检查后启用需要的来源。
2. 点击“更新日报”，等待任务完成，再刷新阅读快照。
3. 展开“AI Summary / AI 总结”阅读摘要，点击“Original”查看原文。

来源检查和采集需要联网。启用模型摘要时，在 `.env` 设置：

```dotenv
NEWSMAN_LLM_PROVIDER=openai
OPENAI_API_KEY=填入你自己的密钥
OPENAI_MODEL=gpt-4.1-mini
```

然后执行 `docker compose up -d` 使配置生效。模型和 embedding 调用使用你自己的账户；默认不会调用模型。

## 保存与调度

数据库、队列和日报导出使用独立 Docker volumes。`docker compose down` 停止服务并保留数据；不要用 `down -v` 停止日常服务，因为它会删除这些卷。

需要每日运行时，把 `.env` 中 `NEWSMAN_DAILY_UPDATE_ENABLED` 改为 `true`，设置时区和时间，再执行：

```bash
docker compose --profile schedule up -d
```

调度依赖容器持续运行。模型摘要与来源报道均应结合原文判断，来源引用本身不代表事实已获独立核验。

## 开发与验证

技术栈：FastAPI、PostgreSQL/pgvector、Celery、Redis、Next.js、React、TypeScript。

Python 3.12、Node.js 24：

```bash
cd backend
python -m venv .venv
# 激活 .venv 后：
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps -e .
python -m pytest tests -q

cd ../frontend
npm ci
npm test
npm run lint
npm run build
```

后端单元测试阻断真实网络、数据库和模型调用。Linux 进程管理测试会在 Windows 跳过，CI 同时检查 Linux 后端、前端和 Docker 启动。

更多说明：[本地开发与运行](docs/OPERATIONS.md) · [架构](docs/ARCHITECTURE.md) · [第三方组件](THIRD_PARTY_NOTICES.md)

## 使用范围与版权

当前版本面向本机单用户，Compose 只映射回环地址，没有公网账户与访问控制。不要直接暴露到公网。

Copyright © 2026 CaesarWANG54。公开展示，保留代码版权；本仓库不是开源许可项目，使用或二次开发请先取得授权。见 [LICENSE](LICENSE)。新闻原文和第三方组件遵循各自的权利与许可。
