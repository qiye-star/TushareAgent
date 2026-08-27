#!/bin/sh
# 容器入口：bake 的预建 RAG 索引（/app/seeded_vectorstore）首启种子进 /app/data 卷，再执行 CMD。
#
# 背景：compose 挂 /app/data 为命名卷 demo_data（首启为空），会覆盖镜像里原位的 /app/data/vectorstore，
#       使 has_index=False -> 容器无检索。这里把镜像内索引拷进卷（仅当卷里还没有 rag_rel.db），
#       从而 build_runtime_retriever 走 has_index -> load_index，与本地加载**同一份**索引。
#
# Milvus-Lite 单写锁：同一份 /app/data/vectorstore/milvus.db 只允许一个进程/容器打开；
#       勿多副本共享，勿同机并开本地 web（会 DataDirLockedError）。
set -e

SEED=/app/seeded_vectorstore
TARGET=/app/data/vectorstore

if [ -d "$SEED" ] && [ -f "$SEED/rag_rel.db" ] && [ ! -f "$TARGET/rag_rel.db" ]; then
    echo "[entrypoint] seeding RAG vectorstore from image into volume ..."
    mkdir -p "$TARGET"
    cp -a "$SEED/." "$TARGET/"
fi

exec "$@"
