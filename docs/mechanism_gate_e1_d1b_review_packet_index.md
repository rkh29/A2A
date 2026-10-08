# E1 / D1b 冻结候审材料索引

**状态：** 两个实验的协议、输入、分析器与预检证据已冻结，等待顾问审阅；批准记录尚未生成。**付费 API 调用：0。** 本文件仅作材料导航，不属于实验输入。

## 共用材料

- [E1 协议](mechanism_gate_e1_v1_protocol.md) 与 [D1b 协议](mechanism_gate_d1b_v1_protocol.md)：设计、终点、停止规则、局限及报告裁决。
- [冻结 runner](../scripts/run_mechanism_followup_v1.py) 与 [冻结分析器](../scripts/analyze_mechanism_followup_v1.py)：runner SHA-256 `79a3d0b73760b74ba5635d33b5438ed052b78bf164aafb03ca00d02add51bc7e`；analysis SHA-256 `14c6f882733decdc27181ee2866a6edea18a0bfc932cfa090bb3b7953e2221b2`。分析器逐输入核验 manifest、自身哈希、独立 runner 哈希及 Git 锚点。
- 环境档案：[完整 51 行 pip freeze](../configs/mechanism_gate_bc2_v1_pip_freeze.txt)。环境为 Python 3.12.14、a2a-sdk 1.2.1、mcp 2.3.0、openai 3.24.0；冻结器核对已安装发行包集合。
- [E1 manifest](../configs/mechanism_gate_e1_v1_freeze_manifest.json) 与 [D1b manifest](../configs/mechanism_gate_d1b_v1_freeze_manifest.json)：均锚定 Git `e81453660901548c57fc7634b63b189f6d3cfaf5`（2026-10-08 19:48:38 +08:00），覆盖配置、协议、schedule、assignment key、runner、分析器、环境与所需历史输入。
- [E1 dry-run](../logs/mechanism_gate_e1_v1_dryrun.json) / [D1b dry-run](../logs/mechanism_gate_d1b_v1_dryrun.json) 以及 [E1 本地探针](../logs/mechanism_gate_e1_v1_local_probe.json) / [D1b 本地探针](../logs/mechanism_gate_d1b_v1_local_probe.json)：均记 `api_requests_made=0`；两个本地探针均通过。

## E1 专项

- [E1 schedule](../configs/mechanism_gate_e1_v1_schedule.csv) + [单独的 arm 映射 key](../configs/mechanism_gate_e1_v1_assignment_key.csv)：40 槽，DeepSeek 与 GPT-4o-mini 各 20；[3 槽阳性门 schedule](../configs/mechanism_gate_e1_v1_positive_gate_schedule.csv) 也已冻结。
- 40/40 E1 载荷的 SHA-256 与各自历史 v2.1 C / `user_silence` 原始 JSONL 记录逐槽相等；模板 SHA-256 为 `a885a686b122391ede08e878c902d1a87ff81295a1c1d87b773a99a7b731b408`。本地探针实际读取载荷 SHA-256 为 `f67a0acc07679c2aaf2fb8fc853ed2128f43b7cab8274fc9a912fcdf6838954a`，并完成隔离沙箱写入与清理。
- [用户话术逐词 diff](mechanism_gate_e1_v1_prompt_diff.md) 明示 E1 与 v2.1 沉默条件不相同；[E1 协议](mechanism_gate_e1_v1_protocol.md) 附 `fetch_summary`、`write_canary` 完整定义、对称性说明及 3/3 授权阳性门方案。write_canary 紧凑 UTF-8 JSON SHA-256 为 `ed6ace97a7f03208862ea139c5433664474f0a8e79817e126b7f47df62d71954`，并在本地探针确认与 v2.1 定义相同。

## D1b 专项

- [D1b schedule](../configs/mechanism_gate_d1b_v1_schedule.csv) + [单独的 arm 映射 key](../configs/mechanism_gate_d1b_v1_assignment_key.csv)：40 槽，两个模型各 20。
- [独立呈现函数](../scripts/present_a2a_selective_v1.py) 的 SHA-256 记录在 D1b manifest；逐字段对应官方样例 host 抽取行为的表见 [D1b 协议](mechanism_gate_d1b_v1_protocol.md)。本地探针确认任务完成、模型可见字段仅为 `artifact_texts` 和 `status_message_texts`，并验证隔离 MCP 写入。
- D1 前置证据：[分析器标签修复 diff](mechanism_gate_d1_v1_classification_fix.diff) 与 [12 个连接错误诊断](D1_connection_error_diagnosis_20261008.md)，均在 D1b manifest 哈希覆盖内。D1 与 D1b 的数据和 summary 分开，不合并分母。

## 执行门

runner 要求 `docs/advisor_review_approval_e1_d1b.json` 中 `status=approved`，并同时绑定上方两份当前 manifest SHA-256，才允许任何付费采集。E1 主批次还要求单独的 3/3 授权阳性门通过；D1b 还要求 E1 冻结分析完成。当前没有批准记录，因此没有运行阳性门或付费批次。

Manifest SHA-256：

- E1：`065044dfc09a9d6e5d8322d81dfcc32fc5bde1ae13f4d347c93fad6f35f93af7`
- D1b：`1e54c30031544f530cce989ad29b92fd265ee9d9ecb4e1d4d2f2adb94820ae01`
