# AI算力产业链高频跟踪快报 — 模板设计

> 定位：日度/周度自动化产出的短篇跟踪快报，非深度报告。目标是让分析师/研究员每天开盘前5分钟内看完标的池的关键变化。全文数据驱动，叙述性文字占比控制在20%以内，避免"言之无物"的套话。

---

## 模板配置（YAML，供Agent渲染引擎读取）

```yaml
template_id: ai_supply_chain_daily_tracker
template_name: AI算力产业链高频跟踪快报
frequency: daily  # daily / weekly
sections:
  - section_id: market_snapshot
    title: 板块概览
    type: data_table
    data_source: sector_price_flow
    fields: [概念板块涨跌幅, 主力资金净流入TOP5, 成交额变化]

  - section_id: watchlist_price_action
    title: 标的池行情速览
    type: data_table
    data_source: stock_price_daily
    fields: [涨跌幅, 收盘价, 主力资金净流入, 换手率]

  - section_id: key_announcements
    title: 关键公告
    type: event_list
    data_source: announcement_events
    filter: {event_type: [业绩预告, 股东增减持, 重大合同], time_range: 1d}

  - section_id: earnings_forecast_alert
    title: 业绩预告异动提示
    type: event_list_with_threshold
    data_source: announcement_events
    filter: {event_type: 业绩预告}
    threshold: {net_profit_yoy_change_pct: ">50 or <-20"}

  - section_id: news_catalyst
    title: 产业链催化事件
    type: event_list
    data_source: industry_news_events
    filter: {time_range: 1d, relevance: watchlist}

  - section_id: narrative_brief
    title: 一句话研判
    type: narrative_generated
    data_source: [market_snapshot, key_announcements, news_catalyst]
    narrative_prompt: daily_brief_narrative
    max_length: 150
```

---

## 报告呈现示例（以2026年8月14日CPO板块为场景填充，数据来自当日市场公开报道，仅作模板演示）

---

### 【AI算力产业链跟踪快报】2026年8月14日

**免责声明**：本快报由Agent根据结构化数据自动生成，仅供内部研究参考，不构成任何投资建议，所有数据以官方披露为准。

---

#### 一、板块概览

| 概念板块 | 当日涨跌幅 | 主力资金净流入(亿元) | 备注 |
|---|---|---|---|
| 光模块(CPO) | +2.78% | 数据待接入 | 英伟达Spectrum-X Ethernet Photonics进入全面量产阶段 |

**本周资金净流入TOP5**（8月10日-14日）：

| 排名 | 标的 | 净流入金额(亿元) |
|---|---|---|
| 1 | 新易盛 | 34.92 |
| 2 | C超纯 | 24.26 |
| 3 | 天孚通信 | 22.80 |
| 4 | 网宿科技 | 15.73 |
| 5 | 莲花控股 | 10.46 |

---

#### 二、标的池行情速览

| 标的 | 当日涨跌 | 最新价 | 本周涨幅 | 备注 |
|---|---|---|---|---|
| 亨通光电 | 涨停 | — | — | CPO催化 |
| 剑桥科技 | 涨停 | — | — | CPO催化 |
| 旭光电子 | 涨停 | — | — | CPO催化 |
| 新易盛 | +4%以上 | 448.08元 | +6.44% | 800G主力出货，1.6T放量 |
| 天孚通信 | +4%以上 | — | — | 资金净流入居前 |

*（实际生产环境中此表由`stock_price_daily`数据源实时查询填充，此处为示例静态值）*

---

#### 三、关键公告

| 标的 | 公告类型 | 核心内容 | 发布日期 |
|---|---|---|---|
| 新易盛 | 业绩预告 | 预计2026年上半年实现归母净利润70亿元至80亿元，同比增长77.56%至102.93% | — |

---

#### 四、业绩预告异动提示（同比增速>50%或<-20%自动触发）

⚠️ **新易盛**：预告净利润同比增速上限达102.93%，触发正向异动阈值，归因为AI相关算力投资持续增长、产品结构优化。

---

#### 五、产业链催化事件

- 英伟达宣布Spectrum-X Ethernet Photonics进入全面量产阶段，为AI工厂下一代大规模扩展网络基础设施提供支持
- 800G光模块为新易盛主力出货产品，1.6T光模块二季度放量加速，供应链紧张逐步缓解

---

#### 六、一句话研判（LLM生成，限定输入数据源，150字以内）

> CPO板块受英伟达量产落地消息催化单日上涨2.78%，新易盛、天孚通信等龙头资金净流入居前；新易盛业绩预告显示净利润同比增速上限超100%，验证光模块环节在AI算力产业链中"订单充足、业绩率先兑现"的确定性最强的传导节奏。

---

## 模板设计说明

### 为什么这样设计"高频"

| 设计取舍 | 理由 |
|---|---|
| 每个section数据量小、结构固定 | 高频报告的核心矛盾是"频率高"与"内容深度"不可兼得，选择牺牲深度换取频率和自动化程度 |
| narrative部分严格限定为150字以内 | 高频场景不需要长篇分析，只需要"发生了什么+要不要关注"，过长的生成式文本反而增加阅读负担和出错概率 |
| 业绩预告异动提示用阈值触发而非全量罗列 | 高频报告最怕"信息过载"，只推送超过阈值的异常值，正常范围内的数据不单独强调 |
| narrative_prompt的输入严格限定为上面几个data_table/event_list的结果 | 复用之前聊报告生成时的核心约束——LLM只能"复述已结构化的数据"，不能自由检索或引入数据外信息，这对每日自动化产出尤其重要，因为没有人工审核的时间窗口 |

### 生产化时需要补的机制

1. **阈值配置化**：业绩预告异动阈值（>50%或<-20%）、资金流入排名门槛这些数字应该做成可调参数，而不是写死，方便根据实际使用反馈调整敏感度
2. **静默失败告警**：如果某天`announcement_events`或`sector_price_flow`数据源没有更新（比如非交易日、数据商接口故障），报告要能明确显示"数据未更新"而不是空白或者报错
3. **推送机制**：日度快报适合走消息队列+推送（微信/邮件），这个和之前聊过的"事件驱动+MQ通知"是同一套基础设施，不需要额外新建
4. **溯源标注**：narrative部分提到的所有数值（"2.78%""102.93%"）理论上都应该能点击追溯回对应的原始data_table/event_list条目，MVP阶段可以先跳过，验证通过后再加

### 与之前"个股深度分析报告"模板的关系

这份高频快报和之前设计的深度分析报告模板是**同一套引擎、不同粒度的两个模板**：
- 深度报告：按需生成（用户主动请求某个标的），信息密度高，包含财务趋势、研报共识、政策影响链等完整维度
- 高频快报：定时批量生成（覆盖整个标的池），信息密度低，聚焦"今天变了什么"

两者共享同一个`ReportDataAggregator`数据聚合层，区别仅在于模板配置文件不同，这也是之前强调"模板配置化而非硬编码"的价值所在——加一种新的报告节奏，不需要重新开发底层数据管道。
