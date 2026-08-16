from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import json
import os
import shutil
from io import BytesIO
from os import getenv
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv, set_key
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from .composer import compose_clips
from .brand_bible import draft_brand_bible
from .fact_guard import assert_billable_ready, bible_conflicts, plan_conflicts
from .research_plan import build_plush_bear_30s_plan
from .benchmarking import (
    BenchmarkExperiment,
    BenchmarkStore,
    approve_benchmark,
    BlindReviewItem,
    next_blind_review,
    prepare_benchmark,
    record_blind_review,
    record_blind_review_by_token,
    write_markdown_report,
)
from .benchmark_assembly import BenchmarkAssemblyResult, assemble_benchmark_reel
from .benchmark_dataset import CalibrationReport, export_benchmark_dataset
from .director import director_mode, generate_creative_plan, repair_editorial_plan, repair_visual_consistency_plan
from .providers import configured_provider_name, get_provider
from .production import ProductionPlan, build_production_plan
from .orchestration import (
    WORKFLOW_VERSION,
    WorkflowRun,
    WorkflowAttempt,
    WorkflowStore,
    approve_node,
    build_workflow,
    complete_node,
    fail_node,
    refresh_readiness,
    reset_node_and_descendants,
    start_node,
    upgrade_workflow,
)
from .pipeline_executor import PipelineExecutor
from .live_preview import refresh_live_preview
from .postprocess import EnhancementCapability, enhancement_capabilities
from .quality_control import QualityBenchmarkReport, quality_benchmark
from .integrations import (
    EvaluationReport,
    IntegrationCapability,
    evaluate_video,
    integration_capabilities,
    shotcraft_summary,
)
from .audio_mastering import analyze_audio
from .routing import RoutingDecision, ProviderCapability, provider_capabilities, route_shot
from .vision import AssetVisualProfile, OllamaVisionService, vision_mode
from .workflow import (
    ProductionSession,
    ProductionSessionStore,
    build_session,
    review_keyframe,
)
from .schemas import (
    Asset,
    AssetKind,
    BrandBible,
    GenerationRequest,
    Project,
    ProjectCreate,
    ProjectPatch,
    LibraryAssetRequest,
    ScriptCandidate,
    ScriptSelectionRequest,
    ProductionBudget,
    ProductionPreflight,
    RenderTask,
    SystemConfig,
)
from .store import JsonStore

_configured_home = os.getenv("FRAMEFLOW_HOME", "").strip()
PROJECT_DIR = Path(_configured_home).resolve() if _configured_home else Path(__file__).resolve().parents[2]
BASE_DIR = PROJECT_DIR / "backend"
load_dotenv(BASE_DIR / ".env", override=True)
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
BENCHMARK_ARTIFACT_DIR = DATA_DIR / "benchmark-artifacts"
CATALOG_ASSET_DIR = DATA_DIR / "catalog-assets"
for runtime_dir in (
    UPLOAD_DIR,
    OUTPUT_DIR,
    DATA_DIR / "workflow-artifacts",
    BENCHMARK_ARTIFACT_DIR,
    CATALOG_ASSET_DIR,
    DATA_DIR / "benchmark-reports",
    DATA_DIR / "benchmark-datasets",
    DATA_DIR / "benchmarks",
    DATA_DIR / "production",
    DATA_DIR / "workflows",
):
    runtime_dir.mkdir(parents=True, exist_ok=True)
store = JsonStore(DATA_DIR / "store.json")
production_store = ProductionSessionStore(DATA_DIR / "production")
workflow_store = WorkflowStore(DATA_DIR / "workflows")
benchmark_store = BenchmarkStore(DATA_DIR / "benchmarks")
pipeline_executor = PipelineExecutor(
    workflow_store=workflow_store,
    upload_root=UPLOAD_DIR,
    artifact_root=DATA_DIR / "workflow-artifacts",
)

app = FastAPI(title="FrameFlow AI Director API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/catalog-assets", StaticFiles(directory=CATALOG_ASSET_DIR), name="catalog-assets")
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")
app.mount(
    "/artifacts",
    StaticFiles(directory=DATA_DIR / "workflow-artifacts"),
    name="workflow-artifacts",
)
app.mount(
    "/benchmark-artifacts",
    StaticFiles(directory=BENCHMARK_ARTIFACT_DIR),
    name="benchmark-artifacts",
)


def now() -> datetime:
    return datetime.now()


def require_project(project_id: str) -> Project:
    raw = store.get_project(project_id)
    if not raw:
        raise HTTPException(status_code=404, detail="项目不存在")
    return Project.model_validate(raw)


def persist_project(project: Project) -> Project:
    store.put_project(project.model_dump(mode="json"))
    return project


@app.get("/api/health")
def health() -> dict[str, str]:
    provider = configured_provider_name()
    return {"status": "ok", "mode": "api" if provider != "demo" else "demo", "provider": provider}


class VideoEvaluationRequest(BaseModel):
    video_path: str


class SetupRequest(BaseModel):
    dashscope_api_key: str = ""
    ai_provider: str = "ark"
    video_provider: str = "minimax-official"
    minimax_api_key: str = ""
    seedance_api_key: str = ""
    video_api_key: str = ""


class SetupStatus(BaseModel):
    ready: bool
    director_configured: bool
    vision_configured: bool
    video_configured: bool
    video_provider: str
    minimax_configured: bool
    seedance_configured: bool
    ai_provider: str


def _setup_status() -> SetupStatus:
    dashscope = bool(getenv("DASHSCOPE_API_KEY", "").strip())
    ark = bool(getenv("ARK_API_KEY", "").strip())
    ai_provider = getenv("VISION_PROVIDER", "dashscope").strip().lower()
    ai_configured = ark if ai_provider == "ark" else dashscope
    provider = getenv("VIDEO_PROVIDER", "demo").strip()
    minimax_configured = bool(getenv("MINIMAX_API_KEY", "").strip())
    seedance_configured = bool(getenv("ARK_API_KEY", "").strip())
    video_configured = minimax_configured if provider == "minimax-official" else seedance_configured
    return SetupStatus(
        ready=ai_configured and video_configured,
        director_configured=ai_configured,
        vision_configured=ai_configured,
        video_configured=video_configured,
        video_provider=provider,
        minimax_configured=minimax_configured,
        seedance_configured=seedance_configured,
        ai_provider=ai_provider,
    )


@app.get("/api/setup", response_model=SetupStatus)
def get_setup_status() -> SetupStatus:
    return _setup_status()


@app.post("/api/setup", response_model=SetupStatus)
def save_setup(payload: SetupRequest) -> SetupStatus:
    provider = payload.video_provider.strip()
    if provider not in {"minimax-official", "seedance-official"}:
        raise HTTPException(status_code=422, detail="不支持的视频供应商")
    env_path = BASE_DIR / ".env"
    env_path.touch(exist_ok=True)
    ai_provider = payload.ai_provider.strip().lower()
    if ai_provider not in {"dashscope", "ark"}:
        raise HTTPException(status_code=422, detail="不支持的脚本/质检供应商")
    if ai_provider == "ark":
        values = {
            "VIDEO_PROVIDER": provider,
            "VISION_PROVIDER": "ark",
            "VISION_CLOUD_API_BASE": "https://ark.cn-beijing.volces.com/api/v3",
            "VISION_CLOUD_MODEL": "doubao-seed-2-0-lite-260215",
            "VISION_REVIEW_MODEL": "doubao-seed-2-0-lite-260215",
            "DIRECTOR_API_BASE": "https://ark.cn-beijing.volces.com/api/v3",
            "DIRECTOR_MODEL": "doubao-seed-2-0-lite-260215",
            "DIRECTOR_REASONING_EFFORT": "none",
        }
    else:
        values = {
            "VIDEO_PROVIDER": provider,
            "VISION_PROVIDER": "dashscope",
            "VISION_CLOUD_API_BASE": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "VISION_CLOUD_MODEL": "qwen3.7-plus",
            "VISION_REVIEW_MODEL": "qwen3.7-plus",
            "DIRECTOR_API_BASE": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "DIRECTOR_MODEL": "qwen3.6-flash",
            "DIRECTOR_REASONING_EFFORT": "none",
        }
    if payload.dashscope_api_key.strip():
        values["DASHSCOPE_API_KEY"] = payload.dashscope_api_key.strip()
        values["DIRECTOR_API_KEY"] = payload.dashscope_api_key.strip()
    minimax_key = payload.minimax_api_key.strip()
    seedance_key = payload.seedance_api_key.strip()
    legacy_key = payload.video_api_key.strip()
    if legacy_key and not (minimax_key or seedance_key):
        if provider == "minimax-official":
            minimax_key = legacy_key
        else:
            seedance_key = legacy_key
    if minimax_key:
        values["MINIMAX_API_KEY"] = minimax_key
    if seedance_key:
        values["ARK_API_KEY"] = seedance_key
    if ai_provider == "ark":
        ark_key = seedance_key or getenv("ARK_API_KEY", "").strip()
        if ark_key:
            values["DIRECTOR_API_KEY"] = ark_key
            values["VISION_CLOUD_API_KEY"] = ark_key
    for key, value in values.items():
        set_key(str(env_path), key, value, quote_mode="never")
        os.environ[key] = value
    return _setup_status()


@app.get("/api/integrations", response_model=list[IntegrationCapability])
def get_integrations() -> list[IntegrationCapability]:
    return integration_capabilities()


@app.get("/api/integrations/shotcraft")
def get_shotcraft_summary() -> dict[str, object]:
    return shotcraft_summary()


@app.post("/api/integrations/evaluate", response_model=EvaluationReport)
def run_integrated_evaluation(payload: VideoEvaluationRequest) -> EvaluationReport:
    candidate = Path(payload.video_path)
    if not candidate.is_absolute():
        candidate = PROJECT_DIR / candidate
    try:
        report = evaluate_video(candidate)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"视频不存在：{exc}") from exc
    report.checks["audio_loudness"] = analyze_audio(candidate)
    return report


