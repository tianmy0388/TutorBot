# 知识库说明

> 本文档描述 Tutor 系统的初始课程知识库结构与内容组织。

## 设计原则

1. **每门课程一个独立目录**，包含元数据、知识图谱、原始讲义、内置题库
2. **知识图谱用 YAML 静态定义**，MVP 阶段不依赖 Neo4j
3. **原始讲义用 Markdown**，便于 RAG 索引与人类维护
4. **结构化题目用 JSON**，便于程序读取与渲染

## 目录结构

```
knowledge_base/
├── ai_introduction/                   # 课程：人工智能导论
│   ├── metadata.json                  # 课程元数据
│   ├── knowledge_graph.yaml           # 知识图谱定义
│   ├── raw/                           # 原始讲义（Markdown）
│   │   ├── 01_什么是人工智能.md
│   │   ├── 02_机器学习基础.md
│   │   ├── 03_神经网络与深度学习.md
│   │   ├── 04_CNN与计算机视觉.md
│   │   ├── 05_RNN与序列建模.md
│   │   ├── 06_Transformer与注意力机制.md
│   │   └── 07_LLM与大语言模型.md
│   └── questions/                     # 内置题库（JSON）
│       └── 03_神经网络.json
└── (后续可加：computer_vision/, nlp/, ...)
```

## metadata.json

```json
{
  "name": "ai_introduction",
  "display_name": "人工智能导论",
  "description": "面向本科生的 AI 入门课程...",
  "level": "undergraduate",
  "language": "zh",
  "created_at": "2026-06-19T00:00:00Z",
  "updated_at": "2026-06-19T00:00:00Z",
  "version": "1.0.0",
  "tags": ["AI", "深度学习", "机器学习"],
  "rag_provider": "llamaindex",
  "embedding_model": "text-embedding-3-small"
}
```

## knowledge_graph.yaml

```yaml
course: ai_introduction
nodes:
  - id: ml_basics
    name: 机器学习基础
    category: machine_learning
    difficulty: 2
    prerequisites: []
    estimated_hours: 4
    learning_outcomes:
      - 理解监督/无监督/强化学习的区别
      - 能用 sklearn 训练简单模型
  
  - id: neural_network
    name: 神经网络与深度学习
    category: deep_learning
    difficulty: 3
    prerequisites: [ml_basics]
    estimated_hours: 8
    learning_outcomes:
      - 理解前馈神经网络结构
      - 能手写反向传播算法
  
  # ... (完整图谱见实际文件)

edges:
  - from: ml_basics
    to: neural_network
  - from: neural_network
    to: cnn
  - from: neural_network
    to: rnn
  # ...

learning_paths:
  - id: cv_path
    name: 计算机视觉方向
    sequence: [ml_basics, neural_network, cnn, transformer, llm]
  - id: nlp_path
    name: 自然语言处理方向
    sequence: [ml_basics, neural_network, rnn, transformer, llm]
```

## RAG 索引

启动时自动：
1. 扫描 `knowledge_base/*/raw/*.md`
2. 用 LlamaIndex 加载并分块（默认 512 token / 50 overlap）
3. 用 Embedding 模型向量化
4. 持久化到 `knowledge_base/<kb>/llamaindex_storage/`

运行时检索：
- 资源生成时，自动用 topic + profile 查询相关片段
- 通过 `stream.sources()` 把引用来源推送到前端

## 内置题库

`questions/` 下的 JSON 文件定义结构化题目：

```json
{
  "topic": "神经网络与深度学习",
  "questions": [
    {
      "id": "nn-001",
      "type": "single_choice",
      "difficulty": 2,
      "question": "反向传播算法中，链式法则的核心作用是？",
      "options": ["A. ...",
                  "B. ...",
                  "C. ...",
                  "D. ..."],
      "answer": "B",
      "explanation": "..."
    }
  ]
}
```

支持的题型：`single_choice`, `multiple_choice`, `true_false`, `fill_blank`, `short_answer`, `code`。

## 添加新课程

1. 在 `knowledge_base/` 下新建课程目录
2. 写 `metadata.json`
3. 写 `knowledge_graph.yaml`
4. 在 `raw/` 下放 Markdown 讲义
5. （可选）在 `questions/` 下放 JSON 题库
6. 重启服务，系统自动发现并初始化

## 后续扩展

- **多模态素材**：PDF 课件、视频字幕、图片
- **自动化讲义生成**：从公开教材/PDF 自动抽取
- **学生贡献**：支持学生上传笔记、纠错
- **跨课程知识图谱**：课程间的先修关系
