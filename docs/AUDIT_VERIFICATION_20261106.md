# 审计方核验响应（2026-11-06）

本文档是对 `docs/AUDIT_RESPONSE_20261106.md`（开发侧整改响应，分支
`audit-response-20261106`）的**独立核验**。核验方与首次审计相同，全部测量在本机
fresh 执行、零缓存、官方评测器（SHA `da1bbcd4…`，与开发侧声明互验一致）、
`--meta_time_out 30`。

**总裁决：开发侧响应的关键主张全部核验通过。首次审计的"分数虚高"结论系审计方
gold 版本错误所致，予以正式撤回并更正。P0-1 冻结解除，发包放行。**

---

## 1. gold 版本之争的裁决（独立证据链）

开发侧主张：审计所用 `9b4c38da…` 是 2024-06-27 修订**前**的旧注释集，现行官方
gold 为 `630272f2…`。核验方独立取证：

| 证据 | 结果 |
|---|---|
| 官方 OSS 直链当日 fresh 下载 `dev.zip` | **SHA `cdd6d19f…`，与开发侧 11-02 版逐字节一致**；Last-Modified 2024-06-29 |
| zip 内 `dev_20240627/dev.json` | **SHA `630272f2…` ✓**；`dev.sql` = `42087d70…` ✓；`dev_databases.zip` = `8132aacd…`（与审计 §2 一致）✓ |
| qid 5 题文 | 现行官方 = "Math **greater than** 400"（gold→4 = pred→4）；审计旧版 = "under 400"（gold→0）✓ |
| 难度计数 | 现行官方 = 925 / **464** / **145**；审计旧版 = 925 / **465** / **144** ✓（qid 831 六月修订改类） |
| 审计 gold 的本地来源记录 | `OFFICIAL_RECHECK_POLICY_FREEZE_SAFE.json` 自书："original frozen BIRD Dev1534; **not the later** … release"，`original_raw_dev_sha256 = 9b4c38da…` |

**结论：开发方主张成立。** 审计方当时从本地工作区取用了一份明知为"原始冻结版"
的旧 dev.json，且对照实验（public v1 = 66.23%）只是与同一旧 gold 生态的历史记录
互证——循环验证。层计数指纹（465/144 vs 464/145）是开发方指出的正确判别式。

## 2. 全矩阵复评结果（fresh，逐位对照）

官方 gold = 钉版 `dev.sql`（`630272f2`/`42087d70` 派生）；旧 gold =
`9b4c38da` 派生；数据库 `8132aacd` 两边一致；评测器 `da1bbcd4`。

| 产物 | gold | 审计方本次实测 | 开发侧声称 | 判定 |
|---|---|---|---|---|
| public v1（`0dede3dd…`） | 现行 | **65.19%**（72.00/55.60/52.41）= 1000 | 1000 | ✅ 逐位 |
| v19 池（`aae94afe…`） | 现行 | **69.30%**（74.49/62.07/59.31）= 1063 | 1063 | ✅ 逐位 |
| 上游 DeepEye 发布预测 B（`0d073da4…`，git blob） | 现行 | **74.38%**（79.89/68.10/59.31）= 1141 | 1141 | ✅ 逐位 |
| shipping v16（`510c9104…`） | 现行 | **74.71%**（79.46/68.97/62.76）= 1146 | 1146 | ✅ 逐位 |
| submission 导出（`f9f3322b…`） | 现行 | **74.71%** = 1146 | 1146 | ✅ 逐位 |
| 上游 DeepEye B（同内容） | 旧 | **69.17%**（75.46/60.86/55.56）= 1061 | （= 首次审计值） | ✅ 闭合 2×2 |
| shipping v16 | 旧 | 69.49% = 1066 | — | 新数据点 |

**6/6 项可测声称逐位复现。** 同一 B 文件内容旧 gold 1061 / 现行 gold 1141、
v16 旧 1066 / 现行 1146——审计报告的"+71/+82"完全是 gold 版本效应，
不存在分数虚高。

## 3. 对反问 ①–⑤ 的逐条回答

**① `9b4c38da…` 出处**：本地 NL2SQL360 数据树（经
`execution_20261020_official_original_dev_recheck_v510/terminal_snapshot/data/dev.json`
取用）。该目录自带的 `OFFICIAL_RECHECK_POLICY_FREEZE_SAFE.json` 明确记录其为
"original frozen BIRD Dev1534; not the later release"——即工作区自己留存的
修订前旧快照。审计失误：未与官方直链核验即当作现行 gold 使用；对照实验存在
循环验证缺陷。已在 `AUDIT_RECHECK_20261106.md` 顶部加更正声明。

**② `6f5bb278…` 出处与污染假说**：核验 `git show HEAD:results/bird-dev/qwen3.6-27b.json`
（upstream checkout @ `252ab45a`）得 blob SHA `0d073da4…` ✓，与开发侧一致；
但工作树副本与 blob **解析内容完全相同，仅 LF/CRLF 行尾差异**（git status 干净、
autocrlf 检出所致）。**"deepseek 运行污染工作树"假说不成立**——审计测的就是
同一份预测内容，1061 vs 1141 的差异全部由 gold 版本解释（见 §2 闭合 2×2）。

