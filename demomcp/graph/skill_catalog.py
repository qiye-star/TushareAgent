"""导入技能的中文短名 + 短描述表（router 清单用）。

为什么需要这张表（决策 D6）：claude-for 的 63 条 `description:` 合计 20.7KB（均 328 字符，含大段
中英文触发词列表），原样拼进 router 系统提示词会让**每一次提问**（包括「查下比亚迪股价」）都多带
8–10k tokens。这里把每条压成「中文短名 + ≤40 字短描述」，整张 router 清单落到 4KB 量级。

维护约定：
- key 是 loader 组装后的 skill id（含 `-fund` 后缀的去重 id，见 skill_loader.ID_SUFFIX_BY_DOMAIN）；
- 缺项不会崩：`skill_loader` 回落「description 首句截断」并打 WARNING（`tests/test_skill_loader.py` 锁定表的完整性）；
- 短描述面向 **router 匹配**，所以要写用户会怎么说这件事（口径/产物），不要写实现细节。
"""

from __future__ import annotations

# id -> (中文短名, 短描述)
CATALOG: dict[str, tuple[str, str]] = {
    # —— china-finance（31）——
    "china-3-statement-model": ("三表联动财务模型", "按 CAS 口径搭建利润表/资产负债表/现金流量表联动的预测模型"),
    "china-accrual-schedule": ("应计科目时间表", "梳理收入确认、费用应计与营运资金的期间归属表"),
    "china-audit-xls": ("财务模型审核", "按 A 股口径检查财务模型的公式、口径与常见错误"),
    "china-break-trace": ("财报异常核查", "排查财报勾稽不符、盈余质量与潜在财务异常信号"),
    "china-catalyst-calendar": ("催化事件日历", "汇总覆盖股池的财报日、政策、行业会议等催化时点"),
    "china-clean-data-xls": ("财务数据清洗", "把原始财报数据归一成可建模的规整表"),
    "china-comps-analysis": ("可比公司估值分析", "选同业可比公司，对比 PE/PB/PS 等倍数做相对估值（详版）"),
    "china-comps": ("A股可比公司速览", "快速搭同业组、拉财务、算 PE/PB/PS 并给相对价值判断"),
    "china-dcf-model": ("DCF估值速查", "DCF 核心参数与流程速查（完整版见「A股DCF估值」）"),
    "china-dcf": ("A股DCF估值", "用中国参数（10 年国债/ERP 6-8%/永续 3-4%/税率 25%）做现金流折现"),
    "china-deal-screening": ("A股标的筛选", "按财务与估值条件系统筛选 A 股投资标的"),
    "china-deck-refresh": ("投资PPT数据刷新", "用最新财务与行情数据更新既有路演/投资演示材料"),
    "china-earnings-analysis": ("业绩点评报告", "对季报/年报做实际 vs 一致预期差异表、驱动归因与预测调整"),
    "china-earnings-preview": ("财报前瞻", "财报前搭建超预期/不及预期情景与关键观察指标"),
    "china-gl-recon": ("总账核对（上市公司）", "上市公司总账与资产负债/应收/应付/存货科目的核对"),
    "china-ib-check-deck": ("投行材料质检", "对并购/融资 pitch 材料做数据与合规质量检查"),
    "china-idea-generation": ("投资机会挖掘", "用量化筛选与主题研究系统性挖掘多空投资想法"),
    "china-initiating-coverage": ("首次覆盖深度报告", "公司分析 + 财务模型 + 估值 + 图表的完整首覆研究报告"),
    "china-lbo-model": ("杠杆收购模型", "按中国债务市场与 CAS 口径搭建 LBO 模型与回报测算"),
    "china-market-data": ("A股数据查询", "多源查 A 股行情/财务/宏观/行业/指数/新闻的通用取数"),
    "china-model-update": ("财务模型更新", "用新季度实际值与管理层指引滚动更新模型并标注变化"),
    "china-morning-note": ("A股晨会纪要", "汇总隔夜变化、盘前情绪、交易想法与当日关键事件"),
    "china-ppt-template-creator": ("投资PPT模板制作", "制作标准化的 A 股投研/客户演示 PPT 模板"),
    "china-pptx-author": ("投资分析PPT撰写", "为任一 A 股公司生成路演/投资分析演示的内容与页面结构"),
    "china-roll-forward": ("模型期间滚动", "把财务模型按 A 股披露日历滚动到新报告期"),
    "china-sector-overview": ("行业深度综述", "市场规模、竞争格局、政策环境、龙头与估值的行业全景"),
    "china-skill-creator": ("技能脚手架", "按既有规范新建 china-* 技能的元技能"),
    "china-tax-loss-harvesting": ("税务亏损收割", "按中国税制为组合做亏损收割与替代持仓建议"),
    "china-thesis-tracker": ("投资逻辑跟踪", "跟踪持仓/自选股的投资逻辑、关键数据点与催化验证"),
    "china-variance-commentary": ("业绩差异点评（上市公司）", "对上市公司财报的同比/环比/预期差异写结构化点评"),
    "china-xlsx-author": ("财务分析Excel编制", "编制 A 股财务分析工作簿的表结构与内容"),
    # —— fund-admin（6，与 china-finance 撞名的 5 条带 -fund 后缀）——
    "china-accrual-schedule-fund": ("基金应计科目表", "基金会计口径的应计费用与收入确认时间表"),
    "china-break-trace-fund": ("基金持仓核查", "对基金持仓与被投公司财务做取证式异常排查"),
    "china-gl-recon-fund": ("总账核对（基金）", "基金会计的证券/现金/收入/费用应计与净值核对"),
    "china-nav-tieout": ("基金净值核对", "A 股组合的 NAV 重算、估值核对与差异追踪"),
    "china-roll-forward-fund": ("基金账簿滚动", "基金会计记录按新报告期滚动结转"),
    "china-variance-commentary-fund": ("基金业绩归因点评", "对基金业绩与组合变化写归因与差异点评"),
    # —— investment-banking（10）——
    "china-buyer-list": ("潜在买方名单", "为卖方并购梳理战略买家与财务买家名单"),
    "china-cim-builder": ("并购CIM编制", "编制卖方并购的保密信息备忘录（CIM）结构与内容"),
    "china-competitive-analysis": ("竞争格局分析", "梳理竞争对手、市场地位与相对优劣势"),
    "china-datapack-builder": ("交易数据包", "为尽调/投决/公司概览编制结构化数据包"),
    "china-deal-tracker": ("交易进度跟踪", "跟踪并购交易的里程碑、截止日与待办事项"),
    "china-merger-model": ("并购增厚摊薄模型", "测算并购的每股收益增厚/摊薄、协同与购买价分摊"),
    "china-pitch-deck": ("Pitch Deck 填充", "用财务与行情数据填充投行路演材料模板"),
    "china-process-letter": ("并购流程函", "起草卖方并购的流程函、意向书与投标指引"),
    "china-strip-profile": ("一页公司概览", "制作 pitch book 里的一页式标的公司概览"),
    "china-teaser": ("匿名 Teaser", "起草不披露公司身份的一页式盲档"),
    # —— operations（2）——
    "china-kyc-doc-parse": ("KYC证件解析", "解析身份证/营业执照等中国 KYC 文件并校验字段"),
    "china-kyc-rules": ("KYC与反洗钱规则", "中国 KYC/AML 合规要求与尽调等级规则"),
    # —— private-equity（9）——
    "china-ai-readiness": ("AI就绪度评估", "评估被投企业的数据基建、技术栈、人才与 AI 落地机会"),
    "china-dd-checklist": ("尽职调查清单", "商业/财务/法务/税务等分类尽职调查清单"),
    "china-dd-meeting-prep": ("管理层访谈准备", "准备尽调管理层会议的问题清单与材料"),
    "china-deal-sourcing": ("投资机会寻源", "挖掘并初筛 A 股及 Pre-IPO 投资机会"),
    "china-ic-memo": ("投委会备忘录", "起草投资委员会决策备忘录（IC Memo）"),
    "china-portfolio-monitoring": ("投后组合监控", "跟踪被投企业的财务、KPI、里程碑与风险指标"),
    "china-returns-analysis": ("基金回报分析", "用 IRR/MOIC 等指标做私募基金回报测算与归因"),
    "china-unit-economics": ("单位经济模型", "测算单位/客户经济性与分群留存模型"),
    "china-value-creation-plan": ("投后价值创造计划", "制定被投企业的价值创造路线图与抓手"),
    # —— wealth-management（5）——
    "china-client-report": ("客户持仓报告", "生成财富管理客户的组合业绩与持仓报告"),
    "china-client-review": ("客户会议准备", "准备客户回顾会的业绩摘要、沟通要点与关系背景"),
    "china-financial-plan": ("家庭财务规划", "覆盖养老/教育/传承/现金流的个人与家庭财务规划"),
    "china-investment-proposal": ("投资建议书", "起草含投资逻辑、产品建议与风险揭示的投资建议书"),
    "china-portfolio-rebalance": ("组合再平衡", "分析配置漂移并给出再平衡交易与税务优化建议"),
}

# 域 -> 中文标签（前端技能页分组用）
DOMAIN_LABELS: dict[str, str] = {
    "china-finance": "股票研究",
    "investment-banking": "投资银行",
    "private-equity": "私募股权",
    "wealth-management": "财富管理",
    "fund-admin": "基金运营",
    "operations": "合规运营",
}
