# TutorBot 比赛演示指南

本指南面向“软件杯 A3：基于大模型的个性化资源生成与学习多智能体系统开发”现场演示。目标是在 5 到 8 分钟内完整展示 TutorBot 的多智能体协作、个性化学习闭环、可信资源证据和报告导出能力。

## 1. 一键启动

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1 -BackendPort 8000 -FrontendPort 3010
```

如果 8000 端口被占用，可以改用：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1 -BackendPort 8010 -FrontendPort 3010
```

启动后访问：

- 前端演示页：http://localhost:3010/demo
- 后端健康检查：http://localhost:8000/api/v1/health

停止服务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop-dev.ps1
```

## 2. 环境配置

首次启动会从 `.env.example` 创建 `.env`。请只在本地 `.env` 填写真实密钥，不要提交 `.env`。

推荐配置：

- `LLM_PROVIDER=deepseek`
- `LLM_API_KEY=你的 DeepSeek key`
- `LLM_MODEL=deepseek-chat`
- Embedding provider 和 key 单独配置，DeepSeek 只作为 LLM provider 使用。

没有 Embedding key 时，演示页仍可加载内置演示数据；实时生成、知识库检索和向量化能力会显示降级提示。

## 3. 推荐演示流程

1. 打开 `http://localhost:3010/demo`。
2. 在场景选择中保留默认“AI 入门学习”。
3. 点击“加载演示数据”，先展示稳定的比赛演示样例。
4. 讲解顶部指标：学习目标、诊断结论、资源数量、测评分数和运行提示。
5. 展示多智能体时间线：画像分析、资源生成、路径规划、辅导、测评和教师干预建议。
6. 展示学习闭环：目标 -> 诊断 -> 学生画像 -> 资源推荐 -> 练习/测评 -> 下一步建议。
7. 展示资源可信度：引用来源、审查结论、安全提示、未核实声明、置信度和生成 agent。
8. 展示教师演示面板：当前学生画像、学习进度、薄弱点和推荐干预。
9. 点击 Markdown 导出或 PDF 导出，说明可沉淀为学习报告。
10. 如果现场网络和密钥可用，再点击“实时生成”展示真实 LLM 调用路径。

## 4. 讲解重点

- TutorBot 不是单一聊天助手，而是围绕学习任务编排多个 agent。
- 系统把学生目标、基础、偏好、时间约束和薄弱点转成可执行学习路径。
- 每个资源都尽量展示证据字段，避免“只生成内容、不说明来源和风险”。
- 测评结果会回流到下一步建议，形成可演示的学习闭环。
- 教师视角先做轻量演示面板，便于说明后续可扩展到班级管理和干预策略。

## 5. 常见问题

### DeepSeek key 已配置，但 Embedding 仍提示缺失

这是预期行为。DeepSeek 在本项目中只作为 LLM provider；Embedding 需要单独配置 provider 和 key。

### `/demo` 能加载，但实时生成降级

检查 `.env` 中的 LLM 和 Embedding 配置，并打开“设置”页运行连接测试。没有 Embedding 时不会阻止项目启动，但知识库检索能力会降级。

### PDF 导出失败

确认前端依赖已安装，并使用浏览器访问页面。PDF 导出依赖 `html2canvas` 和 `jspdf`，Markdown 导出可作为备用。
