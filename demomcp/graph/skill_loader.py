"""claude-for 技能库加载器：`SKILL.md` → 本项目的报告 skill 条目。

背景与决策见 `docs/SKILLS_IMPORT_PLAN.md`（D1–D13）。这里只做「解析 + 装配」，**不改写 vendored 正文**：

- (a) 极简 frontmatter 解析：只认 `name:` / `description:`（含 `>` 折叠块），解析失败 → WARNING 跳过、不抛；
- (b) 小节分段：claude-for 正文 62/63 的主体是「Data Sources + Workflow/Step 1..7」**取数工作流**，
      只有 14/63 带独立 Output 小节 → 取数类小节喂 `tool_hint`（tool_rag 节点），输出/写作类喂
      `system_prompt`（synthesizer）。分段必须**跳过 ``` 代码块**（正文在 bash 示例里用 `# 注释` 当标题会误切）；
- (c) 工具名对照：正文里的 `wind_*`/`ifind_*` 是 claude-for 插件环境的具名工具，本项目网关只暴露
      3 个懒发现元工具 → 生成「旧名 → `wind_query(api_name=…)` / `ifind_query(query=…)`」对照段。
      免费源 11 个工具名与 `external_sources/` 同名，无需翻译（只附 Tushare 官方等价建议）；
- (d) 能力降级标注：本环境无文件读写/Excel/PPT 工具，正文命中文件产物关键词 ≥3 次即追加限制说明（纯规则，不维护名单）；
- (e) 纪律段强制包裹：`skill.system_prompt` 在 synthesizer 是**整体替换** `synth_system`（`nodes.py:658`），
      不包这一层就会丢掉本项目的三反编造纪律与「正文不写来源名」等既有约束。

本模块**不 import `skills.py`**（避免循环）：`load_library()` 返回 `LoadedSkill`，由 `skills.py` 转成 `Skill`。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from demomcp.graph.skill_catalog import CATALOG, DOMAIN_LABELS

_log = logging.getLogger(__name__)

# 包内 vendored 语料（demomcp/skill_library/claude-for/vertical-plugins/<域>/skills/<技能>/SKILL.md）
DEFAULT_LIBRARY_DIR = Path(__file__).resolve().parent.parent / "skill_library" / "claude-for"

# 与 china-finance 撞名的域：id 加后缀去重（实测 fund-admin 那 5 条是**同名的另一个技能**，
# 上市公司口径 vs 基金组合口径，Workflow 步骤完全不同 → 两份都留）
ID_SUFFIX_BY_DOMAIN: dict[str, str] = {"fund-admin": "-fund"}

# tool_hint 硬上限：tool_rag 是多轮循环节点，**每轮都带这段**，不设限会在 10 轮里翻十倍
_MAX_SKILL_HINT_CHARS = 3000

# —— 年报 RAG 名单（决策 D7：核心 11 + 扩展 5）——
RAG_SKILL_IDS: frozenset[str] = frozenset({
    "china-3-statement-model", "china-comps", "china-comps-analysis", "china-dcf", "china-dcf-model",
    "china-earnings-analysis", "china-earnings-preview", "china-initiating-coverage", "china-lbo-model",
    "china-model-update", "china-thesis-tracker",
    "china-merger-model", "china-unit-economics", "china-competitive-analysis",
    "china-ic-memo", "china-sector-overview",
})

# —— 免费源（external_sources/）工具：与 claude-for 同名，直接可调 ——
FREE_SOURCE_TOOLS: frozenset[str] = frozenset({
    "search_stock", "get_quote", "get_historical_data", "get_financials", "get_industry_stocks",
    "get_index_data", "get_stock_info", "get_market_overview", "get_fund_data",
    "get_stock_news", "get_market_headlines",
})

# 免费源 → Tushare 官方等价接口（引导优先官方源，权限失败再退免费源）
OFFICIAL_EQUIVALENT: dict[str, str] = {
    "get_financials": "income / fina_indicator / balancesheet / cashflow",
    "get_historical_data": "daily",
    "get_quote": "daily_basic",
    "get_stock_info": "stock_basic",
    "get_index_data": "index_daily",
    "get_stock_news": "news（需权限，40203 时退回免费源）",
}

# 网关真实暴露的元工具：正文里出现这些名字是**对的**，不进对照表
_META_TOOLS: frozenset[str] = frozenset({
    "wind_query", "wind_list_apis", "wind_get_api_info",
    "ifind_query", "ifind_list_apis", "ifind_get_api_info",
})

# 长得像工具名但其实是**配置项**的 `wind_*`/`ifind_*`（正文里讲鉴权/开关时出现）。
# 主防线是「工具名扫描区分大小写」（工具名一律小写，环境变量一律大写：`WIND_API_KEY`），
# 这张表兜住小写写法——不排掉的话会翻出 `wind_query(api_name="api_key")` 这种根本不存在的接口。
_NOT_TOOLS: frozenset[str] = frozenset({
    "wind_api_key", "wind_enabled", "wind_mcp", "wind_mcp_url", "wind_data_source_mode",
    "ifind_auth_token", "ifind_enabled", "ifind_concurrency", "ifind_mcp", "ifind_mcp_url",
    "ifind_data_source_mode",
})

# —— 分段：标题分类正则（大小写不敏感，中英双语）——
_DATA_TITLE = re.compile(
    r"data\s*source|数据源|数据来源|\btools?\b|工具|key\s+(financial\s+)?terms|术语|字段|"
    r"data\s*coverage|数据覆盖|优先级|priority|tier\b|\bapi\b",
    re.IGNORECASE,
)
_WORKFLOW_TITLE = re.compile(r"workflow|工作流|流程|\bprocess\b|步骤|\bsteps?\b", re.IGNORECASE)
_WRITE_TITLE = re.compile(
    r"draft|\bwrite\b|writing|\breport\b|commentary|quality\s*check|deliverable|output|format|"
    r"template|present|assembl|summar|memo|输出|撰写|点评|模板|交付|总结|结论",
    re.IGNORECASE,
)
_STEP_TITLE = re.compile(r"^(step\s*\d+|\d+[.、)]|第[一二三四五六七八九十]+步)", re.IGNORECASE)

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

# —— 能力降级（D8）：纯规则判定，命中 ≥3 次即标注 ——
_FILES_PAT = re.compile(
    r"xlsx|pptx|powerpoint|excel|spreadsheet|docx|openpyxl|python-pptx|read\s+tool|write\s+tool", re.IGNORECASE
)
_FILES_MIN_HITS = 3

CAPABILITY_NOTE = (
    "【本环境能力限制】运行环境无文件读写 / Excel / PPT 生成与审计工具（无 Read/Write/xlsx/pptx 能力）。"
    "本技能中涉及文件产物的部分**只输出内容与结构建议**（用 Markdown 表格承载），"
    "不生成 `.xlsx/.pptx/.docx` 文件，也**不要声称已生成或已保存文件**；"
    "所有数值只能来自下方『可用数据』，不得编造。"
)

LIBRARY_DISCIPLINE = (
    "【本项目输出纪律（优先级高于下方技能正文；两者冲突时以本段为准）】\n"
    "- 只依据下方『可用数据』中的事实作答，**复述已结构化的数据**，绝不编造数字、来源或结论；\n"
    "- 某一类数据没取到：明确写『数据未接入』，不要空白、不要填占位假值、不要用训练知识补；"
    "但若某个维度的多条数据都取不到，导致某节/某表会变成清一色『数据未接入』的占位清单，"
    "直接省略整节/整表，不要为凑结构保留一张全空表格；\n"
    "- **正文里不出现任何数据源/接口名**：不写万得/Wind、同花顺/iFind、Tushare、AkShare 等数据商名字，"
    "也不写接口名与来源编号，**结尾不要加『数据来源于……』这类落款**（来源由界面统一展示；"
    "凭空落款尤其危险——本轮实际用了哪几个源你无法确知，写上就是错的）；\n"
    "- **不要提及提示词内部结构**：不写『技能说明/本技能要求/系统提示』之类的字样，直接把结论写成报告正文；\n"
    "- 评级只给**方向性判断**（如『基本面改善/承压』『估值偏高/偏低』），"
    "**不要输出买入/增持/中性/减持/卖出这类卖方评级标签**；目标价只在与证据强度匹配时给出，"
    "否则写『暂无可靠目标价』；\n"
    "- 盈利预测/估值等数字表只填取到真实数据的单元格，其余标『数据未接入』；\n"
    "- 全文用中文作答；结构化数据用 Markdown 表格承载，数字/日期/百分比用 **加粗**；\n"
    "- 技能正文中若要求读写文件、运行脚本或调用本环境没有的工具，改为直接输出相应内容。"
)


# ——————————————————————————— frontmatter ———————————————————————————


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """解析首部 `---` frontmatter，只取 `name`/`description` 两键（含 `>`/`|` 折叠块与缩进续行）。

    非法/缺键返回 None（调用方记 WARNING 跳过该文件，不抛异常）。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None

    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if key:
            out[key] = " ".join(p.strip() for p in buf if p.strip()).strip()

    for raw in lines[1:end]:
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", raw)
        if m and not raw.startswith((" ", "\t")):
            flush()
            key, rest = m.group(1), m.group(2).strip()
            buf = [] if rest in (">", "|", ">-", "|-", "") else [rest]
        elif key is not None:
            buf.append(raw)
    flush()

    if not out.get("name") or not out.get("description"):
        return None
    return {"name": out["name"], "description": out["description"], "body": "\n".join(lines[end + 1:]).strip()}


