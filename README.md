# FrameFlow：AI 商业广告视频生成系统

FrameFlow 是依据《AI商业视频自动生成系统技术设计文档》实现的可运行 MVP。它不是单页效果稿，而是一套包含 React 创作工作台、FastAPI 业务接口、持久化项目数据、图片素材上传、结构化 AI 分镜、异步渲染任务和视频供应商抽象的完整应用骨架。

当前默认使用 `demo` Provider，因此不配置任何付费密钥也能走通“创作 Brief → 素材上传 → AI 导演分镜 → 异步生成 → 成片预览”的全部流程。演示模式不会伪装成真实生成的视频文件；成片区域明确显示交互预览。配置官方 API 后，Provider 会返回视频并由后端归档为自有 MP4。

面向中国大陆客户的默认真实生成线路是 MiniMax 海螺官方开放平台，不要求本地 GPU，也不经过海外聚合网关。普通镜头使用 Hailuo 2.3 Fast，只有明确标记为高质量的复杂镜头才使用 Hailuo 2.3。详细成本、配置和非模板化叙事原则见 [国内官方 API 策略](docs/LOW_COST_API_STRATEGY.md)。

## 已实现的产品能力

- 商业 Brief：平台、时长、画幅、风格、受众、卖点与创作要求。
- 分类素材库：商品、人物、场景、品牌四类参考图，限制文件类型与大小。
- AI 导演：输出营销主题、情绪曲线、音乐方向、逐镜头画面、摄影、动作、旁白、屏幕文字和模型 Prompt。
- 生成流水线：任务排队、阶段化进度、失败回传、重复任务保护和完成状态。
- 成片工作台：竖屏/横屏画幅、逐镜头预览、时间线、播放控制和导出入口。
- 可插拔 Provider：业务层只依赖统一接口，不绑定单一视频平台。
- 本地持久化：MVP 使用加锁 JSON Store；接口边界保留，可直接替换 PostgreSQL。
- 工程验证：包含 API 契约测试、Playwright 端到端测试和生产构建检查。

## 系统结构

```text
React Studio
    │
    ├── Project / Asset API
    ├── Creative Plan API ──> Director Contract
    └── Render API ─────────> Background Orchestrator
                                  │
                                  ├── Demo Provider
                                  ├── MiniMax Official Provider
                                  │     ├── Hailuo 2.3 Fast
                                  │     └── Hailuo 2.3 Quality
                                  └── Owned Object Storage
```

主要目录：

```text
backend/app/main.py       HTTP 接口、上传和任务编排
backend/app/director.py   结构化导演输出；未来 LLM 的目标 Schema
backend/app/providers.py  视频供应商统一接口
backend/app/store.py      MVP 持久化层
frontend/src/App.tsx      四步创作工作台
frontend/src/styles.css   响应式视觉系统
tests/                    API 与浏览器端到端测试
```

## 本地启动

面向非技术客户的推荐入口是直接双击项目根目录中的 `启动FrameFlow.bat`。首次运行会检查 Python、Node.js 与 FFmpeg，安装项目依赖、构建前端、启动统一服务并自动打开浏览器。详细步骤见 `客户使用说明.md`。

环境要求为 Python 3.12+、Node.js 20+ 和 npm。首次启动先安装依赖：

```powershell
cd C:\Users\liaoq\Desktop\toy
python -m pip install -r backend\requirements.txt
cd frontend
npm install
```

开发模式可在项目根目录运行：

```powershell
.\start-dev.ps1
```

随后访问 `http://127.0.0.1:5173`。脚本会在后台启动 FastAPI，并在当前终端启动 Vite；结束 Vite 后，后端进程也会一并停止。

生产模式先构建前端，再由 FastAPI 同时提供 API 和静态页面：

```powershell
cd C:\Users\liaoq\Desktop\toy\frontend
npm run build
cd ..
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

此时访问 `http://127.0.0.1:8000`。也可以使用 Docker：

```powershell
docker compose up --build
```

## 验证

```powershell
python -m pytest tests\test_api.py -q
cd frontend
npm run build
```

浏览器测试会自动管理前后端服务：

```powershell
python C:\Users\liaoq\.agents\skills\webapp-testing\scripts\with_server.py `
  --server "python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000" --port 8000 `
  --server "npm --prefix frontend run dev -- --host 127.0.0.1" --port 5173 `
  --timeout 60 -- python tests\e2e_studio.py
```

## 接入真实视频 API

供应商密钥必须只保存在服务端。复制配置模板并填入 MiniMax 开放平台创建的 API Key：

```powershell
Copy-Item backend\.env.example backend\.env
```

```dotenv
VIDEO_PROVIDER=minimax-official
MINIMAX_API_KEY=你的服务端密钥
```

`backend/app/providers.py` 已实现官方任务创建、10 秒轮询、文件地址获取、人民币成本估算和错误转换。商业版本还应继续实现以下逻辑：

1. 提交时发送幂等键，记录供应商任务 ID、请求版本、单镜头成本和素材快照。
2. 同时支持签名 Webhook 与降级轮询；验证回调签名后才更新任务状态。
3. 供应商返回的视频必须下载到自有对象存储，不能把短期外链直接交给用户。
4. 每个镜头独立重试并限制最大次数；失败时允许切换备用 Provider，不重复生成成功镜头。
5. 合成阶段使用 FFmpeg 统一画幅、帧率、色彩空间、旁白、字幕、BGM、Logo 和片尾。
6. 生成前冻结 Project、Asset 和 Prompt 版本，以便审计、复现和成本核算。

`director.py` 当前提供确定性的演示导演。接入多模态 LLM 时，应让模型严格输出 `CreativePlan` Schema，并保留服务端校验与兜底逻辑，避免自由文本污染后续生成链路。

## 云端 AI 部署（无需本地大模型）

FrameFlow 可以在另一台 Windows 或 Linux 电脑上运行，无需 Ollama 或 GPU。安装 Python、Node.js 和 FFmpeg，复制 `backend/.env.example` 为 `backend/.env`，然后填写以下服务端配置：

```dotenv
DASHSCOPE_API_KEY=your_dashscope_key
DIRECTOR_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
DIRECTOR_API_KEY=your_dashscope_key
DIRECTOR_MODEL=qwen3.6-flash
VISION_PROVIDER=dashscope
VISION_CLOUD_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
VISION_CLOUD_MODEL=qwen3.7-plus
VIDEO_PROVIDER=minimax-official
MINIMAX_API_KEY=your_minimax_key
```

导演、素材分析、关键帧审核和视频抽帧质检都会使用百炼云端模型，MiniMax 继续负责视频生成。Ollama 配置只是可选的本地备用项，在 `dashscope` 模式下不会调用。API Key 只能保存在 `backend/.env`，不得写入前端或提交到代码仓库。

## 从 MVP 到商用的必要升级

当前版本适合产品演示、用户访谈和 API 集成验证。正式商用前，应将 JSON Store 替换为 PostgreSQL，将进程内 BackgroundTasks 替换为 Redis/Celery 或云任务队列，并加入用户认证、租户隔离、对象存储、积分冻结与结算、内容审核、版权授权记录、Webhook 签名、可观测性和失败补偿。人物素材尤其需要取得明确授权；涉及儿童形象时还应建立更严格的监护人同意与素材留存规则。
