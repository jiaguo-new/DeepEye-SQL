> ⚠️ 本文件是提交时包的内部说明原样存档。其中引用的 `artifacts/dev_public_v1/`、
> `CURRENT_DEV1534_BASELINE.json`、`EXPERIMENTS_*.md` 等工作区文件不在本仓库分发范围内。
> 公开版说明见仓库根 `README.md`。

# BirdFusion Public v1

从 BIRD 原始公开输入重新生成 SQL 的端到端管线。推理阶段只使用 `question_id / db_id / question / evidence / difficulty`、SQLite 物理 schema、外键和公开存储值。**不读取或使用 Dev/Test gold、`wrong_qids`、逐题 EX、rescue/damage 或旧预测账本。**

## 方法

1. 由配置显式声明模型生成候选；当前实验版为 Qwen3.8-27B-only，DeepSeek 已按同题消融结论停用。
2. 固定提示：直接生成、语义重读后生成；另有显式声明外键/最短 JOIN 路径的消融模板。可选机制包括公开 TEXT 列值检索、BIRD `database_description` 表/列说明、候选派生的 linked-schema 二次生成，以及同库或跨域 masked 公开 train examples。
3. 候选在只读 SQLite 中执行，按 BIRD `set(rows)` 等价语义聚类（整数/浮点数值等价、重复行去重）的 SHA-256；超时、错误和超行数均为 UNKNOWN，不参与支持度。
4. 冻结盲选择：先确定 standard champion；替代答案必须跨模型家族或跨 schema-view，并同时跨模板、支持数至少 3，champion 只有一个来源且替代答案支持数至少多 2。
5. 全部预测冻结后才能单独运行 `evaluate`。推理入口不调用评分器。

这是一套**公开推理重实现**，不是把历史 1150 账本伪装成端到端代码。当前统一主版为发布者 BIRD Dev 20240627：v19 Qwen high、standard/reread 各两候选，实际 zero-shot，全量 **1063/1534＝69.30%，UNKNOWN 2 留在1534分母**。公开输入与参考统一，预测冻结后按原官方集合比较评分；复用匹配执行缓存，17道此前未执行的动态查询另做首次原官方函数执行。当前结果与成本见工作区 `CURRENT_DEV1534_BASELINE.json` 及 `execution_20261030_v19_dev1534/FINAL_OFFICIAL1534_RESULT.json`。未达到 >77%，不表示官方 Test 成绩或已获榜单资格。

历史公开版 **1016/1534＝66.23%** 的冻结产物仍保留在 `artifacts/dev_public_v1/`，但它不是当前发布版的对照，不能与上述新全量混用。

独立 MiniDev500 记录中，v19 high reasoning 冻结预测原评为 **354/500＝70.80%**，同预测复评为 **355/500＝71.00%**；复评多1题不是方法收益。配置虽有同库3-shot上限，实际500题均无同库示例。该旧 MiniDev 输入与发布版少量题意不同，不能当作上述 Dev 全量分数。详见 `EXPERIMENTS_20261028.md`、`EXPERIMENTS_20261030.md`。


## 提交版选择器 v16（本包版本）

本包提交的是 **v16** 多规则 gold-free 选择器，Dev **1146/1534 = 74.71%**（simple 79.46 /
moderate 68.97 / challenging 62.76，UNKNOWN 2 题留在 1534 分母）。相对 v10（1122）净 +24 题，
**新增 0 次模型调用**——全部增益来自复用已冻结的候选池与一个本地复现的 DeepEye-SQL 管线产物。

方法规范见 `SELECTOR_V16_SPEC.md`，可复现脚本见 `reproduce_shipping_v16.py`
（逐字节一致校验 SHA-256 `510c9104…2061c3`）。

规则链（逐条说明与阈值见 SPEC §2）：

| 规则 | 触发 | 说明 |
|---|---|---|
| v10 链 | 266 | 阶段一共识选择 + TRAIN 偏好佐证覆写 |
| A 空结果修复 | 10 | 当前选择 0 行且存在非空簇 → 换最受支持的非空簇 |
| C3/C5 碎片化与分裂票 | 16 + 16 | 簇数 ≥8，或簇数 ≥6 且 champion 支持≤3 且领先≥2 → 换最受支持的合并簇 |
| D/E/F DeepEye 佐证与决胜 | 27 + 10 + 54 | 仅用 DeepEye 管线自身候选的多数/一致性/行列表数等可观测证据做决胜 |

合规：全部 317 个改动选择都是冻结 gold-free 语料簇的成员（0 违规）；Dev gold 在生成与选择中
全程不参与，只由官方 evaluator 在冻结后读取计算 EX。TRAIN gold 仅用于 v10 链内那个只由 TRAIN
训练的冻结选择器（TRAIN 与 Dev 数据库交集为空）。

明确声明：**有一个能达到 1148（+2）的候选规则被有意弃用**，因为它只在 2 道 Dev 题上触发，
该样本量下与 Dev 拟合无法区分（记录在 `V17_NOISE_CANDIDATE.json`，未进入本系统）。
