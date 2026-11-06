# 独立合规复评报告（2026-11-06）

> ## ⚠️ 更正声明（2026-11-06 晚间，见 docs/AUDIT_VERIFICATION_20261106.md）
>
> 本报告使用了一份**修订前的旧版 dev.json**（SHA `9b4c38da…`，2024-06-27 修订
> 前快照）作为 gold。现行官方 gold（publisher dev.zip `cdd6d19f…` → dev.json
> `630272f2…`，经官方直链独立下载核验）下，本报告 §1/§3 的"分数虚高"结论
> **不成立并予以撤回**：v19 = 1063、DeepEye = 1141、v16 = 1146 均已逐位复现。
> §4.4 的缓存/标签继承因果归因亦撤回（缺口纯属 gold 版本差异）；对照实验存在
> 循环验证缺陷。维持的发现：F1（当时复现链断裂，现已修复验证）、F3（溯源
> 标注错误，开发侧已接受并修正）、F5（Windows 兼容）、F6（外部语料风险）。
> 详见 `docs/AUDIT_VERIFICATION_20261106.md`。

本报告由独立复评完成：按内部《NL2SQL 官方评测参考规范》对本仓库声称的
Dev 1534 官方 EX 结果做端到端核验。评测环境、方法、原始数字、逐题分歧清单
全部在下文给出，可由任何人用同样输入复算。

---

## 1. 摘要

| 产物 | 仓库/记录声称 | 独立复评实测（官方评测器） | 判定 |
|---|---|---|---|
| public v1 管线输出 | 66.23%（1016/1534） | **66.23%**（1016/1534，simple 71.89 / moderate 58.71 / challenging 54.17 逐层吻合） | ✅ 可复现（对照基准） |
| v19 生成池（v16 选择器的 Pool P 输入） | 69.30%（1063/1534） | **64.67%**（992/1534） | ❌ 多算 71 题 |
| "Upstream DeepEye-SQL run" | 74.51%（1143/1534，DEEPEYE_ADAPTATIONS.md 称 "official evaluator, local rescore"） | **69.17%**（1061/1534） | ❌ 多算 82 题 |
| shipped v16 最终提交 | 74.71%（1146/1534，SHA-256 `510c9104…2061c3`） | **无法评测：文件不存在** | ❌ 不可验证 |

**结论：本仓库当前可验证、可复现的合规成绩是 66.23%。头条 74.71% 无法复现；
其两个组成声称（v19 池 69.30%、DeepEye 74.51%）在官方评测器下实测分别只有
64.67% 与 69.17%，虚高约 4.6–5.3 个百分点。**

另外确认四件独立事实：

1. `reproduce_shipping_v16.py` 无法运行（缺 4 个冻结产物；另有 `~` 路径 bug）。
2. CLI `run`（从零生成）被 `RuntimeError` 禁用，README 的"Full pipeline from
   scratch"路径不可执行。
3. README 声称 *"we inherit no published scores from any pipeline"* 与
   "DeepEye-SQL 1143 = 74.51% local rescore" 的实际来源矛盾（见 §4.3）。
4. 测试套件 81 通过 / 40 失败（全部为 Windows 平台兼容性问题，非逻辑缺陷）。

---

## 2. 复评环境（全部输入带 SHA-256，供复算）

| 输入 | SHA-256 |
|---|---|
| 官方评测器 `evaluation_ex.py`（本地 6 份副本互验一致） | `da1bbcd4530be83692d7c650c814ea9704bb710d0c953eb75d02ccb38233cf89` |
| BIRD dev.json（1534 题含 gold；本地所有全量副本互验一致） | `9b4c38da7f07999180dc1a463e03371e3b4eb685ce8a110e5944eb8fe3a70853` |
| 官方 `DEV_DATABASES.zip`（11 库） | `8132aacfe61ecec7198a62e93cb8512dac89b1d6b9922fe3c7bb94b13f317d8f` |
| v19 预测 `predicted_sql.json` | `aae94afe7cd7892ab461f1e295aa138075fea2aaba198e1e022b25b8250ad396` |
| DeepEye 输出 `qwen3.6-27b.json` | `6f5bb2784d46095bad633e17c7152275a00b199fd79fb3d0d1a8ed498c8bd897` |

方法：解压官方数据库到新目录 → 用官方 `evaluation_ex.py`（30 s meta-timeout、
BIRD set-equality、UNKNOWN 记 0 且留在 1534 分母）→ 预测文件转官方
`SQL\t----- bird -----\tdb_id` 格式 → gold 由官方 dev.json 生成。

