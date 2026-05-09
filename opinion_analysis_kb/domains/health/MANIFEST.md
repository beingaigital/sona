# 健康领域包 · 材料清单

## 〇、验收口径（老师已确认）

**「领域文档」含 `registry.yaml` 中登记的、属健康包范围的文件路径**；其中 **Wiki / 智库对齐的 15 条（第四节表 #1～#15）按老师最新口径「不算篇数」**：仍保留在 `registry.yaml` 中供 **健康域路由与 Wiki 召回**，但**不计入「至少 30 篇 / 领域文档篇数」**。  
**计篇时**以 **第四节 #16～#30（你们 zip 归入 `materials/` 的材料，含 3 条拆章单独登记）** + **第五节 `PH_SUPP_01`～`15`（自选公共卫生事件舆情材料：`domains/health/materials/公卫舆情报告补充/`，版式同 `10_` 类智库稿，`## 内容` 为正文全文）** 为主；`materials/` 内 **物理 `.md` 文件**（含上半年报告拆章）见第二节与 `materials/` 目录实列。  
本目录 **逐条登记见下文「四、registry 验收全表」**；篇数汇总见该节末尾。

**事件分析时的 Wiki 召回**：仓库根目录 `workflow/domain_routing.json` 已配置 **「健康」** 域（查询命中关键词时优先注入 `playbook_health.md`、`opinion_analysis_kb/references/wiki` 下概念/实体页及若干 `cases/` 示例），与 **「控烟」「大熊猫」** 并列；验收「报告能否召回领域知识」可结合任务目录下的 `wiki_qa_snapshot.json`、`_wiki_meta.domain` 是否为 `健康` 进行核对。

---

## 一、Wiki / 智库侧（与任务「拆分概念/实体」对齐）

详细路径与 `id` 见 **第四节全表**（`H_SRC_00_RAW` 至 `H_ENTITY_FOOD_SAFETY`，共 15 条）。**说明：本节 15 条不计「领域文档篇数」**（见〇节），仅作智库/Wiki 链路对齐与召回用。

## 二、你方 zip：控烟专题 Markdown

**2024 年上半年控烟舆情报告（1110）**：已去重并仅保留**拆章版**（便于检索与编译），与同路径下 raw 一致：

- `…/控烟相关舆情专题/2024年上半年控烟舆情报告1110_目录.md`
- `…/2024年上半年控烟舆情报告1110_01_…`～`_06_…`（共 6 章）

**其余单文件报告（已去掉重复的 `(1)` 副本）**：

| 文件 |
|------|
| `…/控烟相关舆情专题/2024年第四季度控烟舆情报告.md` |
| `…/控烟相关舆情专题/2025年控烟舆情分析年度报告0318.md` |
| `…/控烟相关舆情专题/2024年“烟卡”舆情专题分析报告.md` |
| `…/控烟相关舆情专题/2024年控烟舆情监测年度报告0320.md` |
| `…/控烟相关舆情专题/2024第三季度控烟舆情监测报告（20250102）.md` |

## 三、你方 zip：公卫参考文献

**原始文件（归档）**

| 文件 |
|------|
| `.../公卫参考文献/突发公共卫生事件的社会传播和应急管理0724.doc` |
| `.../公卫参考文献/突发公共卫生事件的微博主题演化模_省略_er和Weibo的埃博拉微博为例_安璐.pdf` |
| `.../公卫参考文献/突发公共卫生事件在不同时期报道的比较研究——以《人民日报》2003年非典报道与2020年新冠肺炎报道为例.pdf` |

**已生成的 Markdown（与 `references/raw/健康舆情/公卫参考文献/` 同步，供 `compile_kb_from_raw` 扫描）**

| 文件 | 说明 |
|------|------|
| `.../公卫参考文献/突发公共卫生事件的社会传播和应急管理0724.md` | `.doc` 经 macOS `textutil` 转写 |
| `.../公卫参考文献/突发公共卫生事件的微博主题演化模_省略_er和Weibo的埃博拉微博为例_安璐.md` | `scripts/pdf_to_markdown.py`（PyMuPDF 按页抽取） |
| `.../公卫参考文献/突发公共卫生事件在不同时期报道的比较研究——以《人民日报》2003年非典报道与2020年新冠肺炎报道为例.md` | 同上 |

---

## 四、registry 验收全表（路径 + 篇数）

以下与 `registry.yaml` 的 `items` **一一对应**，路径均相对于**仓库根目录**。  
（Playbook、案例不在 `items` 中，不计入下表「登记条数」。）