@app.get("/api/config", response_model=SystemConfig)
def system_config() -> SystemConfig:
    provider = configured_provider_name()
    return SystemConfig(
        mode="api" if provider != "demo" else "demo",
        default_provider=provider,
        provider_configured=provider != "demo",
        director_mode=director_mode(),
        vision_mode=vision_mode(),
        vision_model=(
            getenv("VISION_CLOUD_MODEL", "qwen-vl-max")
            if vision_mode() == "dashscope"
            else getenv("VISION_MODEL", "qwen3-vl:4b")
        ),
        pricing_note="MiniMax 海螺官方 2.3 Fast：768P 约 ¥1.35/6秒、¥2.25/10秒；失败任务以官方账单为准。",
        billing_currency="CNY",
    )


@app.get("/api/providers/capabilities", response_model=list[ProviderCapability])
def get_provider_capabilities() -> list[ProviderCapability]:
    """Return safe capability metadata without exposing provider credentials."""
    return provider_capabilities()


@app.get(
    "/api/postprocess/capabilities",
    response_model=list[EnhancementCapability],
)
def get_postprocess_capabilities() -> list[EnhancementCapability]:
    return enhancement_capabilities()


@app.get("/api/projects", response_model=list[Project])
def list_projects() -> list[dict]:
    return store.list_projects()


@app.get("/api/audio/options")
def audio_options() -> dict:
    bgm_root = Path("C:/Users/liaoq/.codex/skills/video-shotcraft/assets/audio/bgm")
    labels = {"house-vibez.mp3": "温暖生活", "cat-walk.mp3": "轻快俏皮", "bgm-tech-house.mp3": "科技律动", "tonight-hiphop.mp3": "都市节拍", "g-eazy-nba-type.mp3": "强劲运动"}
    return {
        "bgm_tracks": [{"id": "none", "label": "无背景音乐"}, *[{"id": item.name, "label": labels.get(item.name, item.stem)} for item in sorted(bgm_root.glob("*.mp3"))]],
        "voices": [
            {"id": "male-qn-qingse", "label": "清朗男声"},
            {"id": "female-shaonv", "label": "活力女声"},
            {"id": "female-yujie", "label": "沉稳女声"},
            {"id": "Microsoft Huihui Desktop", "label": "系统中文女声"},
        ],
    }


CATALOG_ITEMS = [
    {"id": "character-girl", "kind": "character", "title": "自然光女孩", "description": "居家自然光、专注神态，适合生活化玩具广告。", "image_url": "/catalog-assets/character-girl.jpg", "source_url": "https://www.pexels.com/photo/a-girl-piling-cube-blocks-5894710/"},
    {"id": "character-boy", "kind": "character", "title": "专注男孩", "description": "浅色服装与安静神态，适合学习和探索类叙事。", "image_url": "/catalog-assets/character-boy.jpg", "source_url": "https://www.pexels.com/photo/photo-of-a-boy-playing-with-wooden-toys-9271754/"},
    {"id": "character-family", "kind": "character", "title": "双人伙伴", "description": "两位儿童共同出镜，适合互动与分享场景。", "image_url": "/catalog-assets/character-family.jpg", "source_url": "https://www.pexels.com/photo/two-children-playing-a-wooden-toys-3662666/"},
    {"id": "product-rainbow", "kind": "product", "title": "彩虹积木", "description": "色彩和轮廓明确的木质玩具测试素材。", "image_url": "/catalog-assets/product-rainbow.jpg", "source_url": "https://www.pexels.com/photo/a-colorful-stack-toy-6219106/"},
    {"id": "product-stacker", "kind": "product", "title": "彩色叠叠乐", "description": "白色背景、主体清楚，适合商品一致性测试。", "image_url": "/catalog-assets/product-stacker.jpg", "source_url": "https://www.pexels.com/photo/a-colorful-toy-stack-over-white-surfave-6743152/"},
    {"id": "product-plush", "kind": "product", "title": "白色毛绒玩具", "description": "柔软材质与轮廓清晰，适合陪伴类画面测试。", "image_url": "/catalog-assets/product-plush.jpg", "source_url": "https://www.pexels.com/photo/close-up-shot-of-a-white-plush-toy-14587203/"},
    {"id": "scene-modern-living", "kind": "scene", "title": "现代客厅", "description": "中性色家具与大面积自然光，适合家庭叙事。", "image_url": "/catalog-assets/scene-modern-living.jpg", "source_url": "https://www.pexels.com/photo/modern-living-room-9976128/"},
    {"id": "scene-kids-bedroom", "kind": "scene", "title": "明亮儿童房", "description": "柔和纺织品、木质家具和窗光，适合玩具与陪伴故事。", "image_url": "/catalog-assets/scene-kids-bedroom.jpg", "source_url": "https://www.pexels.com/photo/kids-bedroom-interior-15625997/"},
    {"id": "scene-bright-apartment", "kind": "scene", "title": "温暖公寓", "description": "沙发、木地板与柔和窗光，适合真实生活演示。", "image_url": "/catalog-assets/scene-bright-apartment.jpg", "source_url": "https://www.pexels.com/photo/interior-of-light-room-at-apartment-6186848/"},
]


@app.get("/api/catalog")
def creative_catalog() -> list[dict]:
    return CATALOG_ITEMS


@app.get("/api/assets/library", response_model=list[Asset])
def asset_library() -> list[Asset]:
    seen: set[str] = set()
    result: list[Asset] = []
    for raw in store.list_projects():
        for asset in Project.model_validate(raw).assets:
            key = f"{asset.name}:{asset.size}:{asset.kind}"
            if key not in seen:
                seen.add(key)
                result.append(asset)
    return result


@app.post("/api/projects/{project_id}/assets/from-library", response_model=Asset, status_code=201)
def use_library_asset(project_id: str, payload: LibraryAssetRequest) -> Asset:
    project = require_project(project_id)
    source_asset = next((asset for raw in store.list_projects() for asset in Project.model_validate(raw).assets if asset.id == payload.asset_id), None)
    if source_asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    source = UPLOAD_DIR / source_asset.project_id / Path(source_asset.url).name
    if not source.is_file():
        raise HTTPException(status_code=404, detail="素材文件已丢失")
    asset_id = str(uuid4())
    target_dir = UPLOAD_DIR / project_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{asset_id}{source.suffix}"
    shutil.copy2(source, target)
    asset = source_asset.model_copy(update={"id": asset_id, "project_id": project_id, "url": f"/uploads/{project_id}/{target.name}", "created_at": now()})
    persist_project(project.model_copy(update={
        "assets": [*project.assets, asset],
        "creative_plan": None,
        "brand_bible": None,
        "asset_analysis_status": "pending",
        "asset_analysis_model": "",
        "analyzed_asset_ids": [],
        "asset_facts": [],
        "status": "draft",
        "updated_at": now(),
    }))
    return asset


@app.post("/api/projects/{project_id}/catalog/{item_id}", response_model=Project)
def select_catalog_item(project_id: str, item_id: str) -> Project:
    project = require_project(project_id)
    item = next((entry for entry in CATALOG_ITEMS if entry["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="素材目录项不存在")
    updates = {
        "creative_plan": None,
        "brand_bible": None,
        "asset_analysis_status": "pending",
        "asset_analysis_model": "",
        "analyzed_asset_ids": [],
        "asset_facts": [],
        "status": "draft",
        "updated_at": now(),
    }
    source = CATALOG_ASSET_DIR / f"{item_id}.jpg"
    if not source.is_file():
        raise HTTPException(status_code=404, detail="内置素材文件缺失")
    asset_id = str(uuid4())
    target_dir = UPLOAD_DIR / project_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{asset_id}.jpg"
    shutil.copy2(source, target)
    asset = Asset(
        id=asset_id,
        project_id=project_id,
        kind=AssetKind(item["kind"]),
        name=f"catalog-{item_id}.jpg",
        url=f"/uploads/{project_id}/{target.name}",
        content_type="image/jpeg",
        size=target.stat().st_size,
        created_at=now(),
    )
    return persist_project(project.model_copy(update={
        **updates,
        "assets": [*project.assets, asset],
    }))


@app.delete("/api/projects/{project_id}/assets/{asset_id}", response_model=Project)
def delete_project_asset(project_id: str, asset_id: str) -> Project:
    project = require_project(project_id)
    asset = next((item for item in project.assets if item.id == asset_id), None)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    path = (UPLOAD_DIR / project_id / Path(asset.url).name).resolve()
    project_root = (UPLOAD_DIR / project_id).resolve()
    if path.parent == project_root and path.is_file():
        path.unlink()
    return persist_project(project.model_copy(update={
        "assets": [item for item in project.assets if item.id != asset_id],
        "creative_plan": None,
        "brand_bible": None,
        "asset_analysis_status": "pending",
        "asset_analysis_model": "",
        "analyzed_asset_ids": [],
        "asset_facts": [],
        "status": "draft",
        "output_url": None,
        "updated_at": now(),
    }))


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict:
    require_project(project_id)
    if not store.delete_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    for root, suffix in [
        (UPLOAD_DIR, ""),
        (DATA_DIR / "workflow-artifacts", ""),
        (DATA_DIR / "production", ".json"),
        (DATA_DIR / "workflows", ".json"),
    ]:
        target = (root / f"{project_id}{suffix}").resolve()
        root_resolved = root.resolve()
        if target.parent != root_resolved:
            continue
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
    return {"deleted": True, "project_id": project_id}


@app.post("/api/projects", response_model=Project, status_code=201)
def create_project(payload: ProjectCreate) -> Project:
    timestamp = now()
    project = Project(id=str(uuid4()), created_at=timestamp, updated_at=timestamp, **payload.model_dump())
    return persist_project(project)


@app.get("/api/projects/{project_id}", response_model=Project)
def get_project(project_id: str) -> Project:
    return require_project(project_id)


@app.post("/api/projects/{project_id}/editorial/repair", response_model=Project)
def repair_project_editorial(project_id: str) -> Project:
    """Repair prose/timing and re-open only the free audio/composition tail."""
    project = require_project(project_id)
    repaired = persist_project(project.model_copy(update={
        "creative_plan": repair_editorial_plan(project),
        "status": "rendering",
        "updated_at": now(),
    }))
    run = workflow_store.get(project_id)
    if run:
        reset_node_and_descendants(run, "audio-timeline")
        workflow_store.put(run)
    return repaired


@app.post("/api/projects/{project_id}/continuity/repair", response_model=Project)
def repair_project_continuity(project_id: str) -> Project:
    project = require_project(project_id)
    repaired = persist_project(project.model_copy(update={
        "creative_plan": repair_visual_consistency_plan(project),
        "status": "rendering",
        "updated_at": now(),
    }))
    return repaired


@app.patch("/api/projects/{project_id}", response_model=Project)
def update_project(project_id: str, payload: ProjectPatch) -> Project:
    project = require_project(project_id)
    changes = payload.model_dump(exclude_none=True)
    return persist_project(project.model_copy(update={**changes, "updated_at": now()}))


@app.post("/api/projects/{project_id}/assets", response_model=Asset, status_code=201)
async def upload_asset(
    project_id: str,
    kind: AssetKind = Form(...),
    file: UploadFile = File(...),
) -> Asset:
    project = require_project(project_id)
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="目前只接受图片素材")
    content = await file.read()
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="单个文件不能超过 12MB")
    try:
        with Image.open(BytesIO(content)) as source_image:
            image = ImageOps.exif_transpose(source_image)
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail="文件不是可读取的 PNG、JPG 或 WebP 图片") from exc
    ratio = width / height
    if kind != AssetKind.brand and (min(width, height) <= 300 or not 0.4 < ratio < 2.5):
        raise HTTPException(
            status_code=422,
            detail=(
                f"参考图尺寸为 {width}×{height}，不符合视频模型要求："
                "短边必须大于 300 像素，长宽比必须在 2:5 与 5:2 之间。"
            ),
        )
    extension = Path(file.filename or "asset.jpg").suffix.lower() or ".jpg"
    asset_id = str(uuid4())
    project_dir = UPLOAD_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    target = project_dir / f"{asset_id}{extension}"
    target.write_bytes(content)
    asset = Asset(
        id=asset_id,
        project_id=project_id,
        kind=kind,
        name=file.filename or "未命名素材",
        url=f"/uploads/{project_id}/{target.name}",
        content_type=file.content_type,
        size=len(content),
        created_at=now(),
    )
    persist_project(project.model_copy(update={
        "assets": [*project.assets, asset],
        "creative_plan": None,
        "brand_bible": None,
        "asset_analysis_status": "pending",
        "asset_analysis_model": "",
        "analyzed_asset_ids": [],
        "asset_facts": [],
        "status": "draft",
        "updated_at": now(),
    }))
    return asset