**对照验证（评测链路可信性证明）**：用同一环境复评本地 public v1 冻结产物
`predicted_sql.json`，得到 66.23% 且 simple/moderate/challenging 三层分数与
其历史记录 `evaluation_summary.json` 逐位一致。据此，其余产物在同一环境下的
测量结果同样可信。另用独立逐题脚本复核 v19（992 题正确），与官方评测器
输出完全一致。

---

## 3. 逐题差异定位（v19：声称 1063 vs 实测 992）

对 v19 记录 `FINAL_OFFICIAL1534_RESULT.json` 的逐题 EX 与官方语义逐题复评
做差，共 93 处分歧：**82 题记录为对而官方评测为错，11 题相反**。

- 82 个"多算"题中，66 题的预测结果集**不匹配本库任何一题的 gold 结果**
  （排除了错位对齐假设；也无一致偏移量）；抽样语义检查确认是真实答错
  （例如 qid 5：题问 Math **under** 400，预测用 `AvgScrMath > 400`，gold 返回
  `0`，预测返回 `4`；qid 92：预测返回单行 count 69，gold 返回 69 行明细）。
- 11 个"少算"题与 30 s 预算按缓存 elapsed 累计的记账方式一致（真正相等的
  结果对被预算规则判 0）。

差异题号清单（供开发侧逐题核对）：

- 多算 82 题：5,23,74,89,92,96,99,100,106,113,128,132,139,143,153,155,162,
  163,164,176,179,181,187,192,205,212,226,227,228,248,255,277,284,298,313,
  337,395,401,413,416,451,474,479,512,532,583,592,664,716,730,738,761,811,
  828,846,857,876,895,897,934,939,940,945,948,969,982,987,1091,1116,1122,
  1145,1152,1171,1246,1257,1259,1270,1352,1425,1446,1467,1516
- 少算 11 题：16,87,93,110,144,847,1032,1092,1168,1241,1271

---

## 4. 发现详情

### 4.1 F1 — 复现链路断裂（严重）

`reproduce_shipping_v16.py` 依赖 4 个冻结产物：`shipping_v15_predicted_sql.json`、
`unified_pool.json`、`DE12_POOL.json`、`shipping_v16_predicted_sql.json`。

- 仓库未附带其中任何一个；GitHub 无 release/tag/其他分支；
- 本地工作区全盘扫描 836,689 个 JSON 文件，**没有任何文件匹配提交声称的
  SHA-256 前缀 `510c9104`**；
- 脚本存在 bug：`DEV = Path('~/bird_dev_check/dev_20240627')` 从未调用
  `.expanduser()`，`~` 被当作字面子目录（Linux 下同样会失败，除非恰好存在
  字面 `~` 目录）。

因此 SELECTOR_V16_SPEC.md §5 的"byte-for-byte 复现 + SHA 校验"声称当前
不可执行。

### 4.2 F2 — 从零生成路径不可执行（严重）

`src/birdfusion/cli.py` 的 `run` 子命令无条件
`raise RuntimeError("Live CLI disabled …")`。README「Reproduction §2」描述的
"Full pipeline from scratch"在仓库现状下无法运行；v19 配置中的网关
`base_url` 为占位符，模型权重与网关均不随仓库分发。

### 4.3 F3 — "DeepEye-SQL run 1143 = 74.51% (local rescore)" 声称与事实不符（严重，诚信项）

- `results/bird-dev/qwen3.6-27b.json`（Pool D 决胜规则 D/E/F 使用的
  "DeepEye pipeline's own final SQL"，即复现脚本里的 `qwen3.6-27b.json`）是
  **上游 HKUSTDial/DeepEye-SQL 仓库 @ `252ab45a` git 追踪的官方发布预测文件**
  （`git ls-files` 可验证；上游 README 直接链接它并公布 BIRD-Dev EX 74.5）。
  它不是本地复现产物。
- 74.51% = 上游公布数字 74.5 换算为 1143/1534；同一文件用官方评测器 + 官方
  gold 实测 **1061/1534 = 69.17%**。
- 基线决策记录（本地工作区 `execution_20261010_gateway_baseline_v1/BASELINE_DECISION.json`）
  明确写着 `complete_pipeline_deployed: false`、`only_deepseek_LLM_requests: true`
  ——DeepEye 完整管线从未在本地部署执行，当时网关模型列表中也不存在 qwen 模型。
  DE12_POOL（12 采样 × 1520 题，共 18,240 条 SQL 的"本地复现"池）在本地
  工作区无任何痕迹。
- 命名亦不一致：README/SPEC 称 Pool D 模型为 `qwen3.8-27b`，实际文件名为
  `qwen3.6-27b`。

由此，README 的 *"we inherit no published scores from any pipeline"* 与
*"Every number is measured locally with the official BIRD evaluator"* 两条
声明均与事实矛盾。