# ——————————————————————————— 分段 ———————————————————————————


@dataclass(frozen=True)
class Block:
    """一个标题块（标题行 + 直属内容，不含更深层标题的内容）。"""

    level: int
    title: str
    content: str
    parent: str  # 最近的 level<=2 标题（本身是 level<=2 时为空）

    def render(self) -> str:
        return f"{'#' * self.level} {self.title}\n{self.content}".rstrip()


def split_blocks(body: str) -> list[Block]:
    """按 `^#{1,3} ` 标题切块；**跳过 ``` / ~~~ 代码块内的 `#`**（正文 bash 示例里的注释会误判成标题）。"""
    blocks: list[Block] = []
    level, title, parent = 0, "", ""
    buf: list[str] = []
    in_fence = False

    def close() -> None:
        if level or title or "".join(buf).strip():
            blocks.append(Block(level=level or 1, title=title, content="\n".join(buf).strip(), parent=parent))

    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            buf.append(line)
            continue
        m = None if in_fence else _HEADING.match(line)
        if m and len(m.group(1)) <= 3:
            close()
            level, title = len(m.group(1)), m.group(2).strip()
            parent = "" if level <= 2 else parent
            if level <= 2:
                parent = title
            buf = []
            continue
        buf.append(line)
    close()
    return [b for b in blocks if b.title or b.content]