**篇数口径**：表 **#1～#15** 为 Wiki/智库对齐，**不计入**领域文档篇数；**#16～#30** 为 zip 归入 `materials/`（及同源路径，其中 **#17～#19** 为《2024 年上半年控烟舆情报告》拆章 **3 条**，与 **#16 目录**同属一份报告）；**第五节 `PH_SUPP_*`** 为自选补充材料（公共卫生事件相关舆情稿，路径 **`opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/`**，结构与 **`10_爱上《浙江宣传》` 等编号 raw** 一致：`#` 标题 + 作者/时间/平台/来源/**链接** + `## 内容`）。按「**仅计 #16～#30 + PH_SUPP 15**」→ **15 + 15 = 30 条**，满足「至少 30 篇领域文档（不计 Wiki）」的 **registry 登记口径**。

| # | id | role | 路径 |
|---|-----|------|------|
| 1 | H_SRC_00_RAW | corpus | `舆情深度分析/references/raw/00_中国舆情大事件记录（持续更新）.md` |
| 2 | H_SRC_00_WIKI | corpus | `舆情深度分析/references/wiki/sources/00_中国舆情大事件记录_持续更新.md` |
| 3 | H_CONCEPT_MED_DISPUTE | concept | `舆情深度分析/references/wiki/concepts/医疗纠纷.md` |
| 4 | H_ENTITY_MED_DISPUTE | entity | `舆情深度分析/references/wiki/entities/医疗纠纷.md` |
| 5 | H_CONCEPT_NB_HOSP | concept | `舆情深度分析/references/wiki/concepts/宁波妇儿医院.md` |
| 6 | H_ENTITY_NB_HOSP | entity | `舆情深度分析/references/wiki/entities/宁波妇儿医院.md` |
| 7 | H_CONCEPT_NB_UNI_WCH | concept | `舆情深度分析/references/wiki/concepts/宁波大学附属妇女儿童医院.md` |
| 8 | H_ENTITY_NB_UNI_WCH | entity | `舆情深度分析/references/wiki/entities/宁波大学附属妇女儿童医院.md` |
| 9 | H_CONCEPT_PUMC | concept | `舆情深度分析/references/wiki/concepts/北京协和医学院.md` |
| 10 | H_ENTITY_PUMC | entity | `舆情深度分析/references/wiki/entities/北京协和医学院.md` |
| 11 | H_ENTITY_XIEHE | entity | `舆情深度分析/references/wiki/entities/协和医学院.md` |
| 12 | H_SRC_HEALTH_AESTHETICS | source | `舆情深度分析/references/wiki/sources/49_逐玉_粉底液将军_被官方点名_电视剧健康审美座谈会_到底在批评谁.md` |
| 13 | H_ENTITY_TEA_BRAND | entity | `舆情深度分析/references/wiki/entities/霸王茶姬.md` |
| 14 | H_CONCEPT_FOOD_SAFETY | concept | `舆情深度分析/references/wiki/concepts/食品安全.md` |
| 15 | H_ENTITY_FOOD_SAFETY | entity | `舆情深度分析/references/wiki/entities/食品安全.md` |
| 16 | H_MAT_TOBACCO_2024_H1 | domain_report | `opinion_analysis_kb/domains/health/materials/控烟相关舆情专题/2024年上半年控烟舆情报告1110_目录.md` |
| 17 | H_MAT_TOBACCO_2024_H1_CH01 | domain_report | `opinion_analysis_kb/domains/health/materials/控烟相关舆情专题/2024年上半年控烟舆情报告1110_01_一、主要发现与结论.md` |
| 18 | H_MAT_TOBACCO_2024_H1_CH02 | domain_report | `opinion_analysis_kb/domains/health/materials/控烟相关舆情专题/2024年上半年控烟舆情报告1110_02_二、2024年上半年控烟与烟草相关舆情监测概述.md` |
| 19 | H_MAT_TOBACCO_2024_H1_CH03 | domain_report | `opinion_analysis_kb/domains/health/materials/控烟相关舆情专题/2024年上半年控烟舆情报告1110_03_三、各媒体平台控烟宣传效果分析.md` |
| 20 | H_MAT_TOBACCO_2024_Q4 | domain_report | `opinion_analysis_kb/domains/health/materials/控烟相关舆情专题/2024年第四季度控烟舆情报告.md` |
| 21 | H_MAT_TOBACCO_2025_ANNUAL | domain_report | `opinion_analysis_kb/domains/health/materials/控烟相关舆情专题/2025年控烟舆情分析年度报告0318.md` |
| 22 | H_MAT_TOBACCO_YANKA | domain_report | `opinion_analysis_kb/domains/health/materials/控烟相关舆情专题/2024年“烟卡”舆情专题分析报告.md` |
| 23 | H_MAT_TOBACCO_2024_ANNUAL | domain_report | `opinion_analysis_kb/domains/health/materials/控烟相关舆情专题/2024年控烟舆情监测年度报告0320.md` |
| 24 | H_MAT_TOBACCO_2024_Q3 | domain_report | `opinion_analysis_kb/domains/health/materials/控烟相关舆情专题/2024第三季度控烟舆情监测报告（20250102）.md` |
| 25 | H_MAT_PH_DOC | reference | `opinion_analysis_kb/domains/health/materials/公卫参考文献/突发公共卫生事件的社会传播和应急管理0724.doc` |
| 26 | H_MAT_PH_PDF_WEIBO | reference | `opinion_analysis_kb/domains/health/materials/公卫参考文献/突发公共卫生事件的微博主题演化模_省略_er和Weibo的埃博拉微博为例_安璐.pdf` |
| 27 | H_MAT_PH_PDF_RMRB | reference | `opinion_analysis_kb/domains/health/materials/公卫参考文献/突发公共卫生事件在不同时期报道的比较研究——以《人民日报》2003年非典报道与2020年新冠肺炎报道为例.pdf` |
| 28 | H_MAT_PH_MD_H1N1 | reference | `opinion_analysis_kb/domains/health/materials/公卫参考文献/突发公共卫生事件的社会传播和应急管理0724.md` |
| 29 | H_MAT_PH_MD_WEIBO | reference | `opinion_analysis_kb/domains/health/materials/公卫参考文献/突发公共卫生事件的微博主题演化模_省略_er和Weibo的埃博拉微博为例_安璐.md` |
| 30 | H_MAT_PH_MD_RMRB | reference | `opinion_analysis_kb/domains/health/materials/公卫参考文献/突发公共卫生事件在不同时期报道的比较研究——以《人民日报》2003年非典报道与2020年新冠肺炎报道为例.md` |

