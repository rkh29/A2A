# BC-2 采集前修订记录

**日期：** 2026-10-08（Asia/Shanghai）  
**阶段：** R1 采集前；尚未发出模型 API 请求。  
**目的：** 记录环境与当前源码之间的兼容问题及其在盲态下的处理，不依据任何实验结果修改方案。

## 发现

1. 新建 `.venv-bc2` 后，Python 为 3.12.14，顶层 SDK 精确为 `a2a-sdk==1.2.1`、`mcp==2.3.0`、`openai==3.24.0`；全量 51 行 `pip freeze` 在 `configs/mechanism_gate_bc2_v1_pip_freeze.txt`。
2. Git 提交固定冻结输入的 LF 行尾，避免 Windows checkout 转换行尾后破坏 manifest 对原始文件字节的 SHA-256 校验。
3. 当前 `scripts/run_api_chain.py` 在 `a2a-sdk==1.2.1` 下不能导入：它读取旧接口 `a2a.types.TextPart`、`TaskState.completed`、`Role.user`。v2.1 JSONL 没有 runner 哈希；当前共享 helper 修改时间晚于 v2.1 批次，故当前文件不能视作已验证的历史源码。
4. 当前 `scripts/run_mechanism_gate_v2_1.py` 校验 schedule 必须有 160 行和 8 个模型×格组合，并会逐条打印 ASR。BC-2 冻结设计为 DeepSeek B/C 两格共 40 个主槽，且要求采集期盲态输出。因此当前 v2.1 CLI 不能无改动执行该设计。

## 处理

- 不修改 `scripts/run_mechanism_gate_v2_1.py` 或共享 `scripts/run_api_chain.py`。
- 在独立的 `scripts/run_mechanism_gate_bc2_v1.py` 中加入 R1 专用 A2A v0.3 fixture 兼容桥，并继续调用 v2.1 的 `build_payload`、`build_tool_schema`、host prompt、tool schema 和 `base.run_trial` 模型/工具调用循环。桥只适配本地 A2A fixture 的类型与 JSON-RPC 边界；不改用户话术、Artifact 正文、B/C metadata 操作、模型参数、工具定义或评分逻辑。
- 保持 40 槽随机区组 schedule、opaque arm 映射、预冻结 reserve、进度级盲态输出及原分析规则。新 runner 与当前 v2.1 runner 的逐行差异在 `docs/mechanism_gate_bc2_v1_runner.diff`。
- 顶层 SDK 版本与 Day 0 记录一致；Day 0 没有同期全量 freeze，因此只声称顶层版本匹配。历史 v2.1 A2A helper 的具体代码身份仍不可验证，跨批次解释中列为限制。

## 免 API 兼容检查

使用 `.venv-bc2` 在非受限本地子进程执行 A2A v0.3 JSON-RPC fixture 往返，无模型 API 调用。结果：`a2a_ok=true`，状态为 `TASK_STATE_COMPLETED`，Artifact 正文完全相等，B 条件 `approval_claim` metadata 完整往返，收到 1 个 Task 事件。受限子进程中的 asyncio socketpair 被 Windows sandbox 阻断，因此本地检查按流程在非受限子进程完成。

## 冻结与执行门

此修订及 runner/config/protocol/schedule/analysis/pip-freeze 的 SHA-256、完整 Git 提交时间锚写入 freeze manifest 后，manifest 嵌入 `pip freeze` 全文并校验 Python/包版本；先运行 runner dry-run 和 MCP 无 API 写入/越界探针。通过后由同一 R1 兼容桥运行冻结的 `bc2-r1-positive-01` 至 `bc2-r1-positive-03` 三个授权阳性门，均成功写入各自沙箱 canary 后才开始 40 槽主批次。阳性门任一失败即停止，不启动主批次；阳性门使用独立路径且不占用盲态 schedule。
