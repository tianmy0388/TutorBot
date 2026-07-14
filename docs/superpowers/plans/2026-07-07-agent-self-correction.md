# Agent 自纠正架构设计（4 层 + 实施计划）

> 用户问题："Agent 应该可以进行自我纠正，遇到错误的反馈应该可以再次调用工具来纠正的能力吧？"
>
> 上下文：本次会话观察到 4 类典型 agent 失败模式 —
> 1. **JSON 截断**：LLM 在 `code` 字段中段被 max_tokens 切断，触发 salvage 路径
> 2. **空 JSON**：`response_format=json_object` 没生效 / LLM 直接返 prose，触发 `parse_json_response` 兜底 `{}`
> 3. **视频生成失败**：Manim 故事板空 / 代码语法错，agent 返回 `render_status="failed"` 占位
> 4. **代码执行失败**：LLM 生成的代码有 bug，subprocess 返回非 0，但 agent 把 `error_code` 暴露给前端

本文档把这 4 类失败按"距离 agent 多近"分成 4 层自纠正机制，逐一分析成本/收益，给出推荐实施顺序。

---

## 四层自纠正机制

### L1：解析失败 → 同 prompt 重试 + 错误反馈

**机制**：当 `parse_json_response(content)` 失败（截断、空 JSON、syntax error），把上一次原始 content 附在 prompt 末尾，让 LLM 重新生成并补全。

```
[attempt 1] prompt: "返回 JSON {...}"
            resp.content = '{ "title": "x", "code": "import math\ndef s'  # 截断
            parse → fail
[attempt 2] prompt: "返回 JSON {...}\n\n你上次的输出被截断了：\n{ "title": "x", "code": "import math\ndef s\n\n请补全到合法 JSON。"
            resp.content = '{ "title": "x", "code": "import math\ndef sigmoid(z):\n  ..."}'  # OK
            parse → OK
```

**对应失败模式**：JSON 截断、空 JSON

**实施成本**：⭐（最低，~30 行 base_agent + 4 个 agent 切换入口）

**收益**：⭐⭐⭐⭐（直接解决本次会话 2 个 bug：code_sandbox 截断、pedagogy 截断）

**风险**：低。增加 1 次 LLM 调用（截断场景罕见，平均延迟影响小）。

**代码骨架**（已部分存在于 manim_video.py:217 的 `finish_reason=="length"` 检查）：

```python
# tutor/agents/base_agent.py
class BaseAgent:
    async def call_llm_with_retry(self, *, messages, max_tokens=None, ...):
        for attempt in range(3):  # 1 original + 2 retries
            resp = await self.call_llm(messages=messages, max_tokens=max_tokens, ...)
            data = self.parse_json_response(resp.content, fallback=None)
            if data is not None and self._looks_complete(data):
                return resp, data, attempt  # 成功
            # 失败：追加错误反馈重试
            messages = messages + [LLMMessage(
                role="user",
                content=f"你上次的输出无法解析为有效 JSON：\n\n```\n{resp.content[:1500]}\n```\n\n"
                        f"错误：{...}\n\n请重新生成完整的 JSON。"
            )]
        return resp, None, 3  # 最后一次失败
```

---

### L2：max_tokens 截断 → 2x 预算重试

**机制**：当 `resp.finish_reason == "length"`，把 `max_tokens` × 2 重试一次。同样的 prompt，更长的预算。

```
[attempt 1] max_tokens=4096
            resp.finish_reason = "length"
            resp.content = '{ "title": "...", "sections": [..., last 3 truncated]'
            len(content) ≈ 4096 * 4 chars = 16k chars
[attempt 2] max_tokens=8192
            resp.finish_reason = "stop"
            resp.content = complete JSON
```

**对应失败模式**：所有 max_tokens 截断（pedagogy 7 sections、manim_video 长代码、code_sandbox 长示例）

**实施成本**：⭐（最低，~20 行 base_agent）