@app.post("/api/projects/{project_id}/plan", response_model=Project)
async def plan_project(project_id: str) -> Project:
    project = require_project(project_id)
    template_guides = {
        "story": "采用真实故事推进：人物目标、遇到阻力、产品自然介入、获得反馈、生活化收束。",
        "problem-solution": "采用问题—尝试—解决结构，先呈现具体困难，再通过可见动作证明产品如何帮助。",
        "feature-proof": "采用功能实证结构，每个卖点必须有操作、产品反馈、人物反应三段证据链。",
        "documentary": "采用生活微纪录片结构，克制旁白、观察式镜头、自然动作和真实环境细节。",
        "product-film": "采用质感产品短片结构，以材质、结构、操作细节和使用结果逐层推进。",
    }
    control_guide = (
        f"导演控制参数：目标镜头数 {project.shot_count}；叙事节奏 {project.narrative_pace}；"
        f"开场钩子 {project.hook_style}；摄影方式 {project.camera_style}；光线 {project.lighting_style}；"
        f"写实程度 {project.realism_level}；情绪曲线 {project.emotion_curve}；旁白密度 {project.narration_density}；"
        f"产品露出 {project.product_exposure}；镜头衔接 {project.transition_style}；"
        f"禁止内容 {project.negative_constraints}。严格按目标镜头数规划，并把参数写入 visual、camera、"
        "transition、voiceover 与 prompt，而不只是写在说明中。"
    )
    user_intent = project.brief.strip()
    guided = project.model_copy(update={"brief": (
        f"{user_intent}\n\n" if user_intent else
        "用户没有指定故事情节，请由 AI 导演结合真实素材与产品卖点原创一个具体、自然、有因果关系的故事。\n\n"
    ) + f"脚本模板约束：{template_guides.get(project.script_template, template_guides['story'])}\n{control_guide}"})
    plan = await generate_creative_plan(guided)
    updated = persist_project(project.model_copy(update={
        "creative_plan": plan,
        "status": "planned",
        "updated_at": now(),
    }))
    workflow_store.put(build_workflow(updated, build_production_plan(updated)))
    return updated


@app.post("/api/projects/{project_id}/script-candidates", response_model=list[ScriptCandidate])
async def generate_script_candidates(project_id: str) -> list[ScriptCandidate]:
    project = await _ensure_project_visual_context(require_project(project_id))
    variants = [
        ("story", "故事叙事版", "用人物目标和情绪变化推动故事，产品自然进入行动。"),
        ("feature-proof", "功能实证版", "用操作、产品反馈、人物反应构成可见证据链。"),
        ("documentary", "生活纪录版", "使用观察式镜头和克制旁白，保留真实生活细节。"),
    ]
    candidates: list[ScriptCandidate] = []
    user_intent = project.brief.strip()
    for index, (template, label, instruction) in enumerate(variants, start=1):
        guided = project.model_copy(update={
            "script_template": template,
            "brief": (
                f"用户指定的故事要求（必须遵守）：{user_intent}\n" if user_intent else
                "用户未指定故事情节，请由 AI 导演自主构思。\n"
            ) + f"候选方案方向：{instruction}\n生成一个与其他候选明显不同、但严格遵守产品事实的方案。",
        })
        plan = await generate_creative_plan(guided)
        version_payload = json.dumps({
            "asset_ids": project.analyzed_asset_ids,
            "facts": project.asset_facts,
            "template": template,
            "plan": plan.model_dump(mode="json"),
        }, ensure_ascii=False, sort_keys=True, default=str)
        plan = plan.model_copy(update={
            "version_id": hashlib.sha256(version_payload.encode("utf-8")).hexdigest()[:16],
            "source_asset_ids": list(project.analyzed_asset_ids),
            "source_facts": list(project.asset_facts),
        })
        candidates.append(ScriptCandidate(id=f"candidate-{index}", label=label, template=template, plan=plan))
    return candidates


@app.post("/api/projects/{project_id}/script-selection", response_model=Project)
def select_script_candidate(project_id: str, payload: ScriptSelectionRequest) -> Project:
    project = require_project(project_id)
    current_asset_ids = [asset.id for asset in project.assets]
    if (
        payload.candidate.plan.source_asset_ids != current_asset_ids
        or not payload.candidate.plan.source_facts
    ):
        raise HTTPException(
            status_code=409,
            detail="候选脚本不是依据当前上传素材生成的，请重新执行素材理解并生成脚本。",
        )
    return persist_project(project.model_copy(update={
        "creative_plan": payload.candidate.plan,
        "script_template": payload.candidate.template,
        "status": "planned",
        "updated_at": now(),
    }))


@app.post("/api/projects/{project_id}/research/plush-bear-30s-plan", response_model=Project)
def build_plush_bear_research_plan(project_id: str) -> Project:
    """Free deterministic plan used for the isolated plush-bear research sample."""
    project = require_project(project_id)
    plan = build_plush_bear_30s_plan(project)
    conflicts = plan_conflicts(project, plan)
    if conflicts:
        raise HTTPException(status_code=409, detail="；".join(conflicts))
    updated = persist_project(project.model_copy(update={
        "duration": 30,
        "aspect_ratio": "9:16",
        "creative_plan": plan,
        "status": "planned",
        "updated_at": now(),
    }))
    workflow_store.put(build_workflow(updated, build_production_plan(updated)))
    return updated


@app.get("/api/projects/{project_id}/production-plan", response_model=ProductionPlan)
def get_production_plan(project_id: str) -> ProductionPlan:
    project = require_project(project_id)
    try:
        return build_production_plan(project)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/production-budget", response_model=ProductionBudget)
def get_production_budget(project_id: str) -> ProductionBudget:
    project = require_project(project_id)
    try:
        plan = build_production_plan(project)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return project.production_budget or ProductionBudget(ceiling_cny=plan.budget_ceiling_cny)


@app.get("/api/projects/{project_id}/production-preflight", response_model=ProductionPreflight)
def get_production_preflight(project_id: str) -> ProductionPreflight:
    """Read-only production gate report; safe to call repeatedly from the UI."""
    project = require_project(project_id)
    try:
        plan = build_production_plan(project)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    phrase = f"确认生产 {project.duration} 秒广告，预算 ¥{plan.budget_ceiling_cny:.2f}"
    blockers: list[str] = []
    checks = [
        f"已冻结 {len(plan.shots)} 镜头、{len(plan.keyframes)} 张共享边界帧",
        "Logo、CTA、字幕仅由 FFmpeg 后期叠加",
        "视频默认路由为 MiniMax Fast；Seedance 仅人工升级",
    ]
    if not project.brand_bible:
        blockers.append("尚未创建 Brand Bible 草案。")
    elif project.brand_bible.status != "approved":
        blockers.append("Brand Bible 尚未完成人工批准。")
    conflicts = plan_conflicts(project)
    if conflicts:
        blockers.extend(conflicts)
    budget = project.production_budget or ProductionBudget(ceiling_cny=plan.budget_ceiling_cny)
    if plan.cost.estimated_total > plan.budget_ceiling_cny:
        blockers.append("当前账面预估超过项目 ¥30 硬上限。")
    if not budget.approved:
        blockers.append("尚未确认本次生产预算；不会提交任何付费任务。")
    remaining = max(0.0, budget.approved_cny - (workflow_store.get(project_id).actual_cost_cny if workflow_store.get(project_id) else 0))
    return ProductionPreflight(
        project_id=project_id,
        ready_for_paid_generation=not blockers,
        estimated_total_cny=plan.cost.estimated_total,
        budget_ceiling_cny=plan.budget_ceiling_cny,
        remaining_budget_cny=round(remaining, 4),
        approval_phrase=phrase,
        checks=checks,
        blockers=list(dict.fromkeys(blockers)),
    )