**登记条数（上表行数）：30**（其中 **#1～#15 不计篇**，见表首说明。）

**说明（与「至少 30 篇」对齐，已排除 Wiki 15 条）：**

- **计篇主路径**：**#16～#30**（15 条登记）对应 `materials/` 控烟专题（含目录 + 上半年报告拆章 3 条 + 其余单文件）、公卫参考文献（含 pdf/doc/md）；**另加** 第五节 **`PH_SUPP_01`～`15`**（15 条补充材料）。合计登记 **15 + 15 = 30**。  
- **历史写法保留**：若仍把 Wiki #1～#15 算进「领域文档篇数」，会与老师「Wiki 不算」冲突；**以〇节与表首口径为准**。  
- 领域 **Playbook**：`playbook_health.md`（1 份，单列，不计入篇数表）。  
- **案例**：`cases/H01–H10.md`（10 个，单列，不计入篇数表）。

---

## 五、自选补充：公共卫生事件舆情稿 15 篇（`公卫舆情报告补充/`，版式同 `10_` 智库稿）

与老师「**文件夹里只保留 15 篇（智库/Wiki 对齐条）** + **自己再找 15 篇**」的口述对齐：下列 **15 个 Markdown** 均在 **`opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/`**，正文结构对齐 **`10_爱上《浙江宣传》的几个理由.md`**：`#` 标题、**作者 / 发布时间 / 平台 / 来源 / 链接**、`## 内容`（微信公众号或机构网页整理的全文）。`registry.yaml` 中 id 仍为 `PH_SUPP_01`～`PH_SUPP_15`。正式引用请以原网页为准。

| # | id | 路径 |
|---|-----|------|
| 1 | PH_SUPP_01 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/天水幼儿园铅中毒事件舆情动态汇总.md` |
| 2 | PH_SUPP_02 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/2025年上半年国内食品安全热点舆情分析.md` |
| 3 | PH_SUPP_03 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/人民舆情2019上半年突发公共事件舆情应对.md` |
| 4 | PH_SUPP_04 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/人民舆情热点事件是如何引爆舆情的.md` |
| 5 | PH_SUPP_05 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/蚁坊新冠肺炎疫情舆情汇总分析2020-2022.md` |
| 6 | PH_SUPP_06 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/蚁坊二阳相关话题网络舆情热度分析.md` |
| 7 | PH_SUPP_07 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/蚁坊2025年10月医疗类网络舆情风险预警分析.md` |
| 8 | PH_SUPP_08 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/蚁坊退烧药相关网络热度分析.md` |
| 9 | PH_SUPP_09 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/蚁坊近期疫情相关热点事件舆情报告.md` |
| 10 | PH_SUPP_10 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/人民网三评网售处方药乱象.md` |
| 11 | PH_SUPP_11 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/核酸检测结果延迟出报告舆情报道.md` |
| 12 | PH_SUPP_12 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/反思肖某舆情事件刀刃向内重塑信任.md` |
| 13 | PH_SUPP_13 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/蜜雪冰城食品安全投诉与加盟模式舆情观察.md` |
| 14 | PH_SUPP_14 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/艾媒罗永浩西贝预制菜争议消费者认知调研.md` |
| 15 | PH_SUPP_15 | `opinion_analysis_kb/domains/health/materials/公卫舆情报告补充/医疗保障基金监管新规解读人民日报.md` |

**登记条数**：registry 全量 **45** 条（含 Wiki 15 + materials 15 + PH_SUPP 15）。**计「领域文档篇数」时**：**不计** Wiki 15 → **15 + 15 = 30**（见第四节表首）。