def route_block(block: Block, *, skill_id: str, skill_name: str) -> str:
    """`"hint"`（→ tool_rag 取数引导）/ `"out"`（→ synthesizer 输出提示词）/ `"drop"`（冗余标题）。"""
    title = block.title.strip().strip("*").strip()
    low = title.lower()
    # 与技能名重复的 H1（部分文件出现两次）：标题丢弃、内容按其父归属（多为空）
    if block.level == 1 and low in {skill_id.lower(), skill_name.lower()} and not block.content.strip():
        return "drop"
    if _WRITE_TITLE.search(title):
        return "out"
    if _DATA_TITLE.search(title):
        return "hint"
    if _STEP_TITLE.match(title) and _WORKFLOW_TITLE.search(block.parent or ""):
        return "hint"  # 写作类步骤已在上面被 _WRITE_TITLE 截走
    if _WORKFLOW_TITLE.search(title):
        return "hint"
    if _DATA_TITLE.search(block.parent or "") and block.level == 3:
        return "hint"
    return "out"


def _join_capped(blocks: list[Block], cap: int) -> str:
    """按块边界拼接并限长（宁可少一段，也不要截在半句/半个表格里）。"""
    out: list[str] = []
    total = 0
    for b in blocks:
        piece = b.render()
        if total + len(piece) > cap:
            out.append("（其余步骤略：如需更细的取数步骤，按上文数据源与字段自行补齐。）")
            break
        out.append(piece)
        total += len(piece) + 2
    return "\n\n".join(out).strip()