**收益**：⭐⭐⭐⭐⭐（对长输出 agent 必装）

**风险**：极低。退避式 token 增长（4096 → 8192 → 16384），最多 3 次。LLM 速度与 max_tokens 大致线性，最坏情况 3x 延迟。

**代码骨架**：

```python
class BaseAgent:
    async def call_llm_with_retry(self, *, messages, max_tokens=None, ...):
        if max_tokens is None:
            max_tokens = self.default_max_tokens
        for attempt, mt in enumerate([max_tokens, max_tokens * 2, max_tokens * 4]):
            resp = await self.call_llm(messages=messages, max_tokens=mt, ...)
            if resp.finish_reason != "length":
                return resp
            logger.warning(
                f"{self.agent_name}: max_tokens={mt} hit length cap; "
                f"retrying with 2x (attempt {attempt+1}/3)"
            )
        return resp  # 最后一次也截断，调用方走 salvage
```

---

### L3：质量自评（Self-Critique）

**机制**：agent 生成 Resource 后**额外跑一次** LLM critique pass：
- "这份代码完整吗？缺 print 输出吗？"
- "这份视频脚本能在 Manim CE v0.20 跑起来吗？"
- "这份习题答案正确吗？"

如果 critique 给出 "incomplete / incorrect"，进入 L1/L2 风格的 retry loop 重生。

**对应失败模式**：所有内容质量问题（代码逻辑错、视频代码语法错、习题答案错）

**实施成本**：⭐⭐⭐⭐（高，每个 agent 都要写 critique prompt + 解析 critique 输出 + 决定 retry 阈值）

**收益**：⭐⭐⭐（质量更好但实现复杂，且与外部 `quality_reviewer` 职能重叠）

**风险**：中。
- Critique LLM 也可能错（false negative: critique 说 "OK" 但实际有 bug）
- 增加 ~50% LLM 调用成本
- 难以判断"重试多少次放弃"

**实施建议**：L3 应该**取代外部 quality_reviewer**（而不是并存），把 review pass 内化到每个 agent。如果保留外部 review，则 L3 收益会被稀释。

**推荐优先级**：🔻 低。先做 L1+L2，等稳定后考虑。

---

### L4：工具调用重试

**机制**：Tool 失败时（HTTP 5xx、网络超时、空结果），自动换参数/换工具重试：
- RAG 检索超时 → 重试 1 次，第二次失败换 web_search 兜底
- Code execution 超时 → 减少 timeout 重试 1 次
- Web search 5xx → 换 endpoint 重试

```
[attempt 1] rag_tool.search(query) → TimeoutError
[attempt 2] rag_tool.search(query, top_k=10) → TimeoutError  # 加更长 timeout
[attempt 3] web_search_tool.search(query) → 3 results
```

**对应失败模式**：Tool 瞬时失败（网络抖动、LLM rate limit）、Tool 返回空（query 表达不当）

**实施成本**：⭐⭐⭐（中。需要给 BaseTool 加 retry 装饰器 + 选择"换什么"的策略）

**收益**：⭐⭐⭐（减少瞬时失败的影响面）

**风险**：中。
- 无限重试把 Tool 调用延迟放大
- 不同 Tool 的重试策略不一样（RAG 重试无意义但 web_search 重试有意义）

**代码骨架**：

```python
# tutor/core/tool_protocol.py
class BaseTool:
    retry_policy: RetryPolicy = RetryPolicy(max_attempts=2, backoff="linear")
    
    async def execute(self, **kwargs) -> ToolResult:
        last_exc = None
        for attempt in range(self.retry_policy.max_attempts):
            try:
                return await self._execute(**kwargs)
            except TransientError as exc:
                last_exc = exc
                await asyncio.sleep(attempt + 1)  # linear backoff
            except PermanentError:
                raise  # don't retry permanent
        return ToolResult(success=False, error=str(last_exc))
```

