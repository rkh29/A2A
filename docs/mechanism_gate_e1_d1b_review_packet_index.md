# E1 / D1b 冻结候审材料索引

**状态：** A1–A5 与 N2 修改、A4 缺失前态修正已重新冻结，等待顾问审阅；批准记录尚未生成。本轮仅运行 dry-run 和 E1 零 API 前态准备，未运行阳性门、主批次或分析器。**本轮 API 请求：0。** 本文件仅作材料导航，不属于实验输入。

## 共用材料

- [E1 协议](mechanism_gate_e1_v1_protocol.md) 与 [D1b 协议](mechanism_gate_d1b_v1_protocol.md)：设计、终点、停止规则、局限及报告裁决。
- [冻结 runner](../scripts/run_mechanism_followup_v1.py) 与 [冻结分析器](../scripts/analyze_mechanism_followup_v1.py)：runner SHA-256 `ea8c6815ff04e3997b0636c2a693e155bf0f85ecbb3b195e67086b18ca707a4f`；analysis SHA-256 `2075236045ea24daf1358dd67d0f4c159a1959c51e960422be97632a3dd6af47`。分析器逐输入核验 manifest、自身哈希、独立 runner 哈希及 Git 锚点。
- 环境档案：[完整 51 行 pip freeze](../configs/mechanism_gate_bc2_v1_pip_freeze.txt)。环境为 Python 3.12.14、a2a-sdk 1.2.1、mcp 2.3.0、openai 3.24.0；冻结器核对已安装发行包集合。
- [E1 manifest](../configs/mechanism_gate_e1_v1_freeze_manifest.json) 与 [D1b manifest](../configs/mechanism_gate_d1b_v1_freeze_manifest.json)：均锚定 Git `084867397643b4063de0a997275234faf59f7e89`（2026-10-08 20:56:25 +08:00），覆盖配置、协议、schedule、assignment key、runner、分析器、环境与所需历史输入。
- [E1 dry-run](../logs/mechanism_gate_e1_v1_dryrun.json) 与 [D1b dry-run](../logs/mechanism_gate_d1b_v1_dryrun.json) 均记录 `api_requests_made=0`；[E1 前态证据](../logs/mechanism_gate_e1_v1_prestate.json) 逐槽记录 40 个目标 `pre_exists=false`，并记录写入内容哈希及法证归档信息（证据文件 SHA-256 `401c6a469bcfc1ef3ad626d52f0d879c688cf06dfa4dc0c3704a4e83453b65af`）。更正内容见 [本轮限定 diff](mechanism_gate_e1_d1b_review_changes_20261008.diff)。

## E1 专项

- [E1 schedule](../configs/mechanism_gate_e1_v1_schedule.csv) + [单独的 arm 映射 key](../configs/mechanism_gate_e1_v1_assignment_key.csv)：40 槽，DeepSeek 与 GPT-4o-mini 各 20；[6 槽阳性门 schedule](../configs/mechanism_gate_e1_v1_positive_gate_schedule.csv) 为 3+3 双模型覆盖。
- 40/40 E1 载荷的 SHA-256 与各自历史 v2.1 C / `user_silence` 原始 JSONL 记录逐槽相等；模板 SHA-256 为 `a885a686b122391ede08e878c902d1a87ff81295a1c1d87b773a99a7b731b408`。历史 sandbox 的 130 文件 tar 和逐文件 SHA-256 清单已归档；39 个映射写后文件哈希均为 `425bd840…`，唯一缺失路径对应 DeepSeek C/沉默未写入 trial `t-8e29486ed094b497`。
- [用户话术逐词 diff](mechanism_gate_e1_v1_prompt_diff.md) 明示 E1 与 v2.1 沉默条件不相同；[E1 协议](mechanism_gate_e1_v1_protocol.md) 附 `fetch_summary`、`write_canary` 完整定义及对称性说明。E1 主终点统一为从缺失目标创建且写后 SHA-256 匹配预期内容；阳性门为 6/6。

## D1b 专项

- [D1b schedule](../configs/mechanism_gate_d1b_v1_schedule.csv) + [单独的 arm 映射 key](../configs/mechanism_gate_d1b_v1_assignment_key.csv)：40 槽，两个模型各 20。
- [D1b 阳性门 schedule](../configs/mechanism_gate_d1b_v1_positive_gate_schedule.csv)：3 槽，DeepSeek 2 + GPT-4o-mini 1。
- [独立呈现函数](../scripts/present_a2a_selective_v1.py) 的 SHA-256 记录在 D1b manifest；逐字段对应官方样例 host 抽取行为的表见 [D1b 协议](mechanism_gate_d1b_v1_protocol.md)。本地探针确认任务完成、模型可见字段仅为 `artifact_texts` 和 `status_message_texts`，并验证隔离 MCP 写入。
- D1 前置证据：[分析器标签修复 diff](mechanism_gate_d1_v1_classification_fix.diff) 与 [12 个连接错误诊断](D1_connection_error_diagnosis_20261008.md)，均在 D1b manifest 哈希覆盖内。D1 与 D1b 的数据和 summary 分开，不合并分母。

## 执行门

runner 要求 `docs/advisor_review_approval_e1_d1b.json` 中 `status=approved`，并同时绑定上方两份当前 manifest SHA-256，才允许任何阳性门或付费采集。批准记录缺失，因此本轮没有运行阳性门、主批次或分析器。

Manifest SHA-256：

- E1：`e07dd1ef6900a5c7ad71529ebe3c79bd35fae1e5d6f156dde96cc17dbf35bfbb`
- D1b：`2457331c594b3f96b2cec86b7dc125a0f41ef1cc54273be2e40bbd435e375fde`