# ——————————————————————————— 工具名对照 ———————————————————————————


def _scan_text(body: str) -> str:
    """把 `mcp__<server>__<tool>` 归一成裸工具名后的**扫描副本**（vendored 正文本体不改）。"""
    s = re.sub(r"mcp__(wind|ifind)__([a-z][a-z0-9_]*)", r"\1_\2", body)
    return re.sub(r"mcp__[a-z0-9_-]+__([a-z][a-z0-9_]*)", r"\1", s)


def detect_source_families(body: str) -> tuple[str, ...]:
    """技能正文**打算用哪几个数据源**（≠ 引用了哪些工具名）。

    两者必须分开：`tool_mapping` 只翻译真实存在的小写工具名，而这里判断的是「这份 playbook 走哪家源」——
    实测 63 篇里只有 1 篇点名 `wind_*`、6 篇点名 `ifind_*`，但绝大多数的 Data Sources 写着
    「Tier 0 — 万得 Wind / Tier 1 — 同花顺 iFind」，只是用散文和环境变量名描述。
    families 判断错的后果是实打实的：`ifind_list_apis/get_api_info/query` **不在** `META_TOOL_NAMES` 里
    （只有 Wind 三件套在），不靠 `tool_families` 恒保留，iFind 这一路就压根进不了本轮工具面。
    """
    out: list[str] = []
    if re.search(r"万得|\bwind\b", body, re.IGNORECASE):
        out.append("wind")
    if re.search(r"同花顺|\bifind\b|\bifind\b", body, re.IGNORECASE):
        out.append("ifind")
    if re.search(r"akshare|财经新闻|china-news", body, re.IGNORECASE):
        out.append("free")
    return tuple(out)


def extract_tool_names(body: str) -> tuple[list[str], list[str], list[str]]:
    """返回 (wind 具名, ifind 具名, 免费源同名) 三组，去重且保持出现顺序。"""
    scan = _scan_text(body)
    wind: list[str] = []
    ifind: list[str] = []
    free: list[str] = []
    # **区分大小写**：工具名在正文里一律小写，而 `WIND_API_KEY`/`IFIND_AUTH_TOKEN` 这类环境变量一律大写
    # ——大小写不敏感会把配置项当接口翻出去（实测翻出过 `wind_query(api_name="api_key")` 这种不存在的接口）。
    # 结尾是 `_` 的是正文里的通配写法（`wind_get_fund_*`），也不是真名。
    for name in re.findall(r"\b((?:wind|ifind)_[a-z][a-z0-9_]*)\b", scan):
        if name in _META_TOOLS or name in _NOT_TOOLS or name.endswith("_"):
            continue
        bucket = wind if name.startswith("wind_") else ifind
        if name not in bucket:
            bucket.append(name)
    for name in re.findall(r"\b([a-z][a-z0-9_]{2,})\b", scan):
        if name in FREE_SOURCE_TOOLS and name not in free:
            free.append(name)
    return wind, ifind, free


def build_tool_mapping(wind: list[str], ifind: list[str], free: list[str]) -> list[tuple[str, str]]:
    """逐条「旧名 → 本环境写法」；给前端详情页与提示词共用。"""
    rows: list[tuple[str, str]] = []
    for name in wind:
        api = name[len("wind_"):]
        rows.append((name, f'wind_query(api_name="{api}", params={{…}})'))
    for name in ifind:
        rows.append((name, 'ifind_query(query="……自然语言问题……")'))
    for name in free:
        eq = OFFICIAL_EQUIVALENT.get(name)
        rows.append((name, f"同名直接调用；Tushare 官方等价：{eq}" if eq else "同名直接调用"))
    return rows


