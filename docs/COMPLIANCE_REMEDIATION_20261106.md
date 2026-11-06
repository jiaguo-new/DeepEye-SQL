# 合规整改指示（2026-11-06）

配套文件：`docs/AUDIT_RECHECK_20261106.md`（独立复评报告，下称"报告"）。
开发侧按以下优先级执行整改；每项附**验收标准**，未达验收标准不算完成。

总原则（对应内部《NL2SQL 官方评测参考规范》）：

> 一切对外声称的分数，必须能由第三方用**官方评测器 + 官方 dev.json gold +
> 官方 dev_databases** 在全新环境逐字节复算；分数只在预测冻结后由独立评分
> 命令产生；推理与选择阶段不引入任何 Dev/Test gold 或历史 Dev 预测账本。

---

## P0 — 必须立即处理（涉及数字真实性与诚信声明）

### P0-1 用官方评测器全量重测所有对外数字

- 废弃"缓存/标签继承"式评分路径：任何 `(qid, sql) → verdict` 的历史判定
  继承（`labels`、`skipped_selected_success`、跨目录 `sql_cache` 判定复用）
  一律不得参与对外分数的计算。结果集缓存仅可用于加速执行，**判定必须每次
  由当次执行结果与当次 gold 结果按官方 set-equality 重新比较**。
- 逐项重测并公开（含 per-question EX 明细）：
  - v19 生成池（当前记录 1063；报告实测 992 = 64.67%）
  - DeepEye 文件 `qwen3.6-27b.json`（当前声称 1143；报告实测 1061 = 69.17%）
  - 如能找回 v16 输出，同样重测；找不到则按下述 P0-3 撤回声称。
- 评分环境固定并随结果公布：评测器、dev.json、dev_databases、gold 序列化
  全部带 SHA-256（报告 §2 已给出本次复评的基线值，可直接沿用）。

**验收**：在全新机器上，用官方 `evaluation_ex.py` 对冻结预测文件复算，得到
与公布数字逐位一致的结果（含 simple/moderate/challenging 三层）。

### P0-2 修正"DeepEye-SQL run 74.51% local rescore"的来源声明

- 该数字实为上游公布值（上游 README：BIRD-Dev EX 74.5），且
  `qwen3.6-27b.json` 是上游 git 追踪的**发布预测文件**，非本地复现产物。
  DEEPEYE_ADAPTATIONS.md 的 "local rescore" 表述与 BASELINE_DECISION 记录
  （`complete_pipeline_deployed: false`）矛盾。
- 处理方式二选一，不得维持现状：
  1. **如实标注**：README 结果表中该行改为 "DeepEye-SQL published Dev
     predictions (upstream artifact), locally rescored to 69.17%"，并在
     DEEPEYE_ADAPTATIONS.md 撤回"完整管线复现"表述；
  2. 或真实完成一次 DeepEye 管线本地复现（网关 + 模型 + 12 采样池全部落地、
     留存 DE12_POOL.json），再以实测数字替换。
- 同时解决模型命名不一致（README 称 `qwen3.8-27b`，实际文件
  `qwen3.6-27b`）。

**验收**：仓库内不存在任何"把上游公布数字表述为本地实测"的文本；
README 的 *"we inherit no published scores from any pipeline"* 声明与事实一致
（若继续使用上游预测文件作为 Pool D 证据，则该声明必须删除）。

### P0-3 撤回或落地 1146/74.71% 头条声称

- 当前 `shipping_v16_predicted_sql.json`（SHA `510c9104…2061c3`）不存在于
  仓库、GitHub release 与本地工作区（全盘扫描 836,689 个 JSON 无匹配）。
- 处理方式二选一：
  1. 找回该文件并附 SHA 校验发布（GitHub release 附件），同时按 P0-1 重测；
  2. 找不回则在 README/SPEC/SUBMISSION_NOTES 中明确撤回 74.71%，头条改为
     已验证数字（当前唯一可复现的是 public v1 的 66.23%）。

**验收**：任何人能从仓库（含 release）取得头条数字对应的预测文件并复算。

---

## P1 — 一周内完成（可复现性与可运行性）

### P1-1 修复 `reproduce_shipping_v16.py`

- `Path('~/bird_dev_check/dev_20240627')` → `Path.home() / 'bird_dev_check' / …`
  （补 `expanduser`）；
- 增加 `--dev-root/--pools-dir` 参数与产物清单校验（启动时核对每个输入文件
  的 SHA-256，缺文件时给出**明确错误与获取方式**，而不是裸 FileNotFoundError）。