### 4.4 F4 — 评分环节使用了带缓存/标签继承的自研 harness（严重，是虚高的机制来源）

v19 及之后的所有正式数字（1063/69.30%、1096/71.45%、1092/71.19% 等）均出自
Linux 评分机上的自研 "official-comparator" harness（记录中引用的
reference_SHA `630272f2…`、official_source_SHA `2f591e55…` 均不在本机），
而非仓库分发、可复算的官方 `evaluation_ex.py` 路径。工作区留存的同类脚本
（`close_pool1534_cache_only68.py`）显示该架构包含：

- `if (qid, sql) in labels: return labels[...]` —— 直接继承历史判定，不执行；
- `skipped_selected_success` —— 前次结果标对即短路跳过；
- 结果几乎全部来自跨目录缓存（v19 记录中 `cache_hits_old: 1373`，
  即 1534 题里 89% 走缓存）；
- 30 s 预算按缓存 elapsed 累计，可把结果真正相等的题判 0（对应 §3 的 11 题）。

public v1（66.23%）是唯一走"官方评测器子进程直接执行"路径的记录，也是唯一
精确复现的记录——这与上述机制解释一致。

### 4.5 F5 — Windows 平台不可运行（中）

测试套件 81 通过 / 40 失败：`test_budget_ledger*`、`test_request_ledger`、
`test_execution_cache`、`test_sqlite_snapshot`、`test_pipeline`、
`test_sql_exec_safety` 中的失败均为 Windows 特有语义（临时目录清理时 sqlite
句柄未释放 WinError 32、文件名含 `?#` 非法字符、symlink 权限语义）。规范
"保证提交代码、模型和依赖可独立运行"在 Windows 主机上不成立。

### 4.6 F6 — 与内部规范的合规对照

| 规范条款 | 结论 | 依据 |
|---|---|---|
| §1 评测结果可复现 | ❌ | F1、F4：头条不可复现；可复现部分实测 66.23% |
| §2.2/§4 Dev 过拟合控制 | ⚠️ | Pool P 明确包含 "nine public/historical prediction corpora"，Pool D 是上游对**同一 Dev 集**的发布预测——等效蒸馏他人在 Dev 上的调优成果；本仓库自己的 COMPLIANCE.md 将"旧预测账本"列为推理禁止输入，与实现自相矛盾 |
| §3 Ground Truth 使用 | ✅ | 代码层面合规：`public_input.py` 物理剥离 gold 字段；gold 仅由独立 `evaluate` 命令在预测冻结后读取；TRAIN 偏好选择器只用 TRAIN gold（与 Dev 零库重叠） |
| §5 Few-shot 来源 | ✅ | v19 配置 train examples 仅来自 TRAIN |
| §6 数据库内容访问 | ✅ | 只读 sqlite + `PRAGMA query_only`，不上传数据库 |
| §7 工具/Agent 记录 | ✅ | 预算/请求账本完整（本仓库做得好的部分） |
| §1 代码可独立运行 | ❌ | F2、F5 |

---

## 5. 本报告未能覆盖的部分（诚实边界）

- 本机无 GPU、网关未运行，**生成阶段无法从零重跑**；本报告对"分数声称"的
  核验方式是复评既有冻结预测，这恰好覆盖了"可复现"声称应当满足的路径。
- 评分机（Linux）上的 reference 文件（SHA `630272f2…`）不在本机，无法判断
  其与官方 dev.json gold 的语义差异；但无论其内容如何，"官方评测器 + 官方
  gold"的合规复评结果以本报告为准。
- 82 题多算的**精确污染入口**（哪一次历史运行的哪条缓存/标签）无法仅凭本机
  数据定位；机制本身（§4.4）已由留存脚本证实。

## 6. 证据索引（本地工作区）

- 复评脚本与输出：`birdfusion/recheck_20261106/`（`eval_deepeye.log`、
  `perq_compare.py`、`v19_disagree.json`、三份评测 work 目录）
- v19 记录：`execution_20261030_v19_dev1534/FINAL_OFFICIAL1534_RESULT.json`
- 对照记录：`bird_fusion_public_20261024/artifacts/dev_public_v1/evaluation_summary.json`
- 基线决策：`execution_20261010_gateway_baseline_v1/BASELINE_DECISION.json`、
  `REPORT.txt`、`GATEWAY_MODEL_DISCOVERY.json`
- 上游源码与产物：`execution_20261010_gateway_baseline_v1/upstream_DeepEye_SQL/`
  （commit `252ab45a`，`results/bird-dev/qwen3.6-27b.json` 为 git 追踪文件）