def render_tool_mapping(rows: list[tuple[str, str]], *, wind: list[str], ifind: list[str], free: list[str]) -> str:
    if not rows:
        return ""
    lines = [
        (
            "【工具名对照】下方技能正文里的工具名沿用 claude-for 插件环境的旧名，"
            "本环境（MCP 网关）按右侧写法调用："
        ),
    ]
    lines += [f"- `{old}` → {new}" for old, new in rows]
    if wind:
        lines.append(
            "Wind 只暴露 3 个元工具：先 `wind_list_apis` 浏览、`wind_get_api_info` 确认参数，再 "
            "`wind_query` 取数；**`api_name` 一律以 `wind_list_apis` 的实时返回为准**（别照抄旧名）。"
            "Wind 参数是自然语言 + Wind 后缀代码（如 `600519.SH`）。"
        )
    if ifind:
        lines.append(
            "同花顺 iFind 同样只有 3 个元工具，且 `ifind_query` **只吃一个自然语言 `query` 串**"
            "（如 `\"科大讯飞2025年三季度的ROE\"`），没有 `ts_code`/`start_date` 这类结构化字段；"
            "接口清单以 `ifind_list_apis` 为准。"
        )
    if free:
        lines.append(
            "免费源（AkShare / 财经新闻）工具同名可直接调用，但**股票代码是 6 位裸代码**（如 `600519`，不带 `.SH`）；"
            "能用 Tushare 官方接口拿到的优先走官方源，官方报无权限再退免费源。"
        )
    return "\n".join(lines)


# ——————————————————————————— 装配 ———————————————————————————


@dataclass(frozen=True)
class LoadedSkill:
    """一条从 `SKILL.md` 装配出来的技能（`skills.py` 据此构造 `Skill`；web API 直接读它做详情页）。"""

    id: str
    name: str                     # 中文短名（catalog 有则用它，否则用 frontmatter name）
    raw_name: str                 # upstream frontmatter name（= 目录名）
    domain: str
    domain_label: str
    description: str              # upstream 原始 description（详情页展示）
    catalog_line: str             # router 清单用的中文短描述
    body: str                     # SKILL.md 正文原文（详情页渲染）
    hint_text: str                # 取数侧小节（已限长）
    output_text: str              # 输出侧小节
    tool_mapping: tuple[tuple[str, str], ...]
    mapping_note: str
    files_limited: bool
    source_families: tuple[str, ...]   # ("wind","ifind","free") 子集，前端标 Badge
    tool_families: tuple[str, ...]
    should_rag: bool
    strategy: str
    report_type: str

    def system_prompt(self, base: str) -> str:
        """synthesizer 系统提示词：base → 纪律段 → 工具名对照 → 能力限制 → 技能输出侧正文。"""
        parts = [base, LIBRARY_DISCIPLINE]
        if self.mapping_note:
            parts.append(self.mapping_note)
        if self.files_limited:
            parts.append(CAPABILITY_NOTE)
        parts.append(
            f"请按下面这份技能说明（{self.name}）的口径与结构输出报告；"
            "说明中要求的数据若不在『可用数据』里，按上面的纪律标注『数据未接入』。\n\n"
            f"{self.output_text or self.body}"
        )
        return "\n\n".join(p for p in parts if p)

    def tool_hint(self) -> str:
        """tool_rag 取数引导：技能的数据源/术语/取数步骤 + 工具名对照。"""
        parts = [f"本轮命中报告技能【{self.name}】，其取数与口径要求如下（工具以本轮实际可用清单为准）："]
        if self.hint_text:
            parts.append(self.hint_text)
        if self.mapping_note:
            parts.append(self.mapping_note)
        parts.append(
            "拿不准接口与参数时先用 `list_apis` / `get_api_info`（Tushare）或 `wind_list_apis` / "
            "`ifind_list_apis` 确认，再取数；某接口报无权限/空数据就换等价接口，不要反复重试同一个。"
        )
        return "\n\n".join(parts)