class ProductionBudgetApprovalRequest(BaseModel):
    approval_phrase: str
    approved_budget_cny: float


@app.post("/api/projects/{project_id}/production-budget/approve", response_model=ProductionBudget)
def approve_production_budget(project_id: str, payload: ProductionBudgetApprovalRequest) -> ProductionBudget:
    project = require_project(project_id)
    plan = build_production_plan(project)
    expected = f"确认生产 {project.duration} 秒广告，预算 ¥{plan.budget_ceiling_cny:.2f}"
    if payload.approval_phrase.strip() != expected:
        raise HTTPException(status_code=409, detail=f"请使用精确确认语：{expected}")
    if abs(payload.approved_budget_cny - plan.budget_ceiling_cny) > 0.0001:
        raise HTTPException(status_code=409, detail="批准额度必须等于本次研究预算硬上限。")
    if plan.cost.estimated_total > plan.budget_ceiling_cny:
        raise HTTPException(status_code=409, detail="当前计划估算已超过预算上限，不能批准。")
    budget = ProductionBudget(
        ceiling_cny=plan.budget_ceiling_cny,
        approved_cny=payload.approved_budget_cny,
        approval_phrase=payload.approval_phrase.strip(),
        approved_at=now(),
    )
    persist_project(project.model_copy(update={"production_budget": budget, "updated_at": now()}))
    return budget


class WorkflowRequest(BaseModel):
    reset: bool = False


class WorkflowExecutionRequest(BaseModel):
    confirm_billable: bool = False
    provider_override: str = ""
    override_budget: bool = False


class WorkflowApprovalRequest(BaseModel):
    approved: bool = True
    override_failed_review: bool = False


class OneClickProductionRequest(BaseModel):
    budget_cny: float = Field(default=30, gt=0, le=500)


