# 第 7 章：LLM 与大语言模型

## 7.1 什么是 LLM

**大语言模型（Large Language Model, LLM）** 是在大规模文本语料上预训练的 Transformer 语言模型，参数规模从十亿到千亿级。

代表性 LLM：
- **GPT 系列**（OpenAI）：GPT-3, GPT-4, GPT-4o
- **Claude 系列**（Anthropic）：Claude 3 / 3.5 / 4
- **Gemini**（Google）
- **Llama 系列**（Meta，开源）
- **Qwen**（阿里）、**DeepSeek**、**GLM**（智谱）、**文心**（百度）

## 7.2 预训练范式

### 7.2.1 因果语言建模（Causal LM）

给定前文预测下一个 token（GPT 风格）：

$$P(x_1, x_2, \ldots, x_n) = \prod_{t=1}^n P(x_t | x_1, \ldots, x_{t-1})$$

训练目标：最大化语料的似然。

### 7.2.2 掩码语言建模（Masked LM）

BERT 风格：随机 mask 一些 token，预测它们。

## 7.3 三大架构家族

| 架构 | 代表 | 特点 |
|---|---|---|
| **Encoder-Only** | BERT | 双向理解，适合分类/检索 |
| **Decoder-Only** | GPT, Llama | 自回归生成，通用 |
| **Encoder-Decoder** | T5, BART | 翻译/摘要等 Seq2Seq |

## 7.4 缩放法则（Scaling Laws）

Kaplan et al. (2020) 发现的规律：

$$L(N, D) = \left(\frac{N_c}{N}\right)^{\alpha_N} + \left(\frac{D_c}{D}\right)^{\alpha_D} + L_\infty$$

- 模型越大、数据越多 → 损失越低
- 但收益递减 — 必须同步扩展

## 7.5 涌现能力（Emergent Abilities）

模型规模超过某个阈值后突然出现的能力：
- In-Context Learning
- Chain-of-Thought Reasoning
- Instruction Following

但近年研究（2023-）对此有争议，认为很多所谓"涌现"是评估指标选择造成的假象。

## 7.6 对齐（Alignment）

### 7.6.1 指令微调（SFT）

用人类标注的"指令-回答"对微调基础模型。

### 7.6.2 RLHF（人类反馈强化学习）

```
Pretrained LM → SFT → Reward Model → PPO 优化 → Aligned LM
```

三步：
1. 收集人类偏好数据（A vs B）
2. 训练奖励模型（Reward Model）
3. 用 PPO 优化 LM，最大化奖励

### 7.6.3 DPO / RLAIF / Constitutional AI

RLHF 的替代：直接偏好优化（DPO）、AI 反馈强化学习（RLAIF）、宪法 AI（Claude）。

## 7.7 Prompt Engineering

### 7.7.1 基础技巧

- 角色设定："你是一位资深 Python 工程师"
- Few-shot：给几个例子
- 思维链（CoT）："请一步步思考"
- 自一致性：多次采样投票

### 7.7.2 进阶技巧

- ReAct：Reasoning + Acting
- Tree of Thoughts：搜索式推理
- Self-Refine：生成 → 批评 → 改进

## 7.8 RAG（检索增强生成）

解决 LLM 幻觉、知识陈旧、私域知识问题：

```
用户问题 → 检索相关文档 → 拼成 Prompt → LLM 生成回答
```

主流框架：LlamaIndex、LangChain。

## 7.9 Function Calling / Tool Use

LLM 输出结构化调用：

```json
{
  "name": "get_weather",
  "arguments": {"city": "Beijing"}
}
```

Agent 框架让 LLM 自主决定调用哪些工具。

## 7.10 LLM 应用开发模式

```
┌─────────────────────────────────────────────┐
│              LLM Application                 │
├─────────────────────────────────────────────┤
│  Prompt 模板 │ RAG │ Memory │ Tools          │
├─────────────────────────────────────────────┤
│         LLM Provider (API / Local)            │
└─────────────────────────────────────────────┘
```

经典框架：
- **LangChain / LangGraph**：最广泛
- **LlamaIndex**：RAG 强
- **AutoGen / CrewAI**：多 Agent

## 7.11 LLM 评估

| 维度 | 指标 |
|---|---|
| 能力 | MMLU, BBH, GSM8K, HumanEval |
| 安全 | Toxicity, Bias, Jailbreak resistance |
| 实用性 | 用户满意度、人工评估 |
| 效率 | Tokens/s, 时延, 成本 |

## 7.12 未来方向

- **更长上下文**：百万 token 级
- **多模态**：原生支持图像、视频、音频
- **Agent**：自主规划与执行
- **高效推理**：MoE、量化、Speculative Decoding
- **端侧部署**：手机/笔记本本地运行

## 本章小结

- LLM 是预训练的 Transformer Decoder，用海量文本 + RLHF 对齐
- Prompt Engineering 和 RAG 是当前应用主流
- 多智能体系统（Agent）正在成为新的范式 — 这正是 Tutor 的方向！

## 思考题

1. LLM 为什么会出现幻觉？如何缓解？
2. RLHF 和 SFT 的本质区别是什么？
3. 请设计一个基于 LLM 的多智能体学习系统（Tutor 就是答案 😊）
