# demo-mcp — LLM+MCP 数据对话助手（Web/CLI）
# 构建上下文只含 demo-mcp/（零外部路径）。应用经 TUSHARE_MCP_URL 连接 Tushare 官方 MCP；
# 内置 mcp_server/（本地代理→每接口工具）默认停用，仅作后备。
# 同一份镜像也用于 mcp_gateway/（独立部署的 MCP 网关，见 docker-compose.yml 的 mcp-gateway service，
# 只是换 CMD），所以这里把 mcp_gateway/ 也一并拷进去，不单独建 Dockerfile。

# ---------- 阶段 1：构建 React 前端（scripts/web/dist） ----------
FROM node:20-alpine AS webbuild
WORKDIR /webbuild
# 先只拷清单以复用依赖缓存
COPY scripts/web/package.json scripts/web/package-lock.json ./
RUN npm ci
COPY scripts/web/vite.config.ts scripts/web/tsconfig.json scripts/web/index.html ./
COPY scripts/web/src ./src
RUN npm run build

# ---------- 阶段 2：Python 运行时 ----------
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
ENV UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

# 依赖清单先入镜像（利用层缓存）
COPY pyproject.toml uv.lock ./
# 应用源码（含内置 mcp_server/ 与独立部署的 mcp_gateway/）
COPY mcp_server/ ./mcp_server/
COPY mcp_gateway/ ./mcp_gateway/
COPY demomcp/ ./demomcp/
COPY scripts/ ./scripts/
# 前端构建产物（web.py 静态挂载 scripts/web/dist）
COPY --from=webbuild /webbuild/dist ./scripts/web/dist

# 安装运行时依赖（不带 dev 工具）+ RAG 真实后端（--extra rag-full：pymilvus/milvus-lite/pymupdf/jieba/rank-bm25）
RUN uv sync --no-dev --extra rag-full && \
    useradd -m -u 1000 app && chown -R app:app /app
# 预留数据目录并归 app：卷挂载 /app/data 后仍可写（demo.db / RAG 向量库 / 日志均落 /app）
RUN mkdir -p /app/data && chown app:app /app/data
USER app

EXPOSE 8010
# 用容器内 demo-mcp 自带 .venv 跑 Web；MCP 经 .env 的 TUSHARE_MCP_URL 连官方 Tushare MCP
CMD ["uv", "run", "uvicorn", "demomcp.entry.web:app", "--host", "0.0.0.0", "--port", "8010"]