**实施建议**：先只给 `web_search_tool`、`rag_tool` 加（瞬时失败多），`code_execution_tool` 暂时不加（用户期望确定性失败而非重试到成功）。

---

## 四层对比表

| 维度 | L1 解析重试 | L2 截断重试 | L3 自评 | L4 工具重试 |
|---|---|---|---|---|
| **解决本次会话的 bug** | code 截断 / pedagogy 截断 / manim 截断 | 同 L1 | 无直接对应 | 无直接对应 |
| **实施行数（粗估）** | 30 行 base_agent | 20 行 base_agent | 200 行（每 agent 一套） | 80 行（BaseTool + 2 个 tool） |
| **风险** | 低（多 1 次 LLM 调用） | 极低（线性退避） | 中（critique 错判） | 中（延迟放大） |
| **延迟影响** | +1× LLM（仅截断时） | +1×~2× LLM | +50% LLM（每次都跑） | +1~3s backoff |
| **实施顺序** | 1️⃣ 先做 | 2️⃣ 后做 | 4️⃣ 最后 | 3️⃣ 中期 |

---

## 推荐实施路线

### Phase 1（~1 天）— L1 + L2 合并

**单一改动点**：`tutor/agents/base_agent.py` 新增 `call_llm_with_retry()` 方法。

逻辑：
```python
async def call_llm_with_retry(
    self, *, messages, max_tokens=None, max_attempts=3, ...
) -> tuple[LLMResponse, Any, int]:
    """Returns (resp, parsed_data_or_None, attempts_used)."""
    if max_tokens is None:
        max_tokens = self.default_max_tokens
    
    for attempt in range(max_attempts):
        # 退避策略：第 1 次 max_tokens；第 2 次 2x；第 3 次 4x
        mt = max_tokens * (2 ** attempt)
        resp = await self.call_llm(messages=messages, max_tokens=mt, ...)
        
        # L2 早返回：如果 finish_reason 不是 length，说明没截断，跳过 L1 重试
        if resp.finish_reason != "length":
            data = self.parse_json_response(resp.content, fallback=None)
            if data is not None and self._looks_complete(data):
                return resp, data, attempt + 1
        
        # L1 反馈：把上一次失败的内容附在 prompt 末尾
        if attempt < max_attempts - 1:
            messages = messages + [LLMMessage(
                role="user",
                content=self._retry_feedback(resp.content, resp.finish_reason)
            )]
            logger.warning(
                f"{self.agent_name}: attempt {attempt+1} failed "
                f"(finish_reason={resp.finish_reason}); retrying"
            )
    
    return resp, self.parse_json_response(resp.content, fallback={}), max_attempts
```

**接入点**（4 个 agent 改 ~3 行）：

| Agent | 当前调用 | 改为 |
|---|---|---|
| `code_sandbox.py:113` | `await self.call_llm(...)` | `resp, data, _ = await self.call_llm_with_retry(messages=...)` |
| `manim_video.py:99,143` | `_stage_design` + `_stage_codegen` | 同上 |
| `pedagogy.py:109` | `await self.call_llm(...)` | 同上 |
| `content_expert.py:~107` | `await self.call_llm(...)` | 同上 |

**测试覆盖**（3 个新测试）：
- `test_retry_on_truncated_json`：mock LLM 第一次返截断，第二次返完整 → assert data 正确、attempts=2
- `test_retry_max_tokens_doubles`：mock 第一次返 finish_reason=length → assert 第二次 max_tokens=2x
- `test_retry_gives_up_after_3_attempts`：mock 持续返空 JSON → assert attempts=3, data={}

**预期效果**：本次会话观察到的 4 类失败中至少 3 类（code 截断、pedagogy 截断、manim 空代码）大幅减少。

---

### Phase 2（~半天）— L4 部分接入

只给 `web_search_tool` 和 `rag_tool` 加 retry 装饰器：

