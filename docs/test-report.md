# TutorBot 测试说明与验收报告

## 1. 测试范围

测试覆盖学习状态、资源整理、学习路径、辅导、练习反馈、知识库摄取、任务持久化、断线恢复、事实核查、安全过滤和配置管理。

## 2. 测试环境

- Windows 11
- Python 3.13.12 本地验证环境；项目推荐 Python 3.11/3.12
- Node.js 20+
- 后端端口 8010，前端端口 3010

## 3. 验收命令

```powershell
.venv\Scripts\python.exe -m pytest -q
npm test --workspace frontend -- --run
npm run type-check --workspace frontend
npm run build --workspace frontend
```

## 4. 最新结果

最终发布前由 `main-jsc` 分支重新执行并填写：

| 项目 | 结果 | 备注 |
|---|---|---|
| 后端 pytest | 529 passed，4 skipped | 2026-07-16；覆盖单元、集成和 E2E |
| 前端 Vitest | 63 passed | 2026-07-16 |
| TypeScript | 通过 | `tsc --noEmit` |
| Next.js build | 通过 | 学习首页、工作台与资料库可静态构建 |
| 浏览器 smoke | 通过 | 加载五类资源、完成闭环小测；控制台 0 错误；live Job 返回 ID 并写入 109 条阶段事件 |

## 5. 重点测试案例

1. 对话与练习持续更新学习状态并持久化版本。
2. 真实任务生成多类资源并保留来源字段。
3. 长任务返回真实 Job ID，前端订阅阶段事件。
4. 代码沙箱正确处理中文输出和 Matplotlib 图片产物。
5. 知识库在进程重启后保留文档与状态。
6. 资源生成部分失败时保留成功工件并定向重试。
7. API 密钥只返回掩码，不进入 Git 提交。

## 6. 尚需补充的量化评测

正式答辩材料应增加至少 30 个问题的正确率、引用忠实度、未验证声明比例、平均/P95 延迟、失败率，以及“单智能体、无画像、无 RAG”基线对比。该部分属于效果评测，不以单元测试替代。

### 6.1 《计算机网络》RAG 检索评测

已补充脚本：

```powershell
.venv\Scripts\python scripts\evaluate_computer_network_rag.py
```

如需生成 Markdown 报告：

```powershell
.venv\Scripts\python scripts\evaluate_computer_network_rag.py --write docs\rag-evaluation-computer-network.md
```

该脚本覆盖 30 个《计算机网络》问题，统计课程知识库 Top-K 预期文档命中率、Citation 覆盖率、平均延迟和 P95 延迟。它只评估检索阶段；正式答辩仍建议人工抽查最终回答的正确率、引用忠实度和未验证声明比例。

当前《计算机网络》课程库已扩展为 13 份 Markdown 课程材料，并补充 80 题综合题库；最新评测报告见 `docs/rag-evaluation-computer-network.md`。本地 `local-hash-v1` 演示检索采用向量相似度 + 词面重排的混合策略，以保证无云端 Embedding 密钥时仍能稳定展示课程 citation。