@app.post(
    "/api/projects/{project_id}/workflow/budget/approve",
    response_model=ProductionBudget,
)
def approve_workflow_budget(
    project_id: str,
    payload: OneClickProductionRequest,
) -> ProductionBudget:
    """Approve a visible UI budget without rebuilding the existing workflow."""
    project = require_project(project_id)
    run = workflow_store.get(project_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流尚未创建。")
    if run.actual_cost_cny > payload.budget_cny + 0.0001:
        raise HTTPException(
            status_code=409,
            detail=f"当前已产生 ¥{run.actual_cost_cny:.2f}，预算不能低于已发生费用。",
        )
    budget = ProductionBudget(
        ceiling_cny=payload.budget_cny,
        approved_cny=payload.budget_cny,
        approval_phrase="前端局部重做确认",
        approved_at=now(),
    )
    persist_project(project.model_copy(update={
        "production_budget": budget,
        "updated_at": now(),
    }))
    return budget


class BenchmarkApprovalRequest(BaseModel):
    approval_phrase: str
    approved_budget_cny: float


class BenchmarkBlindReviewRequest(BaseModel):
    score: int
    passed: bool
    failure_categories: list[str] = Field(default_factory=list)


class BenchmarkBlindTokenReviewRequest(BenchmarkBlindReviewRequest):
    token: str


@app.get("/api/projects/{project_id}/brand-bible", response_model=BrandBible | None)
def get_brand_bible(project_id: str) -> BrandBible | None:
    return require_project(project_id).brand_bible


def _workflow_asset_profiles(project: Project) -> dict[str, AssetVisualProfile]:
    """Map persisted visual profiles (stored by local absolute path) back to asset ids."""
    path = DATA_DIR / "workflow-artifacts" / project.id / "asset-profiles.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    by_filename = {Path(key).name: value for key, value in raw.items()}
    profiles: dict[str, AssetVisualProfile] = {}
    for asset in project.assets:
        payload = by_filename.get(Path(asset.url).name)
        if payload:
            try:
                profiles[asset.id] = AssetVisualProfile.model_validate(payload)
            except ValueError:
                continue
    return profiles


async def _ensure_project_visual_context(project: Project) -> Project:
    """Analyze every uploaded reference before any director call is allowed."""
    legacy_briefs = {
        "记录真实人物在具体生活情境中的一次使用过程，以动作、反馈和自然反应呈现产品价值。",
        "记录一个真实人物解决具体问题的过程；让商品自然进入行动，每个功能都必须形成动作、反馈和人物反应的证据链。",
    }
    asset_ids = [asset.id for asset in project.assets]
    cached_profiles = _workflow_asset_profiles(project)
    if (
        project.asset_analysis_status == "ready"
        and project.analyzed_asset_ids == asset_ids
        and project.asset_facts
        and len(cached_profiles) == len(project.assets)
        and project.brand_bible
        and project.brand_bible.status == "approved"
    ):
        return project
    if not project.assets:
        raise HTTPException(status_code=409, detail="请先上传商品图片，再生成创作方向。")

    persist_project(project.model_copy(update={
        "asset_analysis_status": "running",
        "updated_at": now(),
    }))
    service = OllamaVisionService(purpose="analysis")
    profiles_by_path: dict[str, dict] = {}
    profiles_by_id: dict[str, AssetVisualProfile] = {}
    errors: list[str] = []
    kind_labels = {
        AssetKind.product: "商品",
        AssetKind.character: "人物",
        AssetKind.scene: "场景",
        AssetKind.brand: "品牌",
    }
    for asset in project.assets:
        path = UPLOAD_DIR / project.id / Path(asset.url).name
        try:
            profile = await service.analyze_asset(
                image_path=path,
                asset_type=asset.kind.value,
            )
            profiles_by_path[str(path)] = profile.model_dump(mode="json")
            profiles_by_id[asset.id] = profile
        except Exception as exc:
            errors.append(f"{asset.name}: {exc}")

    if errors or len(profiles_by_id) != len(project.assets):
        failed = project.model_copy(update={
            "asset_analysis_status": "failed",
            "asset_analysis_model": service.model,
            "updated_at": now(),
        })
        persist_project(failed)
        raise HTTPException(
            status_code=502,
            detail="素材视觉理解失败，尚未调用导演模型：" + "；".join(errors),
        )

    root = DATA_DIR / "workflow-artifacts" / project.id
    root.mkdir(parents=True, exist_ok=True)
    (root / "asset-profiles.json").write_text(
        json.dumps(profiles_by_path, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    facts: list[str] = []
    for asset in project.assets:
        profile = profiles_by_id[asset.id]
        label = kind_labels[asset.kind]
        facts.append(f"{label}素材《{asset.name}》：{profile.summary}")
        facts.extend(f"{label}不可变事实：{item}" for item in profile.immutable_features[:6])
        facts.extend(f"{label}结构/身份：{item}" for item in profile.geometry_or_identity[:4])
    facts = list(dict.fromkeys(fact.strip() for fact in facts if fact.strip()))
    bible = draft_brand_bible(project, profiles=profiles_by_id).model_copy(update={
        "status": "approved",
        "updated_at": now(),
    })
    conflicts = bible_conflicts(bible)
    if conflicts:
        persist_project(project.model_copy(update={
            "asset_analysis_status": "failed",
            "brand_bible": bible.model_copy(update={"status": "conflict"}),
            "asset_facts": facts,
            "updated_at": now(),
        }))
        raise HTTPException(status_code=409, detail="素材事实存在冲突：" + "；".join(conflicts))
    return persist_project(project.model_copy(update={
        "asset_analysis_status": "ready",
        "asset_analysis_model": service.model,
        "analyzed_asset_ids": asset_ids,
        "asset_facts": facts,
        "brand_bible": bible,
        "creative_plan": None,
        "brief": "" if project.brief.strip() in legacy_briefs else project.brief,
        "status": "draft",
        "updated_at": now(),
    }))


@app.post("/api/projects/{project_id}/creative-context/analyze", response_model=Project)
async def analyze_creative_context(project_id: str) -> Project:
    return await _ensure_project_visual_context(require_project(project_id))


@app.post("/api/projects/{project_id}/brand-bible/draft", response_model=BrandBible)
def create_brand_bible_draft(project_id: str) -> BrandBible:
    project = require_project(project_id)
    session = production_store.get(project_id)
    profiles = session.asset_profiles if session else _workflow_asset_profiles(project)
    bible = draft_brand_bible(project, profiles=profiles)
    persist_project(project.model_copy(update={"brand_bible": bible, "updated_at": now()}))
    return bible


@app.put("/api/projects/{project_id}/brand-bible", response_model=BrandBible)
def update_brand_bible(project_id: str, payload: BrandBible) -> BrandBible:
    project = require_project(project_id)
    bible = payload.model_copy(update={"updated_at": now()})
    persist_project(project.model_copy(update={"brand_bible": bible, "updated_at": now()}))
    return bible


@app.post("/api/projects/{project_id}/brand-bible/approve", response_model=BrandBible)
def approve_brand_bible(project_id: str) -> BrandBible:
    project = require_project(project_id)
    if not project.brand_bible:
        raise HTTPException(status_code=409, detail="请先创建品牌真相库草案。")
    conflicts = bible_conflicts(project.brand_bible)
    if conflicts:
        bible = project.brand_bible.model_copy(update={"status": "conflict", "updated_at": now()})
        persist_project(project.model_copy(update={"brand_bible": bible, "updated_at": now()}))
        raise HTTPException(status_code=409, detail="事实冲突未解决：" + "；".join(conflicts))
    bible = project.brand_bible.model_copy(update={"status": "approved", "updated_at": now()})
    persist_project(project.model_copy(update={"brand_bible": bible, "updated_at": now()}))
    return bible


@app.get(
    "/api/projects/{project_id}/routing",
    response_model=list[RoutingDecision],
)
def get_project_routing(project_id: str) -> list[RoutingDecision]:
    project = require_project(project_id)
    if not project.creative_plan:
        raise HTTPException(status_code=409, detail="请先生成故事与分镜。")
    return [route_shot(project, shot) for shot in project.creative_plan.shots]


@app.post(
    "/api/projects/{project_id}/workflow",
    response_model=WorkflowRun,
)
def create_project_workflow(
    project_id: str,
    payload: WorkflowRequest | None = None,
) -> WorkflowRun:
    project = require_project(project_id)
    existing = workflow_store.get(project_id)
    reset = bool(payload and payload.reset)
    if existing and not reset and existing.version == WORKFLOW_VERSION:
        return existing
    try:
        plan = build_production_plan(project)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    fresh = build_workflow(project, plan)
    if existing and not reset:
        fresh = upgrade_workflow(existing, fresh)
    return workflow_store.put(fresh)


async def _run_one_click_production(project_id: str) -> None:
    """Run the approved DAG until completion or a genuine blocking failure.

    Visual review is advisory by default. This mirrors modern shot-based
    studios: a generated take remains available on the timeline while its
    review report travels alongside it. Strict mode can still hold major or
    critical findings; technical mode omits cloud visual judging altogether.
    """
    try:
        while True:
            project = require_project(project_id)
            run = workflow_store.get(project_id)
            if not run:
                return

            review = next((node for node in run.nodes if node.status == "review_required"), None)
            if review:
                passed = bool(review.attempts) and review.attempts[-1].outputs.get("passed") != "false"
                if not passed:
                    score = int(review.quality_decision.get("score", 0) or 0)
                    severity = str(review.quality_decision.get("severity", "major"))
                    # Personal-studio mode keeps visual review in the background.
                    # A clearly weak output gets one bounded local retry; acceptable
                    # takes continue without exposing a numeric score in the UI.
                    if project.quality_mode == "advisory" and score < 60:
                        target_id = (
                            review.id.replace("keyframe-review:", "keyframe:", 1)
                            if review.kind == "keyframe_review"
                            else review.id.replace("video-review:", "video:", 1)
                        )
                        target = next((node for node in run.nodes if node.id == target_id), None)
                        approved_budget = (
                            project.production_budget.approved_cny
                            if project.production_budget and project.production_budget.approved
                            else 0
                        )
                        can_retry_once = bool(target and len(target.attempts) == 1)
                        within_budget = bool(
                            target
                            and run.actual_cost_cny + target.estimated_cost_cny
                            <= approved_budget + 0.0001
                        )
                        if can_retry_once and within_budget:
                            reset_node_and_descendants(run, target_id)
                            workflow_store.put(run)
                            continue
                    should_hold = (
                        project.quality_mode == "strict"
                        and severity in {"major", "critical"}
                    )
                    if should_hold:
                        # Strict commercial review deliberately pauses, but
                        # never submits a paid retry without an operator.
                        return
                approve_node(run, review.id)
                workflow_store.put(run)
                continue

            ready = next((node for node in run.nodes if node.status == "ready"), None)
            if not ready:
                if run.status == "completed":
                    compose = next((node for node in run.nodes if node.id == "final-compose"), None)
                    output_url = compose.attempts[-1].outputs.get("video_url", "") if compose and compose.attempts else ""
                    persist_project(project.model_copy(update={
                        "status": "completed", "output_url": output_url, "updated_at": now(),
                    }))
                return

            if (
                project.quality_mode == "technical"
                and ready.kind in {"keyframe_review", "video_review"}
            ):
                ready.status = "skipped"
                ready.progress = 100
                ready.issues = ["仅技术检查模式：已跳过云端视觉审美判断。"]
                refresh_readiness(run)
                workflow_store.put(run)
                continue

            if ready.billable:
                approved_budget = (
                    project.production_budget.approved_cny
                    if project.production_budget and project.production_budget.approved
                    else 0
                )
                if run.actual_cost_cny + ready.estimated_cost_cny > approved_budget + 0.0001:
                    ready.status = "failed"
                    ready.issues = [
                        "本次局部重做会超过项目预算上限，未提交新的付费任务。"
                    ]
                    refresh_readiness(run)
                    workflow_store.put(run)
                    return

            run = await pipeline_executor.execute(
                project=project,
                run=run,
                node_id=ready.id,
                confirm_billable=True,
            )
            if ready.kind == "video_generation":
                try:
                    await refresh_live_preview(
                        project=project,
                        run=run,
                        artifact_root=DATA_DIR / "workflow-artifacts",
                    )
                except Exception as exc:
                    # Preview delivery is deliberately non-blocking. The
                    # original generated clip remains valid for final compose.
                    ready.issues.append(f"流式预览暂不可用：{exc}")
                workflow_store.put(run)

            if ready.kind == "asset_analysis":
                project = require_project(project_id)
                bible = draft_brand_bible(project, profiles=_workflow_asset_profiles(project))
                if bible_conflicts(bible):
                    persist_project(project.model_copy(update={
                        "brand_bible": bible.model_copy(update={"status": "conflict"}),
                        "updated_at": now(),
                    }))
                    return
                persist_project(project.model_copy(update={
                    "brand_bible": bible.model_copy(update={"status": "approved"}),
                    "updated_at": now(),
                }))
                # The user has explicitly selected and locked this creative
                # plan.  Visual analysis may strengthen the Brand Bible, but
                # must never silently replace the selected script, narration,
                # shot IDs or sound plan.  The approved facts are consumed by
                # build_production_plan when prompts are assembled.
                grounded_project = require_project(project_id)
                fresh_run = build_workflow(
                    grounded_project,
                    build_production_plan(grounded_project),
                )
                workflow_store.put(upgrade_workflow(run, fresh_run))
    except Exception:
        # The executor persists the exact failed node and error for the UI.
        return


@app.post(
    "/api/projects/{project_id}/produce",
    response_model=WorkflowRun,
    status_code=202,
)
async def produce_project_one_click(
    project_id: str,
    payload: OneClickProductionRequest,
    background_tasks: BackgroundTasks,
) -> WorkflowRun:
    if not _setup_status().ready:
        raise HTTPException(status_code=409, detail="请先完成云端模型配置")
    project = require_project(project_id)
    if not any(asset.kind == AssetKind.product for asset in project.assets):
        raise HTTPException(status_code=409, detail="请至少上传一张商品图片")
    if not project.creative_plan:
        raise HTTPException(status_code=409, detail="请先完成素材视觉理解，并从候选方案中锁定一个脚本。")
    current_asset_ids = [asset.id for asset in project.assets]
    if (
        project.creative_plan.source_asset_ids != current_asset_ids
        or not project.creative_plan.source_facts
        or project.asset_analysis_status != "ready"
    ):
        raise HTTPException(
            status_code=409,
            detail="当前脚本没有绑定最新素材事实。请返回故事页，重新执行素材理解并选择脚本。",
        )
    production_plan = build_production_plan(project)
    if production_plan.cost.estimated_total > payload.budget_cny:
        raise HTTPException(
            status_code=409,
            detail=f"预计费用 ¥{production_plan.cost.estimated_total:.2f} 超过本次预算 ¥{payload.budget_cny:.2f}",
        )
    project = persist_project(project.model_copy(update={
        "production_budget": ProductionBudget(
            ceiling_cny=payload.budget_cny,
            approved_cny=payload.budget_cny,
            approval_phrase="前端一键生产确认",
            approved_at=now(),
        ),
        "status": "rendering",
        "updated_at": now(),
    }))
    run = build_workflow(project, production_plan)
    workflow_store.put(run)
    background_tasks.add_task(_run_one_click_production, project_id)
    return run


@app.post(
    "/api/projects/{project_id}/produce/resume",
    response_model=WorkflowRun,
    status_code=202,
)
def resume_project_one_click(
    project_id: str,
    background_tasks: BackgroundTasks,
) -> WorkflowRun:
    project = require_project(project_id)
    run = workflow_store.get(project_id)
    if not run:
        raise HTTPException(status_code=404, detail="生产流程不存在")
    # Resuming a workflow that only has free review/composition work left must
    # not be blocked by a missing production budget.  A budget remains
    # mandatory whenever any paid node could still become executable.
    pending_billable = any(
        node.billable and node.status not in {"completed", "skipped"}
        for node in run.nodes
    )
    if pending_billable and (
        not project.production_budget or not project.production_budget.approved
    ):
        raise HTTPException(status_code=409, detail="本项目尚未批准生产预算")
    if any(node.status == "running" for node in run.nodes):
        raise HTTPException(status_code=409, detail="生产流程已经在运行")
    run.status = "running"
    run = workflow_store.put(run)
    background_tasks.add_task(_run_one_click_production, project_id)
    return run


@app.get(
    "/api/projects/{project_id}/workflow",
    response_model=WorkflowRun,
)
def get_project_workflow(project_id: str) -> WorkflowRun:
    project = require_project(project_id)
    existing = workflow_store.get(project_id)
    if existing and existing.version == WORKFLOW_VERSION:
        return existing
    try:
        fresh = build_workflow(project, build_production_plan(project))
        if existing:
            fresh = upgrade_workflow(existing, fresh)
        return workflow_store.put(fresh)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/workflow/events")
async def stream_project_workflow(project_id: str) -> StreamingResponse:
    """Stream durable workflow snapshots as Server-Sent Events.

    The workflow JSON remains the source of truth, so reconnecting a browser
    cannot lose state. Events are an inexpensive local equivalent of
    ComfyUI's execution/status messages and remove blind polling delays.
    """
    require_project(project_id)

    async def events():
        last_revision = ""
        idle_ticks = 0
        while True:
            run = workflow_store.get(project_id)
            if run:
                revision = run.updated_at.isoformat()
                if revision != last_revision:
                    payload = run.model_dump_json()
                    yield f"event: workflow\ndata: {payload}\n\n"
                    last_revision = revision
                    idle_ticks = 0
                else:
                    idle_ticks += 1
            if idle_ticks >= 15:
                yield ": keep-alive\n\n"
                idle_ticks = 0
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get(
    "/api/projects/{project_id}/quality-report",
    response_model=QualityBenchmarkReport,
)
def get_project_quality_report(project_id: str) -> QualityBenchmarkReport:
    require_project(project_id)
    run = workflow_store.get(project_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流尚未创建。")
    return quality_benchmark(run)


@app.post(
    "/api/projects/{project_id}/benchmarks",
    response_model=BenchmarkExperiment,
)
def prepare_project_benchmark(project_id: str) -> BenchmarkExperiment:
    project = require_project(project_id)
    experiment = prepare_benchmark(project)
    report = write_markdown_report(
        experiment,
        DATA_DIR / "benchmark-reports" / f"{experiment.id}.md",
    )
    experiment.report_path = str(report)
    return benchmark_store.put(experiment)


@app.get(
    "/api/projects/{project_id}/benchmarks/latest",
    response_model=BenchmarkExperiment | None,
)
def get_latest_project_benchmark(project_id: str) -> BenchmarkExperiment | None:
    require_project(project_id)
    return benchmark_store.latest(project_id)


@app.post(
    "/api/projects/{project_id}/benchmarks/{experiment_id}/approve",
    response_model=BenchmarkExperiment,
)
def approve_project_benchmark(
    project_id: str,
    experiment_id: str,
    payload: BenchmarkApprovalRequest,
) -> BenchmarkExperiment:
    require_project(project_id)
    experiment = benchmark_store.get(experiment_id)
    if not experiment or experiment.project_id != project_id:
        raise HTTPException(status_code=404, detail="基准实验不存在。")
    try:
        approve_benchmark(
            experiment,
            phrase=payload.approval_phrase,
            approved_budget_cny=payload.approved_budget_cny,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_markdown_report(experiment, Path(experiment.report_path))
    return benchmark_store.put(experiment)


@app.post(
    "/api/projects/{project_id}/benchmarks/{experiment_id}/jobs/{job_id}/review",
    response_model=BenchmarkExperiment,
)
def review_benchmark_job(
    project_id: str,
    experiment_id: str,
    job_id: str,
    payload: BenchmarkBlindReviewRequest,
) -> BenchmarkExperiment:
    require_project(project_id)
    experiment = benchmark_store.get(experiment_id)
    if not experiment or experiment.project_id != project_id:
        raise HTTPException(status_code=404, detail="基准实验不存在。")
    try:
        record_blind_review(
            experiment,
            job_id=job_id,
            score=payload.score,
            passed=payload.passed,
            failure_categories=payload.failure_categories,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_markdown_report(experiment, Path(experiment.report_path))
    return benchmark_store.put(experiment)


@app.get(
    "/api/projects/{project_id}/benchmarks/{experiment_id}/blind-review/next",
    response_model=BlindReviewItem | None,
)
def next_project_blind_review(project_id: str, experiment_id: str) -> BlindReviewItem | None:
    require_project(project_id)
    experiment = benchmark_store.get(experiment_id)
    if not experiment or experiment.project_id != project_id:
        raise HTTPException(status_code=404, detail="基准实验不存在。")
    item = next_blind_review(experiment)
    if item:
        benchmark_store.put(experiment)
    return item


@app.post(
    "/api/projects/{project_id}/benchmarks/{experiment_id}/blind-review",
    response_model=BenchmarkExperiment,
)
def submit_project_blind_review(
    project_id: str,
    experiment_id: str,
    payload: BenchmarkBlindTokenReviewRequest,
) -> BenchmarkExperiment:
    require_project(project_id)
    experiment = benchmark_store.get(experiment_id)
    if not experiment or experiment.project_id != project_id:
        raise HTTPException(status_code=404, detail="基准实验不存在。")
    try:
        record_blind_review_by_token(
            experiment,
            token=payload.token,
            score=payload.score,
            passed=payload.passed,
            failure_categories=payload.failure_categories,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_markdown_report(experiment, Path(experiment.report_path))
    return benchmark_store.put(experiment)


@app.post(
    "/api/projects/{project_id}/benchmarks/{experiment_id}/dataset/export",
    response_model=CalibrationReport,
)
def export_project_benchmark_dataset(project_id: str, experiment_id: str) -> CalibrationReport:
    """Export benchmark evidence and calibrate QC without any paid API call."""
    require_project(project_id)
    experiment = benchmark_store.get(experiment_id)
    if not experiment or experiment.project_id != project_id:
        raise HTTPException(status_code=404, detail="基准实验不存在。")
    return export_benchmark_dataset(experiment, DATA_DIR / "benchmark-datasets")


@app.get(
    "/api/projects/{project_id}/benchmarks/{experiment_id}/dataset/report",
    response_model=CalibrationReport,
)
def get_project_benchmark_calibration(project_id: str, experiment_id: str) -> CalibrationReport:
    require_project(project_id)
    experiment = benchmark_store.get(experiment_id)
    if not experiment or experiment.project_id != project_id:
        raise HTTPException(status_code=404, detail="基准实验不存在。")
    return export_benchmark_dataset(experiment, DATA_DIR / "benchmark-datasets")


@app.post(
    "/api/projects/{project_id}/benchmarks/{experiment_id}/assemble",
    response_model=BenchmarkAssemblyResult,
)
async def assemble_project_benchmark(
    project_id: str,
    experiment_id: str,
) -> BenchmarkAssemblyResult:
    """Create a local story sample from existing benchmark artifacts only."""
    project = require_project(project_id)
    experiment = benchmark_store.get(experiment_id)
    if not experiment or experiment.project_id != project_id:
        raise HTTPException(status_code=404, detail="基准实验不存在。")
    if experiment.assembly_status == "running":
        raise HTTPException(status_code=409, detail="本地样片正在合成，请稍后刷新。")
    experiment.assembly_status = "running"
    experiment.assembly_error = ""
    benchmark_store.put(experiment)
    try:
        result = await assemble_benchmark_reel(
            project=project,
            experiment=experiment,
            upload_root=UPLOAD_DIR,
            artifact_root=DATA_DIR / "benchmark-artifacts",
        )
        experiment.assembly_status = "completed"
        experiment.assembly_path = result.output_path
        experiment.assembly_output_url = result.output_url
        experiment.assembly_created_at = now()
        benchmark_store.put(experiment)
        return result
    except Exception as exc:
        experiment.assembly_status = "failed"
        experiment.assembly_error = str(exc)
        benchmark_store.put(experiment)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(
    "/api/projects/{project_id}/workflow/nodes/{node_id}/retry",
    response_model=WorkflowRun,
)
def retry_workflow_node(project_id: str, node_id: str) -> WorkflowRun:
    project = require_project(project_id)
    run = workflow_store.get(project_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流尚未创建。")
    target = next((item for item in run.nodes if item.id == node_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"工作流节点不存在：{node_id}")
    if target.kind not in {
        "keyframe_generation",
        "video_generation",
        "keyframe_review",
        "video_review",
        "audio_timeline",
        "composition",
    }:
        raise HTTPException(
            status_code=409,
            detail="只能局部重做关键帧、视频镜头、质检、音频时间轴或最终合成节点。",
        )
    if target.kind == "video_generation":
        missing_demo_keyframes: list[str] = []
        for dependency_id in target.dependencies:
            if not dependency_id.startswith("keyframe-review:"):
                continue
            keyframe_id = dependency_id.replace("keyframe-review:", "keyframe:", 1)
            keyframe = next((item for item in run.nodes if item.id == keyframe_id), None)
            has_image = bool(
                keyframe
                and keyframe.attempts
                and keyframe.attempts[-1].outputs.get("image_path")
            )
            if not has_image:
                missing_demo_keyframes.append(keyframe_id)
        if missing_demo_keyframes:
            raise HTTPException(
                status_code=409,
                detail=(
                    "这是旧版演示工作流，没有可复用的真实关键帧，不能只重做视频镜头。"
                    "请点击“按当前脚本开始真实生成”，系统将从关键帧开始建立完整产物链。"
                ),
            )
    approved_budget = (
        project.production_budget.approved_cny
        if project.production_budget and project.production_budget.approved
        else 0
    )
    if target.billable and run.actual_cost_cny + target.estimated_cost_cny > approved_budget + 0.0001:
        raise HTTPException(
            status_code=409,
            detail=(
                f"局部重做预计增加 ¥{target.estimated_cost_cny:.2f}，将超过当前项目预算上限。"
                "请提高预算后再重做，或选择接受当前镜头。"
            ),
        )
    try:
        reset_node_and_descendants(run, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return workflow_store.put(run)


@app.post(
    "/api/projects/{project_id}/workflow/nodes/{node_id}/skip",
    response_model=WorkflowRun,
)
def skip_optional_workflow_node(project_id: str, node_id: str) -> WorkflowRun:
    project = require_project(project_id)
    run = workflow_store.get(project_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流尚未建立。")
    node = next((item for item in run.nodes if item.id == node_id), None)
    if not node:
        raise HTTPException(status_code=404, detail="工作流节点不存在。")
    if node.kind not in {"upscale", "frame_interpolation"} or node.billable:
        raise HTTPException(status_code=409, detail="只有非付费的可选增强节点可以跳过。")
    if node.status == "running":
        raise HTTPException(status_code=409, detail="运行中的节点不能跳过。")
    node.status = "skipped"
    node.progress = 100
    node.issues = []
    refresh_readiness(run)
    saved = workflow_store.put(run)
    if saved.status == "completed":
        compose = next((item for item in saved.nodes if item.id == "final-compose"), None)
        output_url = compose.attempts[-1].outputs.get("video_url") if compose and compose.attempts else ""
        persist_project(project.model_copy(update={
            "status": "completed",
            "output_url": output_url or project.output_url,
            "updated_at": now(),
        }))
    return saved


async def _execute_workflow_node_background(
    project_id: str,
    node_id: str,
    confirm_billable: bool,
    provider_override: str,
) -> None:
    try:
        project = require_project(project_id)
        run = workflow_store.get(project_id)
        if not run:
            return
        await pipeline_executor.execute(
            project=project,
            run=run,
            node_id=node_id,
            confirm_billable=confirm_billable,
            provider_override=provider_override,
        )
        saved = workflow_store.get(project_id)
        if saved and saved.status == "completed":
            compose = next((item for item in saved.nodes if item.id == "final-compose"), None)
            output_url = compose.attempts[-1].outputs.get("video_url") if compose and compose.attempts else ""
            persist_project(project.model_copy(update={
                "status": "completed",
                "output_url": output_url or project.output_url,
                "updated_at": now(),
            }))
    except Exception:
        # PipelineExecutor persists a failed node with the provider error.
        # Background task failures must not terminate the API server.
        return


@app.post(
    "/api/projects/{project_id}/workflow/nodes/{node_id}/execute",
    response_model=WorkflowRun,
    status_code=202,
)
def execute_workflow_node(
    project_id: str,
    node_id: str,
    payload: WorkflowExecutionRequest,
    background_tasks: BackgroundTasks,
) -> WorkflowRun:
    project = require_project(project_id)
    run = workflow_store.get(project_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流尚未创建。")
    try:
        node = next(item for item in run.nodes if item.id == node_id)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail=f"工作流节点不存在：{node_id}") from exc
    if node.billable and not payload.confirm_billable:
        raise HTTPException(
            status_code=402,
            detail=f"节点“{node.label}”会调用付费API，请确认预计费用后再执行。",
        )
    retry_budget = node.estimated_cost_cny * (node.max_retries + 1)
    budget_exhausted = (
        node.billable
        and bool(node.attempts)
        and (
            node.retry_count >= node.max_retries
            or node.actual_cost_cny + node.estimated_cost_cny > retry_budget + 0.0001
        )
    )
    if budget_exhausted and not payload.override_budget:
        raise HTTPException(
            status_code=409,
            detail=(
                f"节点“{node.label}”已达到默认重试或费用上限（¥{retry_budget:.2f}）。"
                "如确需继续，请人工检查质检报告并明确覆盖预算。"
            ),
        )
    if node.billable:
        try:
            assert_billable_ready(project)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        plan = build_production_plan(project)
        budget = project.production_budget
        if not budget or not budget.approved:
            raise HTTPException(
                status_code=402,
                detail=(f"本研究计划预计 ¥{plan.cost.estimated_total:.2f}，硬上限 ¥{plan.budget_ceiling_cny:.2f}。"
                        f"请先以精确确认语批准：确认生产 30 秒广告，预算 ¥{plan.budget_ceiling_cny:.2f}"),
            )
        if run.actual_cost_cny + node.estimated_cost_cny > budget.approved_cny + 0.0001:
            raise HTTPException(status_code=409, detail="项目预算硬上限将被突破；不会提交新的付费任务。")
    if node.status not in {"ready", "failed"}:
        raise HTTPException(
            status_code=409,
            detail=f"节点当前状态不可执行：{node.status}",
        )
    running = next((item for item in run.nodes if item.status == "running" and item.id != node.id), None)
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"同一项目已有节点正在执行：{running.label}。为保护预算台账，请等待其完成。",
        )
    background_tasks.add_task(
        _execute_workflow_node_background,
        project_id,
        node_id,
        payload.confirm_billable,
        payload.provider_override,
    )
    return run


@app.post("/api/projects/{project_id}/workflow/recover-keyframes", response_model=WorkflowRun)
def recover_project_keyframes(project_id: str) -> WorkflowRun:
    """Reconcile already-downloaded keyframes after an interrupted/racing worker."""
    require_project(project_id)
    run = workflow_store.get(project_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流尚未建立。")
    root = DATA_DIR / "workflow-artifacts" / project_id
    for node in (item for item in run.nodes if item.kind == "keyframe_generation"):
        frame_id = node.id.split(":", 1)[1]
        files = sorted(
            path for path in root.glob(f"{frame_id}-attempt-*.jpg")
            if "-raw" not in path.stem
        )
        if not files:
            continue
        attempts: list[WorkflowAttempt] = []
        for number, path in enumerate(files, start=1):
            raw = path.with_name(f"{path.stem}-raw.jpg")
            attempts.append(WorkflowAttempt(
                number=number,
                status="completed",
                provider="seedream-official",
                model_id=getenv("ARK_IMAGE_MODEL", "doubao-seedream-5-0-lite-260128"),
                estimated_cost_cny=node.estimated_cost_cny,
                actual_cost_cny=node.estimated_cost_cny,
                finished_at=now().astimezone(),
                outputs={
                    "image_path": str(path),
                    "raw_image_path": str(raw) if raw.is_file() else "",
                    "provider": "seedream-official",
                    "model_id": getenv("ARK_IMAGE_MODEL", "doubao-seedream-5-0-lite-260128"),
                    "recovered": "true",
                },
            ))
        node.attempts = attempts
        node.actual_cost_cny = round(node.estimated_cost_cny * len(attempts), 4)
        node.status = "completed"
        node.progress = 100
        node.issues = []
        node.updated_at = now().astimezone()
    refresh_readiness(run)
    return workflow_store.put(run)


class VideoAttemptSelectionRequest(BaseModel):
    preferred_attempt: int


@app.post(
    "/api/projects/{project_id}/workflow/nodes/{node_id}/select-video-attempt",
    response_model=WorkflowRun,
)
async def select_video_attempt(
    project_id: str,
    node_id: str,
    payload: VideoAttemptSelectionRequest,
) -> WorkflowRun:
    """Select an already downloaded video attempt after QC or network interruption.

    No provider call is made. The selected successful attempt becomes the node's
    current output, and only its review plus downstream composition are reset.
    """
    require_project(project_id)
    run = workflow_store.get(project_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流尚未建立。")
    node = next((item for item in run.nodes if item.id == node_id), None)
    if not node or node.kind != "video_generation":
        raise HTTPException(status_code=404, detail="视频生成节点不存在。")
    selected = next(
        (
            attempt for attempt in node.attempts
            if attempt.number == payload.preferred_attempt
            and attempt.status == "completed"
            and Path(attempt.outputs.get("video_path", "")).is_file()
        ),
        None,
    )
    if selected is None:
        raise HTTPException(status_code=409, detail="指定尝试没有可用的本地视频文件。")
    remaining = [attempt for attempt in node.attempts if attempt is not selected]
    node.attempts = [*remaining, selected]
    node.status = "completed"
    node.progress = 100
    node.issues = []
    node.actual_cost_cny = round(sum(
        attempt.actual_cost_cny or 0.0
        for attempt in node.attempts
        if attempt.status == "completed"
    ), 4)
    review_id = f"video-review:{node.shot_id}"
    reset_node_and_descendants(run, review_id)
    review = next((item for item in run.nodes if item.id == review_id), None)
    if review is not None:
        review.status = "review_required"
        review.progress = 100
        review.issues = [
            f"已人工选择视频尝试 {payload.preferred_attempt}；请确认是否覆盖此前自动质检结果。"
        ]
    refresh_readiness(run)
    try:
        await refresh_live_preview(
            project=require_project(project_id),
            run=run,
            artifact_root=DATA_DIR / "workflow-artifacts",
        )
    except Exception as exc:
        node.issues.append(f"候选已切换，但流式预览刷新失败：{exc}")
    return workflow_store.put(run)


@app.post(
    "/api/projects/{project_id}/workflow/nodes/{node_id}/approve",
    response_model=WorkflowRun,
)
def approve_workflow_node(
    project_id: str,
    node_id: str,
    payload: WorkflowApprovalRequest,
) -> WorkflowRun:
    require_project(project_id)
    run = workflow_store.get(project_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流尚未创建。")
    node = next((item for item in run.nodes if item.id == node_id), None)
    if not node:
        raise HTTPException(status_code=404, detail=f"工作流节点不存在：{node_id}")
    if not payload.approved:
        node.status = "failed"
        node.issues = ["人工审核未通过，请局部重做该节点。"]
        return workflow_store.put(run)
    if node.attempts:
        auto_passed = node.attempts[-1].outputs.get("passed")
        if auto_passed == "false" and not payload.override_failed_review:
            raise HTTPException(
                status_code=409,
                detail="自动质检未通过；若已人工核验，请明确选择覆盖自动结果。",
            )
    try:
        approve_node(run, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return workflow_store.put(run)


class ProductionSessionRequest(BaseModel):
    reset: bool = False


class KeyframeReviewRequest(BaseModel):
    approved: bool
    override_auto_failure: bool = False


@app.post("/api/projects/{project_id}/production-session", response_model=ProductionSession)
def create_production_session(
    project_id: str,
    payload: ProductionSessionRequest,
) -> ProductionSession:
    project = require_project(project_id)
    existing = production_store.get(project_id)
    if existing and not payload.reset:
        return existing
    try:
        plan = build_production_plan(project)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return production_store.put(build_session(project, plan))


@app.get("/api/projects/{project_id}/production-session", response_model=ProductionSession)
def get_production_session(project_id: str) -> ProductionSession:
    require_project(project_id)
    session = production_store.get(project_id)
    if not session:
        raise HTTPException(status_code=404, detail="生产会话尚未创建。")
    return session


@app.post("/api/projects/{project_id}/analyze-assets", response_model=ProductionSession)
async def analyze_project_assets(project_id: str) -> ProductionSession:
    project = require_project(project_id)
    session = production_store.get(project_id)
    if not session:
        try:
            session = build_session(project, build_production_plan(project))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    service = OllamaVisionService()
    for asset in project.assets:
        candidate = UPLOAD_DIR / project.id / Path(asset.url).name
        if not candidate.is_file():
            continue
        session.asset_profiles[asset.id] = await service.analyze_asset(
            image_path=candidate,
            asset_type=asset.kind.value,
        )
        production_store.put(session)
    return production_store.put(session)


@app.post(
    "/api/projects/{project_id}/keyframes/{keyframe_id}/review",
    response_model=ProductionSession,
)
def review_project_keyframe(
    project_id: str,
    keyframe_id: str,
    payload: KeyframeReviewRequest,
) -> ProductionSession:
    require_project(project_id)
    session = production_store.get(project_id)
    if not session:
        raise HTTPException(status_code=404, detail="生产会话尚未创建。")
    try:
        review_keyframe(
            session,
            keyframe_id=keyframe_id,
            approved=payload.approved,
            override_auto_failure=payload.override_auto_failure,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return production_store.put(session)


def _reference_paths(project: Project) -> list[str]:
    priority = {AssetKind.product: 0, AssetKind.character: 1, AssetKind.scene: 2, AssetKind.brand: 3}
    # The first path becomes MiniMax's first frame. Prefer the newest product
    # image so replacing an invalid or outdated upload takes effect immediately.
    assets = sorted(
        project.assets,
        key=lambda asset: (priority[asset.kind], -asset.created_at.timestamp()),
    )
    paths: list[str] = []
    for asset in assets:
        candidate = UPLOAD_DIR / project.id / Path(asset.url).name
        if candidate.is_file():
            paths.append(str(candidate))
    return paths


def _sync_task_to_project(project_id: str, task_id: str, **project_changes: object) -> Project:
    project = require_project(project_id)
    task = RenderTask.model_validate(store.get_task(task_id))
    return persist_project(project.model_copy(update={
        "latest_task": task,
        "updated_at": now(),
        **project_changes,
    }))


async def run_render_legacy(project_id: str, task_id: str, provider_name: str) -> None:
    try:
        project = require_project(project_id)
        if not project.creative_plan:
            plan = await generate_creative_plan(project)
            project = persist_project(project.model_copy(update={"creative_plan": plan, "status": "planned"}))

        provider = get_provider(provider_name)
        references = _reference_paths(project)
        if provider.name != "demo" and not references:
            raise RuntimeError("请先上传至少一张商品参考图，再启动真实视频生成。")

        store.update_task(task_id, status="analyzing", progress=12, stage_label="正在建立人物、商品与场景连续性")
        _sync_task_to_project(project_id, task_id, status="rendering")
        store.update_task(task_id, status="planning", progress=25, stage_label="导演正在检查镜头因果与动作衔接")
        project = _sync_task_to_project(project_id, task_id, status="rendering")

        assert project.creative_plan is not None
        clip_urls: list[str] = []
        clip_durations: list[int] = []
        total_cost = 0.0
        shot_count = len(project.creative_plan.shots)
        for position, shot in enumerate(project.creative_plan.shots, start=1):
            shot.status = "rendering"
            progress = 25 + int((position - 1) / max(shot_count, 1) * 60)
            store.update_task(
                task_id,
                status="rendering",
                progress=progress,
                stage_label=f"正在生成剧情镜头 {position} / {shot_count}：{shot.title}",
            )
            persist_project(project.model_copy(update={
                "latest_task": RenderTask.model_validate(store.get_task(task_id)),
                "updated_at": now(),
            }))
            job = await provider.submit(
                prompt=shot.prompt,
                duration=shot.duration,
                reference_images=references,
                model_hint=shot.model_hint,
                aspect_ratio=project.aspect_ratio,
            )
            shot.status = "ready"
            shot.output_url = job.output_url
            shot.provider = job.model_id
            shot.estimated_cost = job.estimated_cost
            total_cost += job.estimated_cost
            if job.output_url:
                clip_urls.append(job.output_url)
                clip_durations.append(shot.duration)

        store.update_task(
            task_id,
            status="composing",
            progress=92,
            stage_label="正在统一画幅并按故事节奏合成镜头",
            estimated_cost=round(total_cost, 4),
        )
        persist_project(project.model_copy(update={
            "latest_task": RenderTask.model_validate(store.get_task(task_id)),
            "updated_at": now(),
        }))

        if provider.name == "demo":
            output_url = f"demo://preview/{project_id}"
        else:
            output_url = await compose_clips(
                project_id=project_id,
                clip_urls=clip_urls,
                clip_durations=clip_durations,
                aspect_ratio=project.aspect_ratio,
                output_root=OUTPUT_DIR,
            )

        completed = store.update_task(
            task_id,
            status="completed",
            progress=100,
            stage_label="叙事成片已完成",
            output_url=output_url,
            estimated_cost=round(total_cost, 4),
        )
        persist_project(project.model_copy(update={
            "status": "completed",
            "latest_task": RenderTask.model_validate(completed),
            "output_url": output_url,
            "updated_at": now(),
        }))
    except Exception as exc:
        failed = store.update_task(task_id, status="failed", stage_label="生成失败", error=str(exc))
        current = require_project(project_id)
        persist_project(current.model_copy(update={
            "status": "planned",
            "latest_task": RenderTask.model_validate(failed),
            "updated_at": now(),
        }))


async def run_render(project_id: str, task_id: str, provider_name: str) -> None:
    """Compatibility renderer with workflow observability.

    The paid generation behavior remains in the proven legacy function. This
    wrapper records exactly which production gates were used or bypassed so a
    preview can never be confused with a fully reviewed production render.
    """
    project = require_project(project_id)
    if not project.creative_plan:
        plan = await generate_creative_plan(project)
        project = persist_project(project.model_copy(
            update={"creative_plan": plan, "status": "planned", "updated_at": now()},
        ))
    try:
        workflow = workflow_store.get(project_id)
        if not workflow:
            workflow = build_workflow(project, build_production_plan(project))
        for node in workflow.nodes:
            if node.kind in {"asset_analysis", "story_plan"}:
                if node.status == "blocked":
                    node.status = "ready"
                if node.status == "ready":
                    complete_node(workflow, node.id, cache_hit=True)
            elif node.kind in {"keyframe_generation", "keyframe_review"}:
                node.status = "skipped"
                node.progress = 100
                node.issues = ["快速预览跳过共享关键帧审核；正式成片请使用生产工作流。"]
        refresh_readiness(workflow)
        workflow_store.put(workflow)
    except Exception:
        workflow = None

    await run_render_legacy(project_id, task_id, provider_name)

    if not workflow:
        return
    finished_project = require_project(project_id)
    task = RenderTask.model_validate(store.get_task(task_id))
    try:
        for shot in finished_project.creative_plan.shots if finished_project.creative_plan else []:
            video_id = f"video:{shot.id}"
            video_node = next((node for node in workflow.nodes if node.id == video_id), None)
            if not video_node:
                continue
            if shot.status == "ready":
                if video_node.status == "blocked":
                    video_node.status = "ready"
                if video_node.status in {"ready", "failed"}:
                    start_node(
                        workflow,
                        video_id,
                        provider=task.provider,
                        model_id=shot.provider or shot.model_hint,
                    )
                complete_node(
                    workflow,
                    video_id,
                    outputs={"video_url": shot.output_url or ""},
                    actual_cost_cny=shot.estimated_cost or 0,
                )
                review_id = f"video-review:{shot.id}"
                review = next((node for node in workflow.nodes if node.id == review_id), None)
                if review:
                    if review.status == "blocked":
                        refresh_readiness(workflow)
                    if review.status == "ready":
                        start_node(workflow, review_id, provider="qwen3-vl")
                        complete_node(
                            workflow,
                            review_id,
                            review_required=True,
                            outputs={"note": "等待生产模式执行抽帧视觉质检"},
                        )
            elif task.status == "failed":
                if video_node.status == "blocked":
                    video_node.status = "ready"
                if video_node.status in {"ready", "running"}:
                    fail_node(workflow, video_id, task.error or "视频生成失败")

        audio_node = next((node for node in workflow.nodes if node.id == "audio-timeline"), None)
        if audio_node and audio_node.status not in {"completed", "skipped"}:
            audio_node.status = "skipped"
            audio_node.progress = 100
            audio_node.issues = ["快速预览未生成完整旁白、字幕和声音桥。"]
        compose_node = next((node for node in workflow.nodes if node.id == "final-compose"), None)
        if task.status == "completed" and compose_node:
            compose_node.dependencies = ["audio-timeline"]
            refresh_readiness(workflow)
            if compose_node.status == "ready":
                start_node(workflow, "final-compose", provider="ffmpeg")
                complete_node(
                    workflow,
                    "final-compose",
                    outputs={"video_url": task.output_url or ""},
                )
        workflow_store.put(workflow)
    except Exception as exc:
        if task.status != "failed":
            task = store.update_task(
                task_id,
                stage_label="预览完成，工作流记录失败",
                error=f"工作流记录失败：{exc}",
            )


@app.post("/api/projects/{project_id}/render", response_model=RenderTask, status_code=202)
def render_project(project_id: str, payload: GenerationRequest, background_tasks: BackgroundTasks) -> RenderTask:
    project = require_project(project_id)
    if project.latest_task and project.latest_task.status not in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail="当前已有生成任务运行中")
    try:
        provider = get_provider(payload.provider)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    timestamp = now()
    task = RenderTask(
        id=str(uuid4()),
        project_id=project_id,
        status="queued",
        provider=provider.name,
        created_at=timestamp,
        updated_at=timestamp,
    )
    store.put_task(task.model_dump(mode="json"))
    persist_project(project.model_copy(update={
        "latest_task": task,
        "status": "rendering",
        "updated_at": timestamp,
    }))
    background_tasks.add_task(run_render, project_id, task.id, provider.name)
    return task


@app.get("/api/tasks/{task_id}", response_model=RenderTask)
def get_task(task_id: str) -> RenderTask:
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return RenderTask.model_validate(task)


FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
