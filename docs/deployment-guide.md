# TutorBot 部署与运行说明

## 1. 推荐环境

- Windows 10/11 或主流 Linux
- Python 3.11 或 3.12；不建议复用其他 Python 版本创建的虚拟环境
- Node.js 20+
- 可选：ffmpeg、LaTeX、Manim，用于视频动画渲染

## 2. Windows 一键启动

```powershell
git checkout main-jsc
powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1 -BackendPort 8010 -FrontendPort 3010
```

访问 `http://localhost:3010/demo`。停止服务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop-dev.ps1
```

## 3. 首次安装

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
npm install
Copy-Item .env.example .env
```

不要提交 `.env`。若虚拟环境从其他 Python 版本复制而来，应删除并重新创建，避免 NumPy、Pillow、Jiter 等二进制扩展不兼容。

## 4. 模型配置

### 讯飞星火

```dotenv
TUTOR_LLM_PROVIDER=spark
TUTOR_LLM_MODEL=4.0Ultra
TUTOR_LLM_BASE_URL=https://spark-api-open.xf-yun.com/v1
TUTOR_LLM_API_KEY=<星火控制台 APIPassword>
```

### DeepSeek

```dotenv
TUTOR_LLM_PROVIDER=deepseek
TUTOR_LLM_MODEL=deepseek-chat
TUTOR_LLM_BASE_URL=https://api.deepseek.com
TUTOR_LLM_API_KEY=<本地密钥>
```

LLM 与 Embedding 独立配置。没有 Embedding 时固定比赛场景可运行，但知识库向量化和语义检索会降级。

## 5. 健康检查

```powershell
Invoke-RestMethod http://localhost:8010/api/v1/health
Invoke-RestMethod http://localhost:8010/api/v1/capabilities
Invoke-RestMethod http://localhost:3010/api/v1/demo/scenarios
```

能力列表应包含 `profile`、`resource_generation`、`path_planning`、`tutoring` 和 `assessment`。

## 6. 发布前验证

```powershell
.venv\Scripts\python.exe -m pytest -q
npm test --workspace frontend -- --run
npm run type-check --workspace frontend
npm run build --workspace frontend
```

比赛现场优先使用“加载演示数据”完成稳定演示，再使用“实时生成”展示真实任务 ID 和流式阶段。固定数据必须明确标注为演示快照。
