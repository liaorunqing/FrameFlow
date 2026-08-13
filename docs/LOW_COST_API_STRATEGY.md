# FrameFlow 中国大陆官方视频 API 与叙事导演策略

## 1. 技术决策

FrameFlow 第一阶段不部署本地视频模型，也不再把海外聚合网关作为中国客户的默认生成线路。真实视频通过 MiniMax 海螺官方开放平台生成，账户、人民币充值、API Key 和账单统一由中国大陆可访问的官方平台管理。

|镜头类型|官方模型|分辨率与时长|公开按量参考价|用途|
|---|---|---|---:|---|
|普通生活流、商品使用、人物简单动作|MiniMax-Hailuo-2.3-Fast|768P，6 秒|¥1.35/条|默认主模型|
|较长叙事镜头|MiniMax-Hailuo-2.3-Fast|768P，10 秒|¥2.25/条|时长超过 6 秒|
|复杂动作或高质量关键镜头|MiniMax-Hailuo-2.3|1080P，6 秒|¥3.50/条|只用于 `premium` 镜头|
|复杂长镜头|MiniMax-Hailuo-2.3|768P，10 秒|¥4.00/条|只用于 `premium` 镜头|

价格是 2026 年 7 月查询到的 MiniMax 官方公开价格，生产环境以供应商实时账单为准。默认 15 秒广告包含三个 5 秒导演镜头，供应商实际生成三个 6 秒片段，再由合成器各裁切为 5 秒，名义生成成本约 ¥4.05。预算还应预留局部重试、旁白、音乐和存储成本。

## 2. 官方接口调用链

MiniMax 视频生成是异步任务，`MiniMaxOfficialVideoProvider` 已实现完整链路：

1. 将第一张商品参考图编码为 Data URI，避免要求本地开发机具备公网文件地址。
2. 调用 `POST /v1/video_generation` 创建图生视频任务并保存 `task_id`。
3. 每 10 秒调用 `GET /v1/query/video_generation` 查询任务状态。
4. 成功后取得 `file_id`，再调用 `GET /v1/files/retrieve` 获取临时下载地址。
5. 后端下载各镜头，使用 FFmpeg 统一画幅、帧率和编码，并按导演时长裁切后拼接。
6. 最终只向前端交付本系统的 `/outputs/.../final.mp4`，不直接暴露供应商临时地址。

当前接入使用官方支持的 `first_frame_image` 图生视频模式。后续可以在同一个 Provider 中继续增加首尾帧模式和 `S2V-01` 人物主体参考模式，无需修改上层项目、导演或任务接口。

## 3. 非模板化叙事规则

系统不采用“痛点—卖点—CTA”的固定文案模板，而使用以下故事约束：

1. 每支短片只有一个主角、一个具体的小愿望或困难，以及一个观众能够感知的变化。
2. 上一镜头必须通过动作、视线、声音或物体推动下一镜头，禁止互不相关的漂亮画面拼接。
3. 产品先作为生活中的物件出现，通过人物行动显露价值，而不是角色面对镜头讲功能。
4. 表演保留停顿、呼吸、犹豫和失败后再尝试的微小动作，减少“AI 广告式大笑”。
5. 旁白先写成一段连续散文，再分配到镜头；能由画面表达的内容不使用旁白。
6. Continuity Bible 锁定人物服装、发型、光线方向、商品结构和道具位置。
7. 转场来自故事动作和声音桥，不使用无意义的闪白、旋转和模板化特效。

这些约束位于 `backend/app/director.py`。配置 OpenAI-compatible 的低成本文本模型后，系统会动态创作；未配置时使用内置的生活流故事导演。

## 4. 服务端配置

复制配置模板：

```powershell
Copy-Item backend\.env.example backend\.env
```

编辑 `backend/.env`：

```dotenv
VIDEO_PROVIDER=minimax-official
MINIMAX_API_KEY=你的MiniMax开放平台API密钥
MINIMAX_API_BASE=https://api.minimaxi.com
# 正常网络留空；代理 Fake-IP 环境可填写经确认的官方真实 IP，逗号分隔
MINIMAX_API_IPS=
MINIMAX_POLL_INTERVAL_SECONDS=10
MINIMAX_TASK_TIMEOUT_SECONDS=900

# 以下三项可选，用于动态故事创作
DIRECTOR_API_BASE=https://你的兼容接口/v1
DIRECTOR_API_KEY=你的文本模型密钥
DIRECTOR_MODEL=模型名称
```

密钥只能存在后端环境，不能写入 React、提交到代码仓库或返回给浏览器。没有 `MINIMAX_API_KEY` 时系统自动进入 `demo` 模式，不会产生 API 费用。用户启动真实生成前必须上传至少一张满足官方尺寸要求的商品参考图。

## 5. 商用前仍需完成的工作

当前 Provider 已完成可测试的官方接入，但正式面向客户收费前还应增加持久化任务队列、镜头级重试与幂等键、内容审核记录、用户肖像授权、余额冻结、账单对账和国内对象存储。涉及真人尤其是儿童形象时，需要取得明确授权并制定更严格的素材留存与删除规则。