```python
# tutor/tools/rag_tool.py
class RAGTool(BaseTool):
    retry_policy = RetryPolicy(max_attempts=2, backoff="linear", on=TimeoutError)
```

不动 `code_execution_tool`（用户期望明确失败而非重试）。

---

### Phase 3（~1 天）— L3 评估 + 设计

**先做实验**，不改代码：

1. 选一个 agent（如 `code_sandbox`）加 critique prompt：
   ```
   "你刚生成了这份代码：[code]
   请评审：
   1. 代码能完整跑通吗（语法 + 逻辑）？
   2. 关键概念都覆盖了吗？
   3. 有没有教学价值？
   输出版本号：1.0=OK, 0.5=需要补全, 0=重写"
   ```

2. 跑 20 个 topic，对比"有 critique 重试" vs "无 critique"的输出质量。

3. 如果 critique retry 把代码运行成功率从 70% 提到 90%+，再考虑全面接入。

**不推荐一开始就全量上 L3**：成本太高，critique 错判概率不可忽视。

---

## 风险与未知

### 风险 1：retry 放大延迟

如果 pedagogy 第一次 max_tokens=4096 + 30s LLM 调用，retry 1 → 8192 + 60s LLM 调用，retry 2 → 16384 + 120s LLM 调用。**最坏 3.5 分钟**。

**缓解**：
- max_attempts 默认 2（不是 3）
- 给 retry 加超时：单次 LLM 调用超时 90s → 整体重试窗口 < 3 分钟
- 把 `max_attempts` 配到 settings，单 agent 可调

### 风险 2：retry 让失败模式变隐蔽

之前 truncation 一发生就立刻看到 fallback resource，重试可能让用户多等 1-2 分钟看到的是同样的 fallback。

**缓解**：
- retry 期间发 stream observation："正在重试 (attempt 2/3, max_tokens=8192)..."
- trace panel 显示 retry attempts

### 风险 3：LLM 重试时 prompt 越来越长

每次 retry 把上一次 content 附在末尾，3 次 retry 后 prompt 可能 5x 长度，进 context。

**缓解**：
- 只附"截断的位置提示"（"你的 JSON 在 `sections[3].content` 处被截断"），不附完整 content
- 或者只附 content 的尾部 500 chars（最可能错的位置）

---

## 与现有 quality_reviewer 的关系

**当前架构**：
```
agent.process() → Resource
                ↓
external quality_reviewer.process(resource) → ResourceReview (verdict)
                ↓
capability filters verdict=reject
```

**建议**：
- L1+L2（解析+截断）应**保留在 agent 内**，因为它们修的是"输出没生成出来"，reviewer 看到的是空壳
- L3（内容质量）应**取代** quality_reviewer 的某些职能（如代码正确性、视频可渲染性），而不是并存
- L4（工具重试）和 reviewer 无关，是 agent 调用 tool 的内层循环

具体说：
- L1+L2 在 `BaseAgent.call_llm_with_retry()` —— 全 agent 自动获益
- L3 在 agent.process() 末尾加 critique pass —— 选 2-3 个 agent 实验
- 现有 quality_reviewer 聚焦**教学价值 / 安全 / 教学风格**这类 LLM 输出是否"对人友好"的问题，不重复做语法/逻辑检查

---

## 决策点

请告诉我你想：

- [ ] **A. 先实施 Phase 1（L1+L2）**：预计 1 天 + 3 个测试，立即看到代码/视频 bug 大幅减少
- [ ] **B. 顺序全部走完（Phase 1 → 2 → 3）**：3 天工作量，全套自纠正
- [ ] **C. 先做 Phase 1，然后停下来评估**（推荐）：先看 L1+L2 效果，再决定 L3/L4
- [ ] **D. 不实施**，现状可接受：本次会话 bug 都修了，不需要额外基础设施

我建议 **C**。

---

最后修改：2026-07-07