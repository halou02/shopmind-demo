from __future__ import annotations

import asyncio
import json
import threading
import time
from contextvars import copy_context
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.core.capability_config import (
    CAPABILITY_CONFIG_FILENAME,
    CapabilityConfigError,
    image_request_config,
    read_capability_settings,
    update_capability_settings,
    video_request_config,
)
from app.core.llm_config import (
    CONFIG_PATH,
    LLMConfig,
    cloud_authorization,
    load_llm_config,
    public_capability_settings,
    public_llm_settings,
    save_llm_settings,
)
from app.core.local_text_probe import run_local_text_probe
from app.core.local_vision_probe import run_local_vision_probe

from app.ui.pipeline_runner import (
    BOUNDARY_NOTICE,
    DEMO_DATA_NOTICE,
    NOT_CONNECTED_NOTICE,
    default_workspace_root,
    resolve_material_root,
    run_analysis,
    run_publish,
)
from app.web.catalog import (
    list_products,
    filter_library_catalog_for_ui,
    list_projects,
    material_stats,
    product_detail,
    scan_material_catalog,
)
from app.web.desktop import open_folder_in_explorer
from app.web.export_download import build_export_zip, list_export_files, resolve_export_file
from app.web.folder_picker import (
    pick_folder_dialog,
    pick_image_files_dialog,
    pick_reference_files_dialog,
)
from app.web.material_delete import delete_material_relative_file, delete_workspace_product
from app.web.material_library_download import (
    build_material_library_zip,
    resolve_library_download_file,
    zip_download_filename,
)
from app.web.material_folders import (
    add_paths_to_folder,
    create_material_folder,
    delete_material_folder,
    list_material_folders,
    remove_path_from_all_folders,
    remove_path_from_folder,
    rename_path_in_all_folders,
    sync_folder_paths,
)
from app.web.material_library_store import (
    library_index_entries,
    record_library_files,
    rebuild_library_index,
    remove_library_file,
    rename_library_file,
)
from app.web.materials_flow import (
    auto_confirm_reference_images,
    build_material_catalog,
    image_payload as material_image_payload,
)
from app.web.creative_archive import finalize_creative_task, list_creative_exports, sync_generated_media_to_stores
from app.web.history_media import (
    attach_history_media_urls,
    resolve_history_media_file,
    resolve_product_media_file,
)
from app.web.product_store import (
    create_product,
    delete_product_file,
    list_product_files,
    save_product_file,
    save_product_profile,
)
from app.web.product_summary import save_product_summary
from app.web.reference_files import (
    copy_reference_files_into_material,
    copy_relative_files_between_roots,
    ensure_material_library_root,
    is_reference_file,
    save_reference_file,
)
from app.web.history_builder import build_history_record
from app.web.history_store import archive_task, delete_history_item, get_history_item, list_history
from app.core.ai_media_generator import AIMediaError, generate_images_from_prompt, generate_video_from_prompt
from app.web.media_generator import (
    build_image_prompts,
    ffmpeg_available,
    generate_slideshow_video,
    media_payload,
    save_image_prompts,
    save_storyboard_fallback,
    resolve_video_images,
)
from app.web.image_upload import (
    IMAGE_SUFFIXES,
    copy_images_into_material,
    ensure_upload_material_root,
    is_managed_upload_root,
    resolve_image_staging_root,
    save_uploaded_bytes,
)
from app.web.material_preview import list_image_files
from app.web.run_meta import write_run_meta
from app.web.scaffold_material import ensure_material_brief_xlsx
from app.demo_paths import BAG_PACK, JEANS_PACK
from app.web.session import SESSION

STATIC_DIR = Path(__file__).resolve().parent / "static"

for demo_pack in (JEANS_PACK, BAG_PACK):
    if demo_pack.is_dir():
        SESSION.register_root(demo_pack)


class PickFolderRequest(BaseModel):
    initial_dir: str | None = None
    target: str = "task"


class PickImagesRequest(BaseModel):
    initial_dir: str | None = None
    force_new_folder: bool = False


class PickFilesRequest(BaseModel):
    initial_dir: str | None = None
    force_new_folder: bool = False
    target: str = "task"


class UploadTargetRequest(BaseModel):
    target: str = "task"


class ImportLibraryFilesRequest(BaseModel):
    relative_paths: list[str]


class SelectReferenceImagesRequest(BaseModel):
    relative_paths: list[str] = Field(default_factory=list)


class CloudRequest(BaseModel):
    cloud_authorized: bool = False


class AnalyzeRequest(CloudRequest):
    material_path: str
    workspace_path: str | None = None
    product_form: dict[str, Any] | None = None


class PublishRequest(CloudRequest):
    direction_id: str


class GenerateMediaRequest(CloudRequest):
    generate_image: bool = False
    generate_video: bool = False
    image_prompt: str | None = None
    video_prompt: str | None = None
    negative_prompt: str | None = None


class ReferenceImageRemoveRequest(BaseModel):
    relative_path: str


class DeleteMaterialRequest(BaseModel):
    relative_path: str


class RenameMaterialRequest(BaseModel):
    relative_path: str
    new_name: str


class MaterialFolderCreateRequest(BaseModel):
    name: str


class MaterialFolderPathsRequest(BaseModel):
    relative_paths: list[str] = Field(default_factory=list)


class MaterialFolderRemovePathRequest(BaseModel):
    relative_path: str


class ProductSummarySaveRequest(BaseModel):
    sections: list[dict[str, str]]


class PreviewRequest(BaseModel):
    material_path: str


class ProductFormRequest(BaseModel):
    product_name: str | None = None
    product_category: str | None = None
    product_category_key: str | None = None
    product_price: str | None = None
    product_desc: str | None = None
    product_brand: str | None = None


class UnderstandingNotesRequest(BaseModel):
    notes: str


class LoadProjectRequest(CloudRequest):
    product_id: str
    rerun_analysis: bool = True


class OpenFolderRequest(BaseModel):
    path: str


class ProductSummarySectionRequest(BaseModel):
    id: str
    title: str | None = None
    body: str = ""


class ProductSummarySaveRequest(BaseModel):
    sections: list[ProductSummarySectionRequest]


class CreateProductRequest(BaseModel):
    product_name: str
    product_category: str | None = None
    product_category_key: str | None = None
    product_price: str | None = None
    product_brand: str | None = None
    product_desc: str | None = None


class ProductProfileSaveRequest(BaseModel):
    product_name: str | None = None
    product_category: str | None = None
    product_category_key: str | None = None
    product_price: str | None = None
    product_brand: str | None = None
    product_desc: str | None = None
    user_notes: str | None = None


class ProductDetailSaveRequest(BaseModel):
    profile: ProductProfileSaveRequest
    sections: list[ProductSummarySectionRequest]


class ProductFileDeleteRequest(BaseModel):
    relative_path: str


app = FastAPI(title="AIPC 商品创意导演", version="0.1.0")

class AISettingsRequest(BaseModel):
    base_url: str
    model: str
    timeout_seconds: float = Field(ge=1, le=600)
    max_images: int = Field(ge=0, le=20)
    api_key: str = ""
    clear_api_key: bool = False


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, exc):
    # Pydantic errors can include the submitted secret in `input`.
    return JSONResponse(status_code=422, content={"detail": "请求字段无效，请检查输入"})


class LocalRequestBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin")
        expected = scope.get("scheme", "http").encode() + b"://" + headers.get(b"host", b"")
        if origin is not None and origin != expected:
            response = JSONResponse(status_code=403, content={"detail": "拒绝非同源请求"})
            return await response(scope, receive, send)
        authorized = False
        messages = []
        if (scope["path"].startswith("/api/") and scope["method"] == "POST"
                and b"application/json" in headers.get(b"content-type", b"")):
            while True:
                message = await receive()
                messages.append(message)
                if not message.get("more_body", False):
                    break
            if b"application/json" in headers.get(b"content-type", b""):
                try:
                    body = json.loads(b"".join(m.get("body", b"") for m in messages))
                    authorized = isinstance(body, dict) and body.get("cloud_authorized") is True
                except (ValueError, TypeError):
                    pass
        async def replay():
            if messages:
                return messages.pop(0)
            return await receive()
        response_started = False
        async def safe_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)
        with cloud_authorization(authorized):
            try:
                await self.app(scope, replay, safe_send)
            except Exception:
                # Never forward provider/config exception text, URLs, or credentials.
                if response_started:
                    raise RuntimeError("本机响应处理失败") from None
                response = JSONResponse(status_code=500, content={"detail": "本机处理失败，请检查设置或使用本地规则"})
                await response(scope, replay, send)


app.add_middleware(LocalRequestBoundary)


@app.get("/api/ai-settings")
def get_ai_settings() -> dict[str, Any]:
    try:
        return public_llm_settings()
    except Exception:
        raise HTTPException(status_code=400, detail="无法读取 AI 设置") from None


@app.post("/api/ai-settings")
def update_ai_settings(payload: AISettingsRequest) -> dict[str, Any]:
    try:
        return save_llm_settings(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=400, detail="无法保存 AI 设置") from None


@app.get("/api/ai-capabilities")
def get_ai_capabilities() -> dict[str, Any]:
    try:
        return public_capability_settings(read_capability_settings())
    except CapabilityConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=400, detail="无法读取 AI 能力配置") from None


@app.post("/api/ai-capabilities/{capability}")
def update_ai_capability(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    if capability not in {"text", "vision", "image", "video"}:
        raise HTTPException(status_code=404, detail="该能力尚未开放配置入口")
    changes = {key: value for key, value in payload.items() if key != "cloud_authorized"}
    if not changes:
        raise HTTPException(status_code=400, detail="至少填写一项能力设置")
    try:
        return public_capability_settings(update_capability_settings(capability, changes))
    except (CapabilityConfigError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=400, detail="无法保存 AI 能力配置") from None


@app.post("/api/ai-capabilities/vision/test")
def test_local_vision_connection(payload: dict[str, Any]) -> dict[str, Any]:
    """图片理解的连接自检：会主动向本机 Ollama 真发请求的两个入口之一（另一个见下面文字那条）。

    只读配置：不写盘、不改 connection_status、不碰 v1 文件、不接收前端传来的地址。
    失败是数据不是异常——每种失败带自己的 reason 和一句不同的话，一律 200 返回；走非 200 就
    会被前端 api() 那条 detail 渲染路重新压成同一句，正是这里要消灭的行为。
    """
    if payload:
        raise HTTPException(status_code=400, detail="测试连接不接受任何参数")
    return run_local_vision_probe()


@app.post("/api/ai-capabilities/text/test")
def test_local_text_connection(payload: dict[str, Any]) -> dict[str, Any]:
    """文字推理的连接自检：与图片理解那条同一套契约，只是探的是 OpenAI 兼容口。

    同样只读配置、一律 200、不写 connection_status：探测结果是一次性的事实，
    写进设置就会变成一条可能过期的缓存结论。
    """
    if payload:
        raise HTTPException(status_code=400, detail="测试连接不接受任何参数")
    return run_local_text_probe()


def _workspace() -> Path:
    return SESSION.workspace_path or default_workspace_root()


def _demo_packs() -> list[dict[str, str]]:
    packs: list[dict[str, str]] = []
    if JEANS_PACK.is_dir():
        packs.append(
            {
                "id": "jeans",
                "name": "界线 直筒牛仔裤 01",
                "category": "服装",
                "path": str(JEANS_PACK),
                "icon": "👖",
            }
        )
    if BAG_PACK.is_dir():
        packs.append(
            {
                "id": "bag",
                "name": "轻序 Daily Tote 01",
                "category": "箱包",
                "path": str(BAG_PACK),
                "icon": "👜",
            }
        )
    return packs


def _material_library_root() -> Path:
    workspace = _workspace()
    SESSION.register_root(workspace)
    library = ensure_material_library_root(workspace)
    SESSION.register_root(library)
    return library


def _stage_reference_paths(
    source_paths: list[Path],
    *,
    force_new_folder: bool = False,
    target: str = "task",
) -> dict[str, Any]:
    workspace = _workspace()
    SESSION.register_root(workspace)
    if target == "library":
        previous_material = SESSION.material_path
        material_root = _material_library_root()
        try:
            copied = copy_reference_files_into_material(material_root, source_paths)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        SESSION.register_root(material_root)
        record_library_files(material_root, copied, source="pick")
        _restore_task_material_path(previous_material)
        return _build_library_catalog(
            material_root,
            added_count=len(copied),
            added_files=[path.name for path in copied],
        )

    material_root = resolve_image_staging_root(
        SESSION.material_path,
        workspace,
        force_new=force_new_folder,
    )
    try:
        copied = copy_reference_files_into_material(material_root, source_paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    SESSION.set_material(material_root)
    SESSION.register_root(material_root)
    ensure_material_brief_xlsx(material_root, SESSION.product_form)
    _sync_confirmed_images(material_root)
    catalog = _material_catalog_payload(
        material_root,
        added_count=len(copied),
        added_files=[path.name for path in copied],
    )
    return catalog


def _image_payload(material_root: Path, relative_path: str) -> dict[str, str] | None:
    return material_image_payload(material_root, relative_path)


def _file_payload(material_root: Path, relative_path: str) -> dict[str, str] | None:
    candidate = (material_root / relative_path).resolve()
    material_resolved = material_root.resolve()
    try:
        candidate.relative_to(material_resolved)
    except ValueError:
        return None
    if not candidate.is_file() or not is_reference_file(candidate.name):
        return None
    return {
        "name": candidate.name,
        "relative_path": relative_path,
        "url": f"/api/material-file?path={candidate.as_posix()}",
        "is_image": candidate.suffix.lower() in IMAGE_SUFFIXES,
    }


def _material_catalog_payload(root: Path, **extra: Any) -> dict[str, Any]:
    return build_material_catalog(root, SESSION.reference_image_paths, **extra)


def _sync_confirmed_images(material_root: Path, relative_paths: list[str] | None = None) -> list[str]:
    confirmed = auto_confirm_reference_images(
        material_root,
        SESSION.reference_image_paths,
        relative_paths=relative_paths,
    )
    SESSION.reference_image_paths = confirmed
    return confirmed


def _build_library_catalog(library_root: Path, **extra: Any) -> dict[str, Any]:
    rebuild_library_index(library_root)
    catalog = filter_library_catalog_for_ui(build_material_catalog(library_root, []))
    catalog.update(
        {
            "target": "library",
            "library_path": str(library_root),
            "index_count": len(library_index_entries(library_root)),
            "folders": list_material_folders(library_root),
        }
    )
    catalog.update(extra)
    return catalog


def _restore_task_material_path(previous: Path | None) -> None:
    if previous is not None and previous.is_dir():
        SESSION.material_path = previous


def _delete_material_file(material_root: Path, relative_path: str) -> dict[str, Any]:
    try:
        delete_material_relative_file(material_root, relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    SESSION.reference_image_paths = [
        path for path in SESSION.reference_image_paths if path != relative_path
    ]
    return _material_catalog_payload(material_root)


async def _save_files_to_library(
    uploads: list[UploadFile],
    *,
    source: str = "upload",
) -> dict[str, Any]:
    previous_material = SESSION.material_path
    library = _material_library_root()
    saved_paths: list[Path] = []
    for upload in uploads:
        content = await upload.read()
        if not content:
            continue
        try:
            saved_paths.append(
                save_reference_file(library, upload.filename or "file.bin", content)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if not saved_paths:
        raise HTTPException(status_code=400, detail="未收到有效的参考资料文件")
    SESSION.register_root(library)
    record_library_files(library, saved_paths, source=source)
    _restore_task_material_path(previous_material)
    return _build_library_catalog(
        library,
        added_count=len(saved_paths),
        added_files=[path.name for path in saved_paths],
    )


def _selected_direction(direction_id: str | None = None):
    if SESSION.analysis is None or not SESSION.analysis.directions:
        return None
    target_id = direction_id or SESSION.selected_direction_id
    if not target_id:
        return None
    for direction in SESSION.analysis.directions.directions:
        if direction.direction_id == target_id:
            return direction
    return None


def _export_media_dir() -> Path:
    export = SESSION.publish.export if SESSION.publish else None
    if export is None or not export.package_root.exists():
        raise HTTPException(status_code=400, detail="请先在步骤 4 生成内容")
    media_dir = export.package_root / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    SESSION.register_root(export.package_root)
    SESSION.active_media_dir = media_dir.resolve()
    return media_dir


def _resolve_media_dir() -> Path:
    if SESSION.publish and SESSION.publish.export and SESSION.publish.export.package_root.exists():
        media_dir = (SESSION.publish.export.package_root / "media").resolve()
        SESSION.active_media_dir = media_dir
        return media_dir
    if SESSION.active_media_dir and SESSION.active_media_dir.exists():
        return SESSION.active_media_dir.resolve()
    raise HTTPException(status_code=400, detail="请先在步骤 4 生成内容")


def _resolve_image_config() -> LLMConfig:
    try:
        return image_request_config(load_llm_config)
    except CapabilityConfigError:
        return load_llm_config()


def _resolve_video_config() -> LLMConfig:
    try:
        return video_request_config(load_llm_config)
    except CapabilityConfigError:
        return load_llm_config()


_IMAGE_JOB_LOCK = threading.Lock()
_IMAGE_JOB: dict[str, Any] = {"status": "idle"}


def _set_image_job(payload: dict[str, Any]) -> None:
    with _IMAGE_JOB_LOCK:
        _IMAGE_JOB.clear()
        _IMAGE_JOB.update(payload)


def _get_image_job() -> dict[str, Any]:
    with _IMAGE_JOB_LOCK:
        return dict(_IMAGE_JOB)


def _run_image_generation_job(
    *,
    media_dir: Path,
    image_paths: list[Path],
    prompt: str,
    negative: str,
    image_cfg: LLMConfig,
) -> None:
    started = time.time()
    _set_image_job({"status": "running", "message": "已提交百炼绘图任务，通常需 2–5 分钟…", "started_at": started})
    try:
        with cloud_authorization(True):
            saved, source = generate_images_from_prompt(
            prompt=prompt,
            output_dir=media_dir / "generated_images",
            reference_images=image_paths,
            negative_prompt=negative,
            count=min(4, len(image_paths) or 1),
            config=image_cfg,
            )
        SESSION.register_root(media_dir)
        SESSION.generated_media["images"] = [media_payload(path, media_dir) for path in saved]
        SESSION.generated_media["image_source"] = source
        export = SESSION.publish.export if SESSION.publish else None
        archive = SESSION.creative_archive if isinstance(SESSION.creative_archive, dict) else {}
        export_info = archive.get("export") if isinstance(archive.get("export"), dict) else {}
        if export and export.package_root.exists():
            sync_generated_media_to_stores(
                _workspace(),
                package_root=export.package_root,
                product_id=export.product_id,
                material_path=str(SESSION.material_path) if SESSION.material_path else None,
                reference_paths=list(SESSION.reference_image_paths),
                export_folder=export_info.get("folder_path"),
                exclude_relative_paths=list(SESSION.library_imported_paths),
            )
        message = (
            "AI 图片已生成并保存" if source == "ai_image" else "已保存参考图副本（AI 绘图不可用）"
        )
        _archive_current_task()
        _set_image_job({
            "status": "done",
            "message": message,
            "image_source": source,
            "generated_media": dict(SESSION.generated_media),
            "image_prompts": list(SESSION.image_prompts),
            "elapsed_seconds": round(time.time() - started, 1),
        })
    except AIMediaError as exc:
        _set_image_job({"status": "error", "message": str(exc), "elapsed_seconds": round(time.time() - started, 1)})
    except Exception:
        _set_image_job({
            "status": "error",
            "message": "图片生成失败，请稍后重试",
            "elapsed_seconds": round(time.time() - started, 1),
        })


def _build_history_record(direction_id: str | None = None) -> dict[str, Any]:
    direction = _selected_direction(direction_id)
    understanding = SESSION.analysis.understanding if SESSION.analysis else None
    export = SESSION.publish.export if SESSION.publish else None
    content = SESSION.publish.content if SESSION.publish else None
    product_name = (
        SESSION.product_form.get("product_name")
        or (understanding.product_name if understanding else None)
        or "未命名商品"
    )
    archive = SESSION.creative_archive or {}
    export_info = archive.get("export") if isinstance(archive, dict) else None
    export_folder = export_info.get("folder_path") if isinstance(export_info, dict) else None
    return build_history_record(
        task_id=SESSION.last_history_id,
        product_name=product_name,
        product_category=SESSION.product_form.get("product_category"),
        product_id=export.product_id if export else None,
        direction_id=direction.direction_id if direction else direction_id,
        direction_name=direction.name if direction else None,
        main_selling_point=direction.main_selling_point if direction else None,
        material_path=str(SESSION.material_path) if SESSION.material_path else None,
        reference_paths=list(SESSION.reference_image_paths),
        package_root=str(export.package_root) if export and export.package_root else None,
        package_id=export.package_id if export else None,
        export_folder=export_folder,
        content=content,
        image_prompts=list(SESSION.image_prompts),
        generated_media=SESSION.generated_media,
        creative_archive=archive if archive else None,
    )


def _archive_current_task(direction_id: str | None = None) -> dict[str, Any] | None:
    if SESSION.publish is None or not SESSION.publish.ok:
        return None
    record = _build_history_record(direction_id)
    archived = archive_task(_workspace(), record)
    SESSION.last_history_id = archived["id"]
    return archived


def _save_run_meta(bundle, material_path: str) -> None:
    understanding = bundle.understanding
    if understanding is None:
        return
    write_run_meta(
        _workspace(),
        understanding.product_id,
        material_path=material_path,
        product_name=SESSION.product_form.get("product_name") or understanding.product_name,
        category=SESSION.product_form.get("product_category"),
        brand=SESSION.product_form.get("product_brand"),
        price=SESSION.product_form.get("product_price"),
        product_form=SESSION.product_form,
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "api_version": "2026-10-06-materials-v2"}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "boundary_notice": BOUNDARY_NOTICE,
        "not_connected_notice": NOT_CONNECTED_NOTICE,
        "demo_data_notice": DEMO_DATA_NOTICE,
        "demo_packs": _demo_packs(),
        "help": {
            "title": "ShopMind 帮助中心",
            "subtitle": "从素材上传到多平台创意导出，完整掌握 AI 商品创意导演的工作流",
            "footer": "所有数据保存在本地 workspace 目录，可随时打开文件夹查看与备份。",
            "sections": [
                {
                    "id": "overview",
                    "label": "快速入门",
                    "icon": "🚀",
                    "title": "ShopMind 是什么？",
                    "summary": "面向电商与品牌团队的本地 AI 创意工作台，帮你把商品资料快速转化为可落地的多平台内容。",
                    "bullets": [
                        "读取本地图片、Excel、文档等资料，自动完成商品理解与经营判断",
                        "一次分析可生成小红书、抖音、快手、购物平台等多渠道文案",
                        "同步输出视觉概念、视频脚本，并支持 AI 配图与视频生成",
                        "创意结果自动归档到商品库、素材库与「创意导出」文件夹",
                    ],
                },
                {
                    "id": "creative",
                    "label": "创意任务",
                    "icon": "✨",
                    "title": "创意任务五步流程",
                    "summary": "从左侧导航或首页进入「创意任务」，按步骤完成一次完整创作。",
                    "steps": [
                        {
                            "title": "商品录入",
                            "desc": "上传参考资料或从素材库导入，填写商品名称、类别、价格等基础信息。",
                        },
                        {
                            "title": "AI 理解",
                            "desc": "系统扫描素材、提取证据，生成商品理解报告与经营判断，可人工修正备注。",
                        },
                        {
                            "title": "创意方向",
                            "desc": "本地规则蓝图给出 3 个创意方向，核对与商品适配后选择一条继续。"
                        },
                        {
                            "title": "内容生成",
                            "desc": "生成四平台文案、视觉方案与视频脚本，可重新生成或自定义配图/视频提示词。",
                        },
                        {
                            "title": "导出归档",
                            "desc": "内容包写入「创意导出」文件夹，商品总结入库，用过的素材自动分类进素材库。",
                        },
                    ],
                },
                {
                    "id": "product",
                    "label": "商品库",
                    "icon": "📦",
                    "title": "商品库管理",
                    "summary": "每个商品独立保存资料、文件与创意总结，支持手动添加与 AI 生成内容混合编辑。",
                    "bullets": [
                        "点击「添加商品」可手动创建商品，填写基本信息并上传相关文件",
                        "点击卡片「详情」查看：基本信息、补充说明、相关文件、创意任务生成板块",
                        "点击「修改」可编辑全部文字内容，「保存」写入本地 product_profile 与 product_summary",
                        "点击「创作」可从该商品已有资料继续发起 AI 创意任务",
                    ],
                },
                {
                    "id": "material",
                    "label": "素材库",
                    "icon": "🖼️",
                    "title": "素材库使用",
                    "summary": "统一管理可复用的商品图片与参考文档，创意任务与商品库均可引用。",
                    "bullets": [
                        "支持本地上传图片、视频、Excel、TXT、PDF、Word 等格式",
                        "图片/视频/文本自动分类；「我的素材夹」可自定义文件夹整理素材",
                        "支持单文件或 ZIP 下载到本地；创意任务可从素材夹筛选导入",
                        "创意任务完成后，本次用过的文件会自动归档到素材库",
                    ],
                },
                {
                    "id": "history",
                    "label": "历史记录",
                    "icon": "📋",
                    "title": "历史与导出",
                    "summary": "每次成功生成内容后，任务会自动存档，方便回溯与打开内容包。",
                    "bullets": [
                        "历史记录保存任务名称、创意方向、内容包路径等关键信息",
                        "可在导出页下载 ZIP 或打开「创意导出」文件夹获取完整 Markdown 与 manifest",
                        "商品库「详情」中可查看并编辑历次创意任务的总结内容",
                    ],
                },
                {
                    "id": "faq",
                    "label": "常见问题",
                    "icon": "💡",
                    "title": "常见问题",
                    "summary": "遇到问题时，可先尝试以下排查方式。",
                    "faqs": [
                        {
                            "q": "页面提示 405 或接口未更新？",
                            "a": "请关闭网站窗口，重新运行「启动网站.bat」，并在浏览器中 Ctrl+F5 强制刷新。",
                        },
                        {
                            "q": "AI 分析结果不理想？",
                            "a": "补充更多商品图片与参数表，在 AI 理解步骤添加修正备注，或重新上传更完整的参考资料。",
                        },
                        {
                            "q": "如何启用真实 LLM 与配图？",
                            "a": "点击 AI 设置填写服务地址、模型和密钥，保存不代表连接成功。每次分析、文案或配图须单独授权，服务商可能收费；不授权或无密钥则不外发。"
                        },
                        {
                            "q": "数据保存在哪里？",
                            "a": "默认保存在项目 workspace 目录：商品库 prod_*、素材库 material_library、导出 创意导出。",
                        },
                    ],
                },
            ],
        },
    }


@app.get("/api/demo-packs")
def demo_packs() -> list[dict[str, str]]:
    return _demo_packs()


@app.get("/api/projects")
def projects() -> dict[str, Any]:
    return {"projects": list_projects(_workspace(), _demo_packs())}


@app.get("/api/products")
def products() -> dict[str, Any]:
    items = list_products(_workspace(), _demo_packs())
    return {
        "products": items,
        "stats": {
            "total": len(items),
            "completed": sum(1 for item in items if item.get("status") in {"completed", "demo"}),
            "analyzed": sum(1 for item in items if item.get("status") == "analyzed"),
        },
    }


@app.post("/api/products")
def create_product_api(payload: CreateProductRequest) -> dict[str, Any]:
    workspace = _workspace()
    try:
        created = create_product(
            workspace,
            {
                "product_name": payload.product_name,
                "product_category": payload.product_category,
                "product_category_key": payload.product_category_key,
                "product_price": payload.product_price,
                "product_brand": payload.product_brand,
                "product_desc": payload.product_desc,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    detail = product_detail(created["product_id"], workspace, _demo_packs())
    return {"ok": True, "product_id": created["product_id"], "product": detail}


@app.get("/api/products/{product_id}")
def get_product(product_id: str) -> dict[str, Any]:
    detail = product_detail(product_id, _workspace(), _demo_packs())
    if detail is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    return detail


@app.post("/api/products/{product_id}/summary")
def save_product_summary_api(product_id: str, payload: ProductSummarySaveRequest) -> dict[str, Any]:
    if product_id.startswith("demo:"):
        raise HTTPException(status_code=400, detail="演示商品不可保存总结")
    workspace = _workspace()
    product_dir = workspace / product_id
    if not product_dir.is_dir():
        raise HTTPException(status_code=404, detail="商品不存在")
    sections = [
        {"id": section.id, "title": section.title or "", "body": section.body}
        for section in payload.sections
    ]
    save_product_summary(product_dir, sections)
    detail = product_detail(product_id, workspace, _demo_packs())
    if detail is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    return {"ok": True, "summary": detail.get("summary")}


@app.post("/api/products/{product_id}/save")
def save_product_detail_api(product_id: str, payload: ProductDetailSaveRequest) -> dict[str, Any]:
    if product_id.startswith("demo:"):
        raise HTTPException(status_code=400, detail="演示商品不可保存")
    workspace = _workspace()
    product_dir = workspace / product_id
    if not product_dir.is_dir():
        raise HTTPException(status_code=404, detail="商品不存在")
    profile = save_product_profile(product_dir, payload.profile.model_dump())
    sections = [
        {"id": section.id, "title": section.title or "", "body": section.body}
        for section in payload.sections
    ]
    save_product_summary(product_dir, sections)
    detail = product_detail(product_id, workspace, _demo_packs())
    if detail is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    return {"ok": True, "profile": profile, "summary": detail.get("summary"), "product": detail}


@app.post("/api/products/{product_id}/files")
async def upload_product_files(product_id: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    if product_id.startswith("demo:"):
        raise HTTPException(status_code=400, detail="演示商品不可上传文件")
    workspace = _workspace()
    product_dir = workspace / product_id
    if not product_dir.is_dir():
        raise HTTPException(status_code=404, detail="商品不存在")
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
    saved_paths: list[Path] = []
    for upload in files:
        content = await upload.read()
        if not content:
            continue
        try:
            saved_paths.append(save_product_file(product_dir, upload.filename or "file.bin", content))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if not saved_paths:
        raise HTTPException(status_code=400, detail="未收到有效的文件")
    SESSION.register_root(product_dir)
    return {
        "ok": True,
        "added_count": len(saved_paths),
        "files": list_product_files(product_dir),
    }


@app.post("/api/products/{product_id}/files/delete")
def delete_product_file_api(product_id: str, payload: ProductFileDeleteRequest) -> dict[str, Any]:
    if product_id.startswith("demo:"):
        raise HTTPException(status_code=400, detail="演示商品不可删除文件")
    workspace = _workspace()
    product_dir = workspace / product_id
    if not product_dir.is_dir():
        raise HTTPException(status_code=404, detail="商品不存在")
    try:
        delete_product_file(product_dir, payload.relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "files": list_product_files(product_dir)}


@app.delete("/api/products/{product_id}")
def delete_product(product_id: str) -> dict[str, Any]:
    workspace = _workspace()
    try:
        deleted = delete_workspace_product(workspace, product_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if SESSION.analysis and getattr(SESSION.analysis.understanding, "product_id", None) == product_id:
        SESSION.analysis = None
        SESSION.publish = None
    return {"ok": True, "product_id": product_id, "deleted_path": str(deleted)}


@app.post("/api/pick-folder")
async def pick_folder(payload: PickFolderRequest) -> dict[str, Any]:
    initial = payload.initial_dir or (str(SESSION.material_path) if SESSION.material_path else None)
    selected = await asyncio.to_thread(pick_folder_dialog, initial)
    if not selected:
        return {"path": None}
    folder = Path(selected)
    if payload.target == "library":
        previous_material = SESSION.material_path
        library = _material_library_root()
        sources = [
            path
            for path in folder.rglob("*")
            if path.is_file() and is_reference_file(path.name) and not path.name.startswith(".")
        ]
        if not sources:
            raise HTTPException(status_code=400, detail="文件夹内没有可分析的参考资料")
        try:
            copied = copy_reference_files_into_material(library, sources)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        SESSION.register_root(library)
        record_library_files(library, copied, source="folder")
        _restore_task_material_path(previous_material)
        catalog = _build_library_catalog(
            library,
            added_count=len(copied),
            added_files=[path.name for path in copied],
        )
        return {"path": str(library), **catalog}
    SESSION.set_material(folder)
    SESSION.register_root(folder)
    return {"path": selected, **_material_catalog_payload(folder)}


def _stage_image_paths(
    source_paths: list[Path],
    *,
    force_new_folder: bool = False,
) -> dict[str, Any]:
    workspace = _workspace()
    SESSION.register_root(workspace)
    material_root = resolve_image_staging_root(
        SESSION.material_path,
        workspace,
        force_new=force_new_folder,
    )
    try:
        copied = copy_images_into_material(material_root, source_paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    SESSION.set_material(material_root)
    SESSION.register_root(material_root)
    ensure_material_brief_xlsx(material_root, SESSION.product_form)
    catalog = _material_catalog_payload(material_root)
    return {
        "material_path": str(material_root),
        "added_count": len(copied),
        "added_files": [path.name for path in copied],
        **catalog,
    }


@app.post("/api/materials/confirm")
def confirm_materials(payload: SelectReferenceImagesRequest = SelectReferenceImagesRequest()) -> dict[str, Any]:
    if SESSION.material_path is None or not SESSION.material_path.is_dir():
        raise HTTPException(status_code=400, detail="请先上传或选择参考资料")
    relative_paths = payload.relative_paths or None
    confirmed = _sync_confirmed_images(SESSION.material_path, relative_paths)
    catalog = _material_catalog_payload(SESSION.material_path)
    if not catalog.get("materials_ready"):
        raise HTTPException(status_code=400, detail="未找到可分析的参考资料，请先上传文件")
    return {
        "ok": True,
        "count": len(confirmed),
        "reference_images": catalog["reference_images"],
        **catalog,
    }


@app.post("/api/select-reference-images")
def select_reference_images(payload: SelectReferenceImagesRequest) -> dict[str, Any]:
    if SESSION.material_path is None or not SESSION.material_path.is_dir():
        raise HTTPException(status_code=400, detail="请先选择素材文件夹")
    confirmed = _sync_confirmed_images(SESSION.material_path, payload.relative_paths)
    if not confirmed and not payload.relative_paths:
        confirmed = _sync_confirmed_images(SESSION.material_path)
    catalog = _material_catalog_payload(SESSION.material_path)
    if payload.relative_paths and not confirmed:
        raise HTTPException(status_code=400, detail="未找到有效的图片，请从素材文件夹中重新选择")
    return {
        "ok": True,
        "reference_images": catalog["reference_images"],
        "count": len(confirmed),
        **catalog,
    }


@app.post("/api/reference-images/add")
def add_reference_images(payload: SelectReferenceImagesRequest) -> dict[str, Any]:
    if SESSION.material_path is None or not SESSION.material_path.is_dir():
        raise HTTPException(status_code=400, detail="请先选择素材文件夹")
    current = list(SESSION.reference_image_paths)
    for relative_path in payload.relative_paths:
        if len(current) >= 10:
            break
        if relative_path in current:
            continue
        if _image_payload(SESSION.material_path, relative_path) is not None:
            current.append(relative_path)
    SESSION.reference_image_paths = current
    catalog = _material_catalog_payload(SESSION.material_path)
    return {
        "ok": True,
        "reference_images": catalog["reference_images"],
        "count": len(current),
        **catalog,
    }


@app.post("/api/task-materials/delete")
def delete_task_material(payload: DeleteMaterialRequest) -> dict[str, Any]:
    if SESSION.material_path is None or not SESSION.material_path.is_dir():
        raise HTTPException(status_code=400, detail="请先上传参考资料")
    catalog = _delete_material_file(SESSION.material_path, payload.relative_path)
    normalized = str(payload.relative_path or "").replace("\\", "/").strip().lstrip("/")
    SESSION.library_imported_paths = [
        path for path in SESSION.library_imported_paths if path != normalized
    ]
    return {"ok": True, "deleted_path": payload.relative_path, **catalog}


@app.post("/api/reference-images/remove")
def remove_reference_image(payload: ReferenceImageRemoveRequest) -> dict[str, Any]:
    if SESSION.material_path is None or not SESSION.material_path.is_dir():
        raise HTTPException(status_code=400, detail="请先选择素材文件夹")
    catalog = _delete_material_file(SESSION.material_path, payload.relative_path)
    return {
        "ok": True,
        "reference_images": catalog["reference_images"],
        "count": len(SESSION.reference_image_paths),
        **catalog,
    }


@app.post("/api/pick-images")
async def pick_images(payload: PickImagesRequest) -> dict[str, Any]:
    initial = payload.initial_dir or (str(SESSION.material_path) if SESSION.material_path else None)
    selected = await asyncio.to_thread(pick_image_files_dialog, initial)
    if not selected:
        return {"material_path": str(SESSION.material_path) if SESSION.material_path else None, "added_count": 0}
    return _stage_image_paths(
        [Path(item) for item in selected],
        force_new_folder=payload.force_new_folder,
    )


@app.post("/api/pick-files")
async def pick_files(payload: PickFilesRequest) -> dict[str, Any]:
    initial = payload.initial_dir or (str(SESSION.material_path) if SESSION.material_path else None)
    selected = await asyncio.to_thread(pick_reference_files_dialog, initial)
    if not selected:
        return {
            "material_path": str(SESSION.material_path) if SESSION.material_path else None,
            "added_count": 0,
        }
    return _stage_reference_paths(
        [Path(item) for item in selected],
        force_new_folder=payload.force_new_folder,
        target=payload.target,
    )


@app.get("/api/material-library")
def material_library() -> dict[str, Any]:
    library = _material_library_root()
    SESSION.register_root(library)
    return _build_library_catalog(library)


@app.post("/api/material-library/upload")
async def upload_material_library(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    return await _save_files_to_library(files, source="upload")


@app.post("/api/material-library/delete")
def delete_library_material(payload: DeleteMaterialRequest) -> dict[str, Any]:
    previous_material = SESSION.material_path
    library = _material_library_root()
    try:
        delete_material_relative_file(library, payload.relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    remove_library_file(library, payload.relative_path)
    remove_path_from_all_folders(library, payload.relative_path)
    SESSION.register_root(library)
    _restore_task_material_path(previous_material)
    catalog = _build_library_catalog(library)
    return {"ok": True, "deleted_path": payload.relative_path, **catalog}


@app.post("/api/material-library/rename")
def rename_library_material(payload: RenameMaterialRequest) -> dict[str, Any]:
    previous_material = SESSION.material_path
    library = _material_library_root()
    try:
        new_path = rename_library_file(library, payload.relative_path, payload.new_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    rename_path_in_all_folders(library, payload.relative_path, new_path)
    SESSION.reference_image_paths = [
        new_path if path == payload.relative_path else path
        for path in SESSION.reference_image_paths
    ]
    SESSION.register_root(library)
    _restore_task_material_path(previous_material)
    catalog = _build_library_catalog(library)
    return {
        "ok": True,
        "old_path": payload.relative_path,
        "new_path": new_path,
        **catalog,
    }


@app.post("/api/creative-task/reset")
def reset_creative_task() -> dict[str, Any]:
    SESSION.reset_creative_task()
    return {"ok": True}


@app.get("/api/material-library/download")
def download_library_material(relative_path: str = Query(..., min_length=1)) -> FileResponse:
    library = _material_library_root()
    try:
        target = resolve_library_download_file(library, relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return FileResponse(target, filename=target.name)


@app.get("/api/material-library/download-zip")
def download_library_material_zip(
    folder_id: str | None = Query(default=None),
) -> FileResponse:
    library = _material_library_root()
    folder_name: str | None = None
    relative_paths: list[str] | None = None
    if folder_id:
        folder = next(
            (item for item in list_material_folders(library) if item.get("id") == folder_id),
            None,
        )
        if folder is None:
            raise HTTPException(status_code=404, detail="素材夹不存在")
        folder_name = str(folder.get("name") or "")
        relative_paths = list(folder.get("paths") or [])
    try:
        zip_path = build_material_library_zip(library, relative_paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return FileResponse(
        zip_path,
        filename=zip_download_filename(folder_name),
        media_type="application/zip",
    )


@app.post("/api/material-library/folders")
def create_library_folder(payload: MaterialFolderCreateRequest) -> dict[str, Any]:
    library = _material_library_root()
    try:
        folder = create_material_folder(library, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "folder": folder, "folders": list_material_folders(library)}


@app.delete("/api/material-library/folders/{folder_id}")
def delete_library_folder(folder_id: str) -> dict[str, Any]:
    library = _material_library_root()
    if not delete_material_folder(library, folder_id):
        raise HTTPException(status_code=404, detail="文件夹不存在")
    return {"ok": True, "folders": list_material_folders(library)}


@app.post("/api/material-library/folders/{folder_id}/add")
def add_library_folder_paths(folder_id: str, payload: MaterialFolderPathsRequest) -> dict[str, Any]:
    library = _material_library_root()
    try:
        folder = add_paths_to_folder(library, folder_id, payload.relative_paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "folder": folder, "folders": list_material_folders(library)}


@app.post("/api/material-library/folders/{folder_id}/sync")
def sync_library_folder_paths(folder_id: str, payload: MaterialFolderPathsRequest) -> dict[str, Any]:
    library = _material_library_root()
    try:
        folder = sync_folder_paths(library, folder_id, payload.relative_paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "folder": folder, "folders": list_material_folders(library)}


@app.post("/api/material-library/folders/{folder_id}/remove")
def remove_library_folder_path(folder_id: str, payload: MaterialFolderRemovePathRequest) -> dict[str, Any]:
    library = _material_library_root()
    try:
        folder = remove_path_from_folder(library, folder_id, payload.relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "folder": folder, "folders": list_material_folders(library)}


@app.post("/api/import-library-files")
def import_library_files(payload: ImportLibraryFilesRequest) -> dict[str, Any]:
    if not payload.relative_paths:
        raise HTTPException(status_code=400, detail="请选择至少一个素材")
    library = _material_library_root()
    workspace = _workspace()
    material_root = resolve_image_staging_root(SESSION.material_path, workspace, force_new=not SESSION.material_path)
    try:
        copied = copy_relative_files_between_roots(library, material_root, payload.relative_paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    material_resolved = material_root.resolve()
    for path in copied:
        try:
            relative = path.relative_to(material_resolved).as_posix()
        except ValueError:
            continue
        if relative not in SESSION.library_imported_paths:
            SESSION.library_imported_paths.append(relative)
    SESSION.set_material(material_root)
    SESSION.register_root(material_root)
    ensure_material_brief_xlsx(material_root, SESSION.product_form)
    _sync_confirmed_images(material_root)
    return _material_catalog_payload(material_root) | {
        "material_path": str(material_root),
        "added_count": len(copied),
        "added_files": [path.name for path in copied],
    }


@app.post("/api/upload-images")
async def upload_images(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    return await upload_reference_files(files, target="task")


@app.post("/api/upload-files")
async def upload_reference_files(
    files: list[UploadFile] = File(...),
    target: str = Query(default="task"),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
    if target == "library":
        return await _save_files_to_library(files, source="upload")
    workspace = _workspace()
    SESSION.register_root(workspace)
    material_root = resolve_image_staging_root(SESSION.material_path, workspace)
    saved_paths: list[Path] = []
    for upload in files:
        content = await upload.read()
        if not content:
            continue
        try:
            saved_paths.append(
                save_reference_file(material_root, upload.filename or "file.bin", content)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if not saved_paths:
        raise HTTPException(status_code=400, detail="未收到有效的参考资料文件")
    SESSION.set_material(material_root)
    SESSION.register_root(material_root)
    ensure_material_brief_xlsx(material_root, SESSION.product_form)
    _sync_confirmed_images(material_root)
    catalog = _material_catalog_payload(
        material_root,
        added_count=len(saved_paths),
        added_files=[path.name for path in saved_paths],
    )
    return catalog


@app.post("/api/open-folder")
def open_folder(payload: OpenFolderRequest) -> dict[str, str]:
    target = Path(payload.path).resolve()
    if not SESSION.is_allowed_path(target) and not target.is_dir():
        if SESSION.material_path and target == SESSION.material_path:
            pass
        elif SESSION.publish and SESSION.publish.export and target == SESSION.publish.export.package_root:
            pass
        elif not target.exists():
            raise HTTPException(status_code=404, detail="路径不存在")
        elif not any(
            str(target).startswith(str(root))
            for root in [SESSION.workspace_path, SESSION.material_path]
            if root is not None
        ):
            raise HTTPException(status_code=403, detail="无权打开该路径")
    try:
        open_folder_in_explorer(target)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="路径不存在")
    return {"ok": True, "path": str(target)}


@app.post("/api/material-preview")
def material_preview(payload: PreviewRequest) -> dict[str, Any]:
    root = resolve_material_root(payload.material_path)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不存在：{root}")
    SESSION.set_material(root)
    return _material_catalog_payload(root)


@app.get("/api/materials")
def materials() -> dict[str, Any]:
    if SESSION.material_path is None:
        library = _material_library_root()
        payload = _material_catalog_payload(library)
        payload["material_path"] = str(library)
        payload["library_path"] = str(library)
        return payload
    payload = _material_catalog_payload(SESSION.material_path)
    payload["library_path"] = str(_material_library_root())
    return payload


@app.get("/api/material-file")
def material_file(path: str) -> FileResponse:
    candidate = Path(path).resolve()
    if (candidate == CONFIG_PATH.resolve() or candidate.name.startswith(".ai-settings-")
            or candidate.name == CAPABILITY_CONFIG_FILENAME
            or candidate.name.startswith(".ai-capabilities-")):
        raise HTTPException(status_code=403, detail="私有配置不可下载")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not SESSION.is_allowed_path(candidate):
        workspace = _workspace()
        if not str(candidate).startswith(str(workspace.resolve())):
            raise HTTPException(status_code=403, detail="无权访问该文件")
    return FileResponse(candidate)


@app.post("/api/product-form")
def save_product_form(payload: ProductFormRequest) -> dict[str, Any]:
    SESSION.product_form = {
        "product_name": payload.product_name,
        "product_category": payload.product_category,
        "product_category_key": payload.product_category_key,
        "product_price": payload.product_price,
        "product_desc": payload.product_desc,
        "product_brand": payload.product_brand,
    }
    return {"ok": True, "product_form": SESSION.product_form}


@app.get("/api/product-form")
def get_product_form() -> dict[str, Any]:
    return {"product_form": SESSION.product_form, "understanding_notes": SESSION.understanding_notes}


@app.post("/api/understanding-notes")
def save_understanding_notes(payload: UnderstandingNotesRequest) -> dict[str, Any]:
    SESSION.understanding_notes = payload.notes.strip()
    return {"ok": True, "understanding_notes": SESSION.understanding_notes}


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest) -> dict[str, Any]:
    material_root = resolve_material_root(payload.material_path)
    if not material_root.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不存在：{material_root}")
    workspace = Path(payload.workspace_path) if payload.workspace_path else default_workspace_root()
    SESSION.set_material(material_root)
    SESSION.workspace_path = workspace.resolve()
    SESSION.register_root(SESSION.workspace_path)
    if payload.product_form:
        SESSION.product_form = payload.product_form
    elif not SESSION.product_form:
        SESSION.product_form = {}

    ensure_material_brief_xlsx(material_root, SESSION.product_form)
    bundle = run_analysis(material_root, workspace, product_form=SESSION.product_form)
    SESSION.analysis = bundle
    SESSION.publish = None
    SESSION.context = dict(bundle.context)
    if bundle.ok:
        _save_run_meta(bundle, str(material_root))
    return analysis_payload(bundle)


@app.post("/api/regenerate-directions")
def regenerate_directions(payload: CloudRequest = CloudRequest()) -> dict[str, Any]:
    if SESSION.material_path is None:
        raise HTTPException(status_code=400, detail="请先选择素材文件夹")
    bundle = run_analysis(SESSION.material_path, _workspace(), product_form=SESSION.product_form)
    SESSION.analysis = bundle
    SESSION.publish = None
    SESSION.context = dict(bundle.context)
    if bundle.ok:
        _save_run_meta(bundle, str(SESSION.material_path))
    return analysis_payload(bundle)


def _ensure_publish_context() -> None:
    if SESSION.analysis is None or not SESSION.analysis.ok:
        raise HTTPException(status_code=400, detail="请先完成分析")
    if not isinstance(SESSION.context, dict) or "generate_directions" not in SESSION.context:
        if SESSION.analysis.context:
            SESSION.context = dict(SESSION.analysis.context)
    if not isinstance(SESSION.context, dict) or "generate_directions" not in SESSION.context:
        raise HTTPException(status_code=400, detail="分析会话已失效，请返回第二步重新分析")


@app.post("/api/publish")
def publish(payload: PublishRequest) -> dict[str, Any]:
    _ensure_publish_context()
    if _selected_direction(payload.direction_id) is None:
        raise HTTPException(status_code=400, detail="所选创意方向无效，请返回上一步重新选择")
    publish_bundle = run_publish(
        SESSION.context,
        selected_direction_id=payload.direction_id,
        workspace_root=SESSION.workspace_path,
    )
    SESSION.publish = publish_bundle
    SESSION.context = dict(publish_bundle.context)
    SESSION.selected_direction_id = payload.direction_id
    SESSION.image_prompts = []
    SESSION.generated_media = {}
    archive_result: dict[str, Any] | None = None
    if publish_bundle.ok and publish_bundle.export:
        export = publish_bundle.export
        understanding = SESSION.analysis.understanding if SESSION.analysis else None
        try:
            archive_result = finalize_creative_task(
                _workspace(),
                product_id=export.product_id if export else "",
                product_name=export.product_name if export else "未命名商品",
                material_path=str(SESSION.material_path) if SESSION.material_path else None,
                reference_paths=list(SESSION.reference_image_paths),
                product_form=SESSION.product_form,
                understanding=understanding.to_dict() if understanding else None,
                judgment=SESSION.analysis.judgment.to_dict()
                if SESSION.analysis and SESSION.analysis.judgment
                else None,
                directions=SESSION.analysis.directions.to_dict()
                if SESSION.analysis and SESSION.analysis.directions
                else None,
                package_root=export.package_root if export else Path("."),
                package_id=export.package_id if export else "",
                exclude_relative_paths=list(SESSION.library_imported_paths),
            )
        except OSError:
            archive_result = None
        SESSION.creative_archive = archive_result
        try:
            _archive_current_task(payload.direction_id)
        except OSError:
            pass
    return publish_payload(publish_bundle, archive=archive_result)


@app.post("/api/regenerate-content")
def regenerate_content(payload: PublishRequest) -> dict[str, Any]:
    return publish(payload)


@app.post("/api/load-project")
def load_project(payload: LoadProjectRequest) -> dict[str, Any]:
    detail = product_detail(payload.product_id, _workspace(), _demo_packs())
    if detail is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    material_path = detail.get("material_path")
    if not material_path:
        raise HTTPException(status_code=400, detail="该项目没有可用的素材路径")
    root = resolve_material_root(material_path)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"素材路径不存在：{root}")
    SESSION.set_material(root)
    SESSION.product_form = (detail.get("run_meta") or {}).get("product_form") or {}
    response: dict[str, Any] = {
        "material_path": str(root),
        "product": detail,
        "catalog": _material_catalog_payload(root),
    }
    if payload.rerun_analysis:
        bundle = run_analysis(root, _workspace(), product_form=SESSION.product_form)
        SESSION.analysis = bundle
        SESSION.publish = None
        SESSION.context = dict(bundle.context)
        if bundle.ok:
            _save_run_meta(bundle, str(root))
        response["analysis"] = analysis_payload(bundle)
    else:
        response["analysis"] = {
            "ok": True,
            "source": "history",
            "ai_analysis": {"source": "history"},
            "direction_source": "history",
            "understanding": detail.get("understanding"),
            "judgment": detail.get("judgment"),
            "directions": detail.get("directions"),
        }
    return response


@app.post("/api/generate-image-prompts")
def generate_image_prompts_endpoint() -> dict[str, Any]:
    if SESSION.publish is None or not SESSION.publish.ok:
        raise HTTPException(status_code=400, detail="请先生成创意内容")
    if SESSION.material_path is None:
        raise HTTPException(status_code=400, detail="请先选择素材文件夹")
    direction = _selected_direction()
    if direction is None:
        raise HTTPException(status_code=400, detail="未找到所选创意方向")
    understanding = SESSION.analysis.understanding if SESSION.analysis else None
    ai_analysis = None
    if SESSION.context:
        understand_payload = SESSION.context.get("understand_product")
        if isinstance(understand_payload, dict):
            ai_analysis = understand_payload.get("ai_analysis")
    media_dir = _export_media_dir()
    image_names = [Path(path).name for path in SESSION.reference_image_paths]
    visual_hint = ""
    if isinstance(ai_analysis, dict):
        keywords = ai_analysis.get("visual_keywords") or []
        if keywords:
            visual_hint = "、".join(str(k) for k in keywords[:4])
        facts = ai_analysis.get("product_facts") or {}
        if isinstance(facts, dict):
            for key in ("颜色", "材质"):
                if facts.get(key):
                    visual_hint += f" {key}{facts[key]}"
    prompts = build_image_prompts(
        product_name=SESSION.product_form.get("product_name")
        or (understanding.product_name if understanding else "商品"),
        direction_name=direction.name,
        main_selling_point=direction.main_selling_point or "",
        scenes=list(direction.scenes or []),
        avoid_expression=direction.avoid_expression or "",
        brand=SESSION.product_form.get("product_brand") or "品牌",
        image_names=image_names,
        visual_hint=visual_hint.strip(),
    )
    json_path = save_image_prompts(media_dir, prompts)
    SESSION.image_prompts = [item.to_dict() for item in prompts]
    _archive_current_task()
    return {
        "ok": True,
        "image_prompts": SESSION.image_prompts,
        "files": [
            media_payload(json_path, media_dir),
            media_payload(json_path.with_suffix(".md"), media_dir),
        ],
    }


@app.get("/api/media-job")
def media_job_status() -> dict[str, Any]:
    job = _get_image_job()
    if job.get("status") == "idle":
        return {"status": "idle"}
    if job.get("status") == "running" and job.get("started_at"):
        job = dict(job)
        job["elapsed_seconds"] = round(time.time() - float(job["started_at"]), 1)
    return job


@app.post("/api/generate-media")
def generate_media(payload: GenerateMediaRequest) -> dict[str, Any]:
    if SESSION.publish is None or not SESSION.publish.ok:
        raise HTTPException(status_code=400, detail="请先生成创意内容")
    if SESSION.material_path is None:
        raise HTTPException(status_code=400, detail="请先选择素材文件夹")
    if payload.generate_image or payload.generate_video:
        if not SESSION.image_prompts:
            generate_image_prompts_endpoint()
    media_dir = _export_media_dir()
    result: dict[str, Any] = {
        "ok": True,
        "image_prompts": SESSION.image_prompts,
        "generated_media": SESSION.generated_media,
        "ffmpeg_available": ffmpeg_available(),
    }
    if payload.generate_image:
        image_paths = resolve_video_images(SESSION.material_path, SESSION.reference_image_paths)
        if not image_paths:
            raise HTTPException(status_code=400, detail="请至少上传一张参考图用于图片生成")
        prompt = (payload.image_prompt or "").strip()
        if not prompt and SESSION.image_prompts:
            prompt = str(SESSION.image_prompts[0].get("prompt") or "")
        if not prompt:
            raise HTTPException(status_code=400, detail="请输入图片生成提示词")
        negative = (payload.negative_prompt or "").strip()
        if not negative and SESSION.image_prompts:
            negative = str(SESSION.image_prompts[0].get("negative_prompt") or "")
        job = _get_image_job()
        if job.get("status") == "running":
            raise HTTPException(status_code=409, detail="图片正在生成中，请稍候")
        image_cfg = _resolve_image_config()
        _set_image_job({"status": "running", "message": "正在启动图片生成…"})
        job_ctx = copy_context()
        threading.Thread(
            target=lambda: job_ctx.run(
                _run_image_generation_job,
                media_dir=media_dir,
                image_paths=image_paths,
                prompt=prompt,
                negative=negative,
                image_cfg=image_cfg,
            ),
            daemon=True,
        ).start()
        result["status"] = "running"
        result["message"] = "已开始生成图片，请等待（百炼通常需 2–5 分钟）"
    if not payload.generate_video:
        if payload.generate_image:
            return result
        result["generated_media"] = SESSION.generated_media
        _archive_current_task()
        return result

    image_paths = resolve_video_images(SESSION.material_path, SESSION.reference_image_paths)
    if not image_paths:
        raise HTTPException(status_code=400, detail="请至少上传一张参考图用于视频生成")
    video_path = media_dir / "slideshow.mp4"
    video_prompt = (payload.video_prompt or "").strip()
    if not video_prompt and SESSION.image_prompts:
        video_prompt = str(SESSION.image_prompts[0].get("prompt") or "")
    if not video_prompt:
        raise HTTPException(status_code=400, detail="请输入视频生成提示词")
    video_cfg = _resolve_video_config()
    video_source = "ffmpeg_slideshow"
    try:
        if video_cfg.enabled:
            output = generate_video_from_prompt(
                prompt=video_prompt,
                output_mp4=video_path,
                reference_images=image_paths,
                config=video_cfg,
            )
            video_source = "ai_video"
            SESSION.generated_media["video"] = media_payload(output, media_dir)
            result["message"] = "GPT 视频已生成"
        else:
            raise AIMediaError("视频生成未配置")
    except (AIMediaError, RuntimeError):
        try:
            output = generate_slideshow_video(image_paths, video_path, caption=video_prompt)
            SESSION.generated_media["video"] = media_payload(output, media_dir)
            result["message"] = (
                "视频已生成（本地合成）" + ("（含提示词字幕）" if video_prompt else "")
            )
        except RuntimeError as exc:
            storyboard_dir = media_dir / "storyboard"
            fallback = save_storyboard_fallback(image_paths, storyboard_dir)
            SESSION.generated_media["storyboard"] = {
                "name": fallback.name,
                "relative_path": fallback.relative_to(media_dir).as_posix(),
                "path": str(fallback),
                "message": str(exc),
            }
            result["message"] = str(exc)
            result["fallback"] = True
    result["video_source"] = video_source
    SESSION.generated_media["video_source"] = video_source
    result["generated_media"] = SESSION.generated_media
    _archive_current_task()
    return result


@app.get("/api/material-asset")
def material_asset(rel: str) -> FileResponse:
    if SESSION.material_path is None:
        raise HTTPException(status_code=400, detail="请先选择素材文件夹")
    rel_path = unquote(rel).replace("\\", "/").lstrip("/")
    if not rel_path or ".." in rel_path:
        raise HTTPException(status_code=400, detail="文件路径无效")
    material_root = SESSION.material_path.resolve()
    candidate = (material_root / rel_path).resolve()
    try:
        candidate.relative_to(material_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="无权访问该文件")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(candidate)


@app.get("/api/package-media")
def package_media(file: str) -> FileResponse:
    if not file or ".." in file.replace("\\", "/"):
        raise HTTPException(status_code=400, detail="文件路径无效")
    rel = unquote(file).replace("\\", "/").lstrip("/")
    media_dir = _resolve_media_dir()
    candidate = (media_dir / rel).resolve()
    try:
        candidate.relative_to(media_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="无权访问该文件")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(candidate)


@app.get("/api/history-media")
def history_media(task_id: str, file: str) -> FileResponse:
    if not task_id or not file:
        raise HTTPException(status_code=400, detail="参数无效")
    try:
        candidate = resolve_history_media_file(_workspace(), task_id, file)
    except ValueError:
        raise HTTPException(status_code=400, detail="文件路径无效")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(candidate)


@app.get("/api/product-media")
def product_media(product_id: str, file: str) -> FileResponse:
    if not product_id or not file:
        raise HTTPException(status_code=400, detail="参数无效")
    try:
        candidate = resolve_product_media_file(_workspace(), product_id, file)
    except ValueError:
        raise HTTPException(status_code=400, detail="文件路径无效")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(candidate)


@app.get("/api/media-file")
def media_file(path: str) -> FileResponse:
    candidate = Path(unquote(path)).resolve()
    if (candidate == CONFIG_PATH.resolve() or candidate.name.startswith(".ai-settings-")
            or candidate.name == CAPABILITY_CONFIG_FILENAME
            or candidate.name.startswith(".ai-capabilities-")):
        raise HTTPException(status_code=403, detail="私有配置不可下载")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not SESSION.is_allowed_path(candidate):
        export = SESSION.publish.export if SESSION.publish else None
        if export and export.package_root.exists():
            try:
                candidate.relative_to(export.package_root.resolve())
            except ValueError:
                raise HTTPException(status_code=403, detail="无权访问该文件")
        else:
            raise HTTPException(status_code=403, detail="无权访问该文件")
    return FileResponse(candidate)


@app.get("/api/history")
def history(limit: int = 50) -> dict[str, Any]:
    items = list_history(_workspace(), limit=limit)
    return {"items": items, "count": len(items)}


@app.get("/api/history/{task_id}")
def history_detail(task_id: str) -> dict[str, Any]:
    item = get_history_item(_workspace(), task_id)
    if item is None:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return {**item, "source": "history"}


@app.post("/api/history/archive")
def history_archive() -> dict[str, Any]:
    archived = _archive_current_task()
    if archived is None:
        raise HTTPException(status_code=400, detail="当前没有可存档的任务")
    return {"ok": True, "record": archived}


@app.delete("/api/history/{task_id}")
def delete_history(task_id: str) -> dict[str, Any]:
    try:
        deleted = delete_history_item(_workspace(), task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    if SESSION.last_history_id == task_id.removesuffix(".json") or SESSION.last_history_id == task_id:
        SESSION.last_history_id = None
    return {"ok": True, "id": task_id.removesuffix(".json")}


@app.get("/api/export/files")
def export_files() -> dict[str, Any]:
    export = SESSION.publish.export if SESSION.publish else None
    if export is None or not export.package_root.exists():
        raise HTTPException(status_code=400, detail="暂无可导出的内容包")
    return {
        "package_root": str(export.package_root),
        "export_status": export.export_status,
        "files": list_export_files(export.package_root),
    }


@app.get("/api/export/download")
def export_download(path: str) -> FileResponse:
    export = SESSION.publish.export if SESSION.publish else None
    if export is None or not export.package_root.exists():
        raise HTTPException(status_code=400, detail="暂无可导出的内容包")
    try:
        target = resolve_export_file(export.package_root, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="非法文件路径")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(target, filename=target.name)


@app.get("/api/creative-exports/download")
def download_creative_export(name: str = Query(..., min_length=1)) -> FileResponse:
    export_root = (_workspace() / "创意导出").resolve()
    candidate = (export_root / Path(name).name).resolve()
    try:
        candidate.relative_to(export_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法导出路径")
    if not candidate.is_file() or candidate.suffix.lower() != ".zip":
        raise HTTPException(status_code=404, detail="导出文件不存在")
    return FileResponse(candidate, filename=candidate.name, media_type="application/zip")


@app.get("/api/export/zip")
def export_zip() -> FileResponse:
    export = SESSION.publish.export if SESSION.publish else None
    if export is None or not export.package_root.exists():
        raise HTTPException(status_code=400, detail="暂无可导出的内容包")
    zip_path = build_export_zip(export.package_root)
    return FileResponse(zip_path, filename=f"{export.package_id}.zip", media_type="application/zip")


@app.get("/api/state")
def state() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "material_path": str(SESSION.material_path) if SESSION.material_path else None,
        "workspace_path": str(SESSION.workspace_path) if SESSION.workspace_path else None,
        "product_form": SESSION.product_form,
        "understanding_notes": SESSION.understanding_notes,
        "selected_direction_id": SESSION.selected_direction_id,
        "image_prompts": SESSION.image_prompts,
        "generated_media": SESSION.generated_media,
        "last_history_id": SESSION.last_history_id,
        "reference_images": _material_catalog_payload(SESSION.material_path)["reference_images"]
        if SESSION.material_path
        else [],
        "analysis": None,
        "publish": None,
    }
    if SESSION.analysis is not None:
        payload["analysis"] = analysis_payload(SESSION.analysis)
    if SESSION.publish is not None:
        payload["publish"] = publish_payload(SESSION.publish, archive=SESSION.creative_archive)
    if SESSION.material_path:
        payload["catalog"] = _material_catalog_payload(SESSION.material_path)
    return payload


def analysis_payload(bundle) -> dict[str, Any]:
    return {
        "ok": bundle.ok,
        "message": bundle.message,
        "stopped_at": bundle.stopped_at,
        "understanding": bundle.understanding.to_dict() if bundle.understanding else None,
        "judgment": bundle.judgment.to_dict() if bundle.judgment else None,
        "directions": bundle.directions.to_dict() if bundle.directions else None,
        "understanding_notes": SESSION.understanding_notes,
        "product_form": SESSION.product_form,
        "ai_analysis": (bundle.context.get("understand_product") or {}).get("ai_analysis") or {"source": "offline"},
        "direction_source": "local_blueprint",
    }


def publish_payload(bundle, archive: dict[str, Any] | None = None) -> dict[str, Any]:
    export = bundle.export
    package_root = str(export.package_root) if export and export.package_root else None
    files = (
        list_export_files(export.package_root)
        if export and export.package_root and export.package_root.exists()
        else []
    )
    payload: dict[str, Any] = {
        "ok": bundle.ok,
        "message": bundle.message,
        "content": bundle.content.to_dict() if bundle.content else None,
        "export": {
            **export.to_dict(),
            "package_root": package_root,
            "files": files,
        }
        if export
        else None,
    }
    if archive:
        payload["archive"] = archive
    return payload


@app.get("/api/creative-exports")
def get_creative_exports() -> dict[str, Any]:
    workspace = _workspace()
    items = list_creative_exports(workspace)
    export_root = workspace / "创意导出"
    return {
        "export_root": str(export_root),
        "items": items,
        "count": len(items),
    }


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