def _first_sentence(text: str, limit: int = 160) -> str:
    head = re.split(r"(?<=[.。！!？?])\s", text.strip(), maxsplit=1)[0]
    return head if len(head) <= limit else head[: limit - 1] + "…"


def load_library(library_dir: str | Path | None = None) -> list[LoadedSkill]:
    """扫描 vendored 语料目录，装配全部技能条目（按 域 → 技能名 排序，稳定可测）。"""
    root = Path(library_dir) if library_dir else DEFAULT_LIBRARY_DIR
    paths = sorted(root.glob("vertical-plugins/*/skills/*/SKILL.md"))
    if not paths:
        _log.warning("skill_library 为空或路径不存在：%s", root)
        return []

    # 先统计「同一目录名出现在几个域」——撞名的才加后缀，且与文件遍历顺序无关（稳定 id）
    by_name: dict[str, set[str]] = {}
    for path in paths:
        by_name.setdefault(path.parts[-2], set()).add(path.parts[-4])
    dup = {name for name, doms in by_name.items() if len(doms) > 1}

    loaded: list[LoadedSkill] = []
    seen: set[str] = set()
    for path in paths:
        domain = path.parts[-4]
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            _log.warning("skill_library 读取失败，跳过 %s：%s", path, exc)
            continue
        fm = parse_frontmatter(text)
        if not fm:
            _log.warning("skill_library frontmatter 无法解析，跳过 %s", path)
            continue

        raw_name = fm["name"]
        suffix = ID_SUFFIX_BY_DOMAIN.get(domain, "") if raw_name in dup else ""
        skill_id = f"{raw_name}{suffix}"
        if skill_id in seen:
            _log.warning("skill_library id 冲突，跳过 %s（%s）", skill_id, path)
            continue
        seen.add(skill_id)

        body = fm["body"]
        routed = [(b, route_block(b, skill_id=skill_id, skill_name=raw_name)) for b in split_blocks(body)]
        hints = [b for b, r in routed if r == "hint"]
        outs = [b for b, r in routed if r == "out"]

        wind, ifind, free = extract_tool_names(body)
        rows = build_tool_mapping(wind, ifind, free)
        # families 用「正文走哪家源」判断（散文也算），mapping 只翻译真实工具名——见 detect_source_families
        sources = detect_source_families(body)
        families: list[str] = []
        if "wind" in sources:
            families.append("wind_")
        if "ifind" in sources:
            families.append("ifind_")
        families.extend(free)  # 免费源用**精确名**恒保留（子串会误伤一片同前缀接口）

        zh = CATALOG.get(skill_id)
        if zh is None:
            _log.warning("skill_catalog 缺少 %s 的中文短描述，回落 description 首句", skill_id)
        name_zh, line = zh if zh else (raw_name, _first_sentence(fm["description"]))

        loaded.append(
            LoadedSkill(
                id=skill_id,
                name=name_zh,
                raw_name=raw_name,
                domain=domain,
                domain_label=DOMAIN_LABELS.get(domain, domain),
                description=fm["description"],
                catalog_line=line,
                body=body,
                hint_text=_join_capped(hints, _MAX_SKILL_HINT_CHARS),
                output_text=_join_capped(outs, 12000),
                tool_mapping=tuple(rows),
                mapping_note=render_tool_mapping(rows, wind=wind, ifind=ifind, free=free),
                files_limited=len(_FILES_PAT.findall(body)) >= _FILES_MIN_HITS,
                source_families=sources,
                tool_families=tuple(families),
                should_rag=skill_id in RAG_SKILL_IDS,
                strategy="factual" if skill_id in RAG_SKILL_IDS else "auto",
                report_type=f"library:{domain}",
            )
        )
    return loaded
