# 开源组件与协议说明

## 1. TutorBot 许可证

TutorBot 自主开发代码采用 MIT License，详见根目录 `LICENSE`。课程资料、演示数据和参赛文档由团队用于本项目展示；引用的论文和外部资料保留原作者署名与链接。

## 2. 主要直接依赖

| 组件 | 用途 | 来源 | 协议 |
|---|---|---|---|
| FastAPI | 后端 Web API | https://github.com/fastapi/fastapi | MIT |
| Next.js | 前端框架 | https://github.com/vercel/next.js | MIT |
| React | 前端 UI | https://github.com/facebook/react | MIT |
| Tailwind CSS | 样式系统 | https://github.com/tailwindlabs/tailwindcss | MIT |
| LlamaIndex | RAG 编排 | https://github.com/run-llama/llama_index | MIT |
| NetworkX | 知识图谱遍历 | https://github.com/networkx/networkx | BSD-3-Clause |
| Manim Community | 教学动画 | https://github.com/ManimCommunity/manim | MIT |
| Mermaid | 思维导图 | https://github.com/mermaid-js/mermaid | MIT |
| python-pptx | PPTX 生成 | https://github.com/scanny/python-pptx | MIT |
| jsPDF | 浏览器 PDF 导出 | https://github.com/parallax/jsPDF | MIT |
| PaperJSX JSON to PPTX | 参赛 PPTX 生成 | https://github.com/paperjsx/json-to-pptx | Apache-2.0 |

最终提交前使用 `pip-licenses` 与 npm lockfile 再生成一次完整依赖清单，并保留各依赖版本对应的许可证文本。

## 3. 架构参考

- DeepTutor：参考 Capability、Agent、Tool、StreamBus 和运行时注册设计。来源：https://github.com/HKUDS/DeepTutor ，Apache-2.0。
- ManimCat：参考“场景设计、代码生成、静态检查、失败重试”的思路。来源：https://github.com/MathInspector/ManimCat 。本项目不直接复制其资源或模型输出；提交前需再次确认上游当前许可证。

## 4. 使用规则

新增依赖时必须记录名称、版本、用途、来源和许可证。没有明确许可证的仓库只可作为思想参考，不直接复制代码、素材或文档。第三方课程资料必须确认授权范围并保留出处。
