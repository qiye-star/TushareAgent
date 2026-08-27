"""章节语义树：PyMuPDF 原生 block 上的「字号聚类 + 编号正则」双融合标题检测，构建嵌套 SectionNode。

作用于 LayoutBlock（一个 text block ≈ 一个自然段落，size 为块内 span 字号）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from statistics import median

from demomcp.rag.schemas import LayoutBlock, SectionNode

# 有序正则（按优先级）：层级越小越靠前
_REGEX_FUSION: tuple[tuple[int, re.Pattern[str]], ...] = (
    (1, re.compile(r"^(第[一二三四五六七八九十百]+[章编])")),
    (2, re.compile(r"^(第[一二三四五六七八九十]+节)")),
    (3, re.compile(r"^\d+(\.\d+)*\s")),
    (4, re.compile(r"^[一二三四五六七八九十]+、")),
    (5, re.compile(r"^（[一二三四五六七八九十]+）")),
)


@dataclass(frozen=True)
class HeadingCandidate:
    heading: str
    level: int
    page: int
    size: float
    block_index: int


def _regex_level(text: str) -> int | None:
    stripped = text.strip()
    for level, pattern in _REGEX_FUSION:
        if pattern.match(stripped):
            return level
    return None


_TOC_MARKERS = {"目录", "CONTENTS"}
_TOC_ENTRY_RE = re.compile(r"[。.…··]{3,}\s*\d+\s*$")
_HEADER_REPEAT_PAGES = 5  # 同一短文本出现在 ≥5 个不同页 → 视为 running header（页眉/页脚标题），非真标题


def _is_toc_entry(text: str) -> bool:
    """目录条目：点线导引 + 尾页码（如「第八节 财务报告 ……………… 页码」）。"""
    stripped = text.strip()
    if len(stripped) > 200:
        return False
    return bool(_TOC_ENTRY_RE.search(stripped))


def _is_toc_marker(text: str) -> bool:
    """目录页标记（不含空格归一化）。"""
    return text.strip().replace(" ", "") in _TOC_MARKERS


def _is_running_header(text: str, page_counts: dict[str, set[int]]) -> bool:
    """跨多页重复的短文本（如报告标题作页眉/页脚）不是真标题。"""
    pages = page_counts.get(text)
    return bool(pages) and len(pages) >= _HEADER_REPEAT_PAGES


def cluster_font_sizes(sizes: list[float], *, eps: float = 0.5) -> list[tuple[float, int]]:
    """字号聚类：返回 [(代表字号, 层级序号)]，字号越大层级序号越小(level 越小)。"""
    if not sizes:
        return []
    distinct = sorted(set(sizes), reverse=True)
    clusters: list[tuple[float, int]] = []
    group = [distinct[0]]
    for size in distinct[1:]:
        if group and group[-1] - size <= eps:
            group.append(size)
        else:
            clusters.append((group[0], len(clusters) + 1))
            group = [size]
    clusters.append((group[0], len(clusters) + 1))
    return clusters


def _level_for_size(size: float, clusters: list[tuple[float, int]]) -> int:
    for rep, level in clusters:
        if rep - size <= 1e-6 or (rep - size) <= 0.5:
            return level
    return min(clusters, key=lambda x: abs(x[0] - size))[1]


def detect_headings(
    blocks: list[LayoutBlock],
    *,
    body_size: float | None = None,
) -> list[HeadingCandidate]:
    """字号聚类（主）+ 编号正则（校验）双融合，输出去重后的标题候选。"""
    text_sizes = [b.size for b in blocks if b.kind == "text" and b.size > 0]
    if not text_sizes:
        return []
    body_size = body_size if body_size is not None else median(text_sizes)
    clusters = cluster_font_sizes(text_sizes)
    body_level = _level_for_size(body_size, clusters)

    # running-header 过滤：统计每个短文本出现的不同页数；跨页重复者为页眉/页脚
    page_counts: dict[str, set[int]] = {}
    for b in blocks:
        if b.kind == "text":
            t = b.text.strip()
            if t and len(t) <= 80:
                page_counts.setdefault(t, set()).add(b.page)

    candidates: list[HeadingCandidate] = []
    prev_key: tuple[int, int] | None = None  # (page, level) 去重连续同级

    for idx, block in enumerate(blocks):
        if block.kind != "text" or block.size <= 0 or not block.text.strip():
            prev_key = None
            continue
        # 跳过目录条目（点线导引 + 尾页码）与目录页标记，避免 TOC 污染章节树
        if _is_toc_entry(block.text) or _is_toc_marker(block.text):
            prev_key = None
            continue
        stripped = block.text.strip()
        # 跨多页重复的短标题 = running header（页眉/页脚），不作为标题
        if _is_running_header(stripped, page_counts):
            prev_key = None
            continue
        regex_level = _regex_level(block.text)
        font_heading = _level_for_size(block.size, clusters) < body_level
        distinct = (block.size - body_size) >= 1.5

        # 长文本段落（即使以编号开头）不是标题：非大字号的长块一律否决，避免正文被误判为子标题
        if len(stripped) > 50 and not distinct:
            prev_key = None
            continue

        # 含句读的正文片段（如「3.0 Evo」打造，标配「天神之眼C」…」）不是标题：真实标题（第X节/3.2/一、）不含 ，；。
        if any(p in stripped for p in "，；。"):
            prev_key = None
            continue

        if regex_level is not None and distinct:
            level = _level_for_size(block.size, clusters)  # 双一致：按字号定级
        elif regex_level is not None:
            level = regex_level + 1  # 仅正则、字号近正文 → 降一等
        elif font_heading and distinct:
            level = _level_for_size(block.size, clusters)  # 仅字号显著
        else:
            prev_key = None
            continue

        # 同页连续同级去重（多行标题拆块时取首块）
        if (block.page, level) == prev_key:
            continue
        candidates.append(
            HeadingCandidate(
                heading=block.text.strip(), level=level,
                page=block.page, size=block.size, block_index=idx,
            )
        )
        prev_key = (block.page, level)
    return candidates


def build_section_tree(
    candidates: list[HeadingCandidate],
    *,
    doc_id: str,
    total_pages: int,
    max_level: int = 6,
) -> SectionNode:
    """按文档顺序构建嵌套 SectionNode：推 page_end、栈建树、填 path/id。"""
    root = SectionNode(
        id=_hash(f"{doc_id}|")[:12], heading=doc_id, level=0,
        path=[], page_start=1, page_end=max(total_pages, 1),
    )
    if not candidates:
        return root

    page_ends = _assign_page_ends(candidates, total_pages)
    stack: list[tuple[SectionNode, int]] = [(root, 0)]

    for cand, page_end in zip(candidates, page_ends):
        cand_level = min(cand.level, max_level)
        node = SectionNode(
            id=_hash(f"{doc_id}|")[:12], heading=cand.heading, level=cand_level,
            path=[], page_start=cand.page, page_end=max(page_end, cand.page),
        )
        while stack and stack[-1][1] >= cand_level:
            stack.pop()
        parent, _ = stack[-1]
        node.path = parent.path + [cand.heading]
        node.id = _hash(f"{doc_id}|{'>'.join(node.path)}")[:12]
        parent.children.append(node)
        stack.append((node, cand_level))
    return root


def _assign_page_ends(candidates: list[HeadingCandidate], total_pages: int) -> list[int]:
    ends: list[int] = []
    for i, cand in enumerate(candidates):
        pe = total_pages
        for j in range(i + 1, len(candidates)):
            if candidates[j].level <= cand.level:
                pe = candidates[j].page
                break
        ends.append(pe)
    return ends


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()
