# TutorBot 测试说明与验收报告

## 1. 测试范围

测试覆盖画像、资源生成、学习路径、辅导、评测、知识库摄取、任务持久化、断线恢复、事实核查、安全过滤、配置管理和比赛演示页。

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
| Next.js build | 通过 | 包含 `/demo` 路由 |
| 浏览器 smoke | 通过 | 加载五类资源、完成闭环小测；控制台 0 错误；live Job 返回 ID 并写入 109 条阶段事件 |

## 5. 重点测试案例

1. 画像对话连续更新六个维度并持久化版本。
2. 固定演示返回不少于五类资源及证据字段。
3. live 模式返回真实 Job ID，前端订阅阶段事件。
4. 代码沙箱正确处理中文输出和 Matplotlib 图片产物。
5. 知识库在进程重启后保留文档与状态。
6. 资源生成部分失败时保留成功工件并定向重试。
7. API 密钥只返回掩码，不进入 Git 提交。

## 6. 尚需补充的量化评测

正式答辩材料应增加至少 30 个问题的正确率、引用忠实度、未验证声明比例、平均/P95 延迟、失败率，以及“单智能体、无画像、无 RAG”基线对比。该部分属于效果评测，不以单元测试替代。