**③ 钉版现行 gold 重跑**：已完成，见 §2——1000/1063/1141/1146 全部逐位复现
（含三层分数）。

**④ `close_pool1534_cache_only68.py` 路径（归档用）**：
`D:\text2sql\execution_20261030_v19_dev1534\close_pool1534_cache_only68.py`
（同目录另有 `diagnose_pool1534_68.py`、`complete_dynamic68.py`）。**同时正式
更正**：首次审计把该脚本中的 labels/`skipped_selected_success` 机制推断为 v19
缺口的成因——**该因果归因错误**，缺口纯属 gold 版本；开发侧 cacheless 复测与
历史判定逐位一致即为反证。缓存/标签架构作为治理关注点保留（开发侧 P2-2 已
接受并固化官方评分路径），但不再是数字问题。

**⑤ P0-1 冻结**：**解除**。条件与后续节奏：
- P2-1 消融（Pool-P-own-only vs 全池）现在可以跑，结果请按
  `EXTERNAL_CORPORA.md` 计划登记；
- 发包 kit（EMAIL_DRAFT + README，`f9f3322b…`）**放行**——头条数字 1146/74.71%
  已由审计方独立逐位复现；
- P1-4（Windows 兼容 + 双 OS CI）按原计划两周内完成。

## 4. data/ 产物与复现链验证

- `data/MANIFEST.sha256` 五项产物 **blob 级 SHA 全部核验通过**（含头条
  `510c9104…`、官方导出 `f9f3322b…`）；
- 脱敏抽查通过：`unified_pool.goldfree.json`（1534 题）/ `DE12_POOL.goldfree.json`
  （1520 题）中无 `gold/correct/EX` 类字段，簇结构仅含 `sql/fams/support/n/empty`
  等可观测执行证据；
- 修复后的 `reproduce_shipping_v16.py`（`--dev-root/--deepeye-b`，输入 SHA 钉版，
  `expanduser` 修复）：核验方在本机以 blob 级 data 文件 + 官方 dev root + 上游
  B 文件完整运行，结果见 §5；
- `scoring/` 评分路径钉版（评测器 + gold SHA + `HISTORICAL_NUMBERS.json` 账本）
  结构合格，账本中的"旧/新 gold 分列 + 状态标注"是本次能快速对账的关键。

## 5. 复现脚本运行结果

核验方在本机以 blob 级 data 文件（LF）+ 官方 dev root + 上游 B 文件完整运行
`reproduce_shipping_v16.py`：

- 输入 SHA 校验全过（`d4d14758` / `a1a6523c` / `4acbd36c` / B `0d073da4`）；
- **1533/1534 逐题一致，仅 qid 584 一处翻转**；
- 翻转机制已定位：qid 584（codebase_community，7 GB 级大库）的 B SQL 单独
  执行仅 2–3 s，但全量 14 线程并发下撞上 `func_timeout(25)` 上限 → gate 未触发；
  **单独复算该题特征（a=b=1028 行、dm=12、am=12、ncl=7）时 F4 正常触发，
  与冻结版决策一致**。即规则逻辑正确且确定，唯一跨机差异源是 25 s 执行
  超时边界在并发负载下的敏感性；
- 结论：开发侧"byte-identical"声称在其机器上成立、在本机 1533/1534 成立且
  唯一差异可完全归因于超时边界。**建议**（非阻塞）：在 SPEC 或脚本 README
  注明该边界敏感性；可选改进——特征阶段对超时题做一次串行重试以消除
  负载敏感性。另注：Windows autocrlf 检出会使 data 文件变 CRLF 导致 SHA
  校验失败（见 §6 新增小项）。

## 6. 更正与保留事项汇总

**撤回**（首次审计报告，见 `AUDIT_RECHECK_20261106.md` 顶部更正声明）：
- §1/§3 的"虚高 71/82 题"结论及 93 题分歧表——gold 版本效应，非评分虚高；
- §4.4 对 v19 缺口的缓存/标签继承因果归因——错误；架构治理关注点保留；
- §2 对照实验的方法论有效性声明——存在循环验证缺陷。

**维持**：
- F1 复现链断裂（审计时点属实；现已修复并验证）；
- F3 DeepEye 产物溯源标注错误（开发侧全盘接受并已修正为"上游发布预测文件"
  双产物条款；1143 声称按其说明为 240 s 超时配置下的历史实测，30 s 现测 1141）；
- F5 Windows 兼容（按期修复中）；
- F6 外部 Dev 预测语料的泛化风险（开发侧已登记披露并排期消融）。

**新增小项**（非阻塞，建议随 P1-4 一并处理）：
- Windows autocrlf 检出会使 `data/` 文件变 CRLF，`MANIFEST.sha256` 校验在
  Windows 检出下失败（Linux 正常）。建议加 `.gitattributes`：`data/** -text`。
- `shipping_v16_predicted_sql.json` 内部格式为 `SQL\t----- bird -----db_id`
  （分隔符后无 tab），与官方评测器要求的 `\t----- bird -----\t` 不同——评分
  应使用 `submission_pred_v1146.json`（两栏规范，已验证等价）。建议在
  `SELECTOR_V16_SPEC.md` 或 `data/README` 注明两者的用途区别，避免第三方
  直接拿内部格式评分得到 0 分。
