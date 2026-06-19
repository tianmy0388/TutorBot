# Agent 与 Capability 清单

> 本文档列出 Tutor 系统中所有 Agent 与 Capability 的职责与协作关系。

## Capability 层（顶层编排）

| Capability | 职责 | 涉及的 Agent |
|---|---|---|
| `LearnerProfileCapability` | 对话式画像构建与更新 | 特征抽取、认知诊断、画像更新 |
| `ResourceGenerationCapability` | 多模态资源生成（核心） | 内容专家、教学设计、习题生成、多媒体、Manim 视频、代码沙箱、质量审核、防幻觉 |
| `PathPlanningCapability` | 学习路径规划与资源推送 | 路径规划、资源匹配 |
| `TutoringCapability` | 即时答疑（加分项） | 智能辅导 |
| `AssessmentCapability` | 学习效果评估（加分项） | 评估 Agent |

## Agent 层（领域智能体）

### 画像构建集群（3 个）

| Agent | 职责 | 关键实现 |
|---|---|---|
| FeatureExtractorAgent | 从对话中提取结构化特征 | LLM + JSON Schema 约束输出 |
| CognitiveDiagnosticAgent | 通过对话式探测评估知识掌握 | 基于 IRT 轻量模型 + LLM |
| ProfileUpdaterAgent | 随学随新，增量更新画像 | 差异检测：比较新旧特征 |

### 资源生成集群（7 个）

| Agent | 职责 | 输出 |
|---|---|---|
| ContentExpertAgent | 知识准确性，从 RAG 获取素材 | 初版内容 |
| PedagogyAgent | 怎么教：调整顺序、补充例子 | 教学版本 |
| ExerciseGeneratorAgent | 分层习题（基础/进阶/挑战） | 题库 |
| MultimediaAgent | 决定哪些内容做动画/思维导图 | Mermaid DSL + 表格 |
| ManimVideoAgent | 视频/动画生成 | Manim Python 代码 + 渲染 |
| CodeSandboxAgent | 代码实操案例 | 可运行代码 + 解释 |
| QualityReviewerAgent | 事实核查 + 教学合理性 + 难度匹配 | 审核通过的最终版 |

### 路径与辅导

| Agent | 职责 |
|---|---|
| PathPlannerAgent | 拓扑排序 + 剪枝 + 顺序推荐 |
| ResourceMatcherAgent | 为路径节点匹配已有资源 |
| TutorAgent | 即时答疑、多模态讲解 |
| AssessmentAgent | 多维度评估 + 推送策略调整 |

### 安全

| Agent | 职责 |
|---|---|
| AntiHallucinationAgent | 事实核查（RAG 比对）+ 逻辑一致性 + 内容安全 |

## 数据流：资源生成 Capability

```
用户请求 (topic, profile)
    │
    ▼
┌──────────────────┐
│ ResourceGeneration│
│ Capability        │
│ (Orchestrator)    │
└────────┬─────────┘
         │
         │ for each resource_type in plan:
         │
         ▼
   ┌──────────┐
   │ ContentExpert│ → 初版内容
   └────┬─────┘
        │
        ▼
   ┌──────────┐
   │ Pedagogy │ → 教学版本
   └────┬─────┘
        │
   ┌────┴─────────────┐
   │ (按资源类型分支)    │
   │ - Document        │
   │ - MindMap         │
   │ - Exercise        │
   │ - Reading         │
   │ - Video (Manim)   │
   │ - Code (Sandbox)  │
   └────┬─────────────┘
        │
        ▼
   ┌──────────────────┐
   │ AntiHallucination │ → confidence_score
   └────┬─────────────┘
        │
        ▼
   ┌──────────────────┐
   │ QualityReviewer   │ → 最终版 (passed / revise)
   └────┬─────────────┘
        │
        ▼
   Resource Package
```

## 多 Agent 协作模式

### 模式 1：串行生成（默认）
内容专家 → 教学设计 → 类型特化 Agent → 质量审核

### 模式 2：并行生成
内容专家 + 教学设计 并行后合并 → 类型特化 → 审核

### 模式 3：辩论审核（高价值内容）
生成 → 同行评审 + 学生视角双 Agent 辩论 → 仲裁

### 模式 4：人机协同（边界情况）
置信度低于阈值 → 标记 `needs_human_review` → 前端提示用户确认

## Prompt 管理

所有 Agent 的 prompt 放在 `backend/tutor/prompts/{module}/{lang}/{agent_name}.yaml`：

```
prompts/
├── en/
│   ├── profile/
│   │   ├── feature_extractor.yaml
│   │   ├── cognitive_diagnostic.yaml
│   │   └── profile_updater.yaml
│   ├── resource/
│   │   ├── content_expert.yaml
│   │   ├── pedagogy.yaml
│   │   ├── exercise_generator.yaml
│   │   ├── multimedia.yaml
│   │   ├── manim_video.yaml
│   │   └── code_sandbox.yaml
│   ├── path/
│   ├── tutor/
│   ├── assessment/
│   └── safety/
└── zh/
    └── (同上，中文版)
```

通过 `PromptManager` 加载，支持 `zh → cn → en` 回退链。

## 后续扩展

- **辩论 Agent**：对高价值内容启用"同行评审 vs 学生视角"双 Agent 辩论
- **元认知 Agent**：监控整个生成过程，动态调整 Agent 调度策略
- **记忆 Agent**：跨 session 记忆学生偏好与历史
