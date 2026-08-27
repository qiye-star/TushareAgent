# demo-mcp — LLM+MCP 数据对话助手（Web/CLI）
# 构建上下文只含 demo-mcp/（零外部路径）。应用经 TUSHARE_MCP_URL 连接 Tushare 官方 MCP；
# 内置 mcp_server/（本地代理→每接口工具）默认停用，仅作后备。

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
# 应用源码（含内置 mcp_server/）
COPY mcp_server/ ./mcp_server/
COPY demomcp/ ./demomcp/
COPY scripts/ ./scripts/
# 前端构建产物（web.py 静态挂载 scripts/web/dist）
COPY --from=webbuild /webbuild/dist ./scripts/web/dist
# 预建 RAG 索引（Milvus-Lite + SQLite + BM25）：bake 到非被卷遮挡的路径，供入口脚本首启种子进 /app/data 卷。
# 必须与业务代码同源生成（含策略/改写/表格保底），否则加载行为偏旧。
COPY data/vectorstore/ ./seeded_vectorstore/

# 入口脚本：把 bake 的索引进卷（若卷为空），再进入 CMD（须在 chmod 之前 COPY）
COPY docker/entrypoint.sh /app/entrypoint.sh
# 安装运行时依赖（不带 dev 工具；RAG 真引擎需 rag-full：pymilvus/milvus-lite/pymupdf/rank-bm25/jieba）
RUN uv sync --no-dev --extra rag-full && \
    useradd -m -u 1000 app && chown -R app:app /app && \
    chmod +x /app/entrypoint.sh
# 预留数据目录并归 app：卷挂载 /app/data 后仍可写（demo.db / RAG 向量库 / 日志均落 /app）
RUN mkdir -p /app/data && chown app:app /app/data
USER app

EXPOSE 8010
# 用容器内 demo-mcp 自带 .venv 跑 Web；MCP 经 .env 的 TUSHARE_MCP_URL 连官方 Tushare MCP
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uv", "run", "uvicorn", "demomcp.entry.web:app", "--host", "0.0.0.0", "--port", "8010"]