**验收**：在只有 BIRD 官方数据 + 本仓库 release 产物的全新机器上，脚本
可运行并输出 SHA 校验结果。

### P1-2 冻结产物入库或入 release

- `unified_pool.json`、`DE12_POOL.json`、`shipping_v15_predicted_sql.json`、
  `shipping_v16_predicted_sql.json` 打包为 GitHub release 附件（含
  MANIFEST.sha256）；SELECTOR_V16_SPEC.md §1 引用该 release。
- 每个池必须附**来源说明**：候选由哪个模型/配置生成、历史预测语料各自的
  出处与许可（见 P2-1 的连带要求）。

**验收**：`git clone` + 下载 release 后，无需任何私有路径即可完成复现。

### P1-3 CLI `run` 解禁或如实降级

- 现状是 `raise RuntimeError("Live CLI disabled …")`，而 README 宣称
  "Full pipeline from scratch" 可行。二选一：
  1. 实现并启用（网关凭据走环境变量/credential_config，缺省拒绝启动）；
  2. README 改为如实描述"生成路径需私有网关，仓库仅提供选择器复现"。
- 同时把 `run` 禁用状态写入 COMPLIANCE.md 的"已知限制"。

**验收**：README 的复现三步中，每一步要么可执行、要么明确标注前置条件。

### P1-4 Windows 兼容

- 修复 40 个平台失败：临时目录清理前关闭 sqlite 句柄；测试文件名去掉
  `?#` 等非法字符；symlink 用例按平台跳过或改用 junction。
- 建议 CI 增加 `windows-latest` + `ubuntu-latest` 双矩阵跑 `pytest tests/`。

**验收**：两个平台 `python -m pytest tests/` 全绿（允许 sqlglot 相关 skip）。

---

## P2 — 两周内完成（方法合规提升）

### P2-1 历史预测语料的合规治理

- Pool P 的 "nine public/historical prediction corpora" 与 Pool D（上游对
  **同一 Dev 集**的发布预测）逐个登记：来源、是否在 Dev 上调优过、许可。
- 对照规范 §2.2/§4 与本仓库 COMPLIANCE.md 自身的"推理禁止输入"清单做一次
  自查：**"他人产出的 Dev 预测"应视同旧预测账本**。要么全部剔除并重建
  选择器，要么在 COMPLIANCE.md 中把该边界如实写清（承认使用了外部 Dev
  预测语料及其对独立 Test 泛化的影响）。
- 若保留，需补一组消融：仅用自家生成池 vs 含外部语料池的 Dev/Test 差距，
  证明增益不来自对 Dev 的间接拟合。

**验收**：COMPLIANCE.md 的允许/禁止清单与代码实际输入一致，无自相矛盾；
外部语料清单与其对泛化的风险评估入库。

### P2-2 评分治理固化

- 仓库内新增 `scoring/` 目录固化唯一评分路径：官方 `evaluation_ex.py`
  原样副本 + gold 序列化脚本 + 输入 SHA 清单；`birdfusion evaluate` 只允许
  调用该路径。
- 任何自研缓存只加速执行，不缓存"对错判定"；评分日志记录每题
  pred/gold 的执行状态。
- 历史所有正式数字（1016/1063/1092/1096/1143/1146）补一份"官方评测器复算
  对照表"入库：能复算的给复算值，不能复算的标注撤回。

**验收**：`birdfusion evaluate --predictions X` 产出的分数与手工调用官方
评测器逐位一致；对照表入库。

### P2-3 诚实边界声明更新

- SELECTOR_V16_SPEC.md §6 的"已知上限"、README 的"Honest limits"在 P0/P1
  完成后同步改写：上限应基于**实测**数字重述，而不是基于虚高基线。
- README 头部成绩表在每行加"verified/unverified/retracted"状态标记。

**验收**：README 成绩表每行都可追溯到"预测文件 + 复算记录"。

---

## 处理顺序与责任建议

1. 先 P0-1（重测拿到真实基线）→ P0-3（头条二选一）→ P0-2（来源声明）——
   这三件事决定对外口径，需在继续任何方法迭代之前完成。
2. P1 全部完成前，冻结一切新方法实验（避免在虚高基线上继续叠规则——报告
   §4.4 的机制正是这样把误差滚大的）。
3. P2-1 的语料边界决定 v17 之后选择器的合法输入集合，应在设计新规则前定案。

## 本次复评的可复用资产

报告 §2 的输入 SHA 清单 + `birdfusion/recheck_20261106/` 下的脚本
（`build_eval_inputs.py`、`perq_compare.py`、`diagnose_alignment.py`、
`inspect_disagree.py`）可直接作为 P0-1 重测的起点。
