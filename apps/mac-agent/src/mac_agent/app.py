from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from audiobook_core.errors import BookParseError
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .diagnostics import SystemDiagnostics
from .error_reporting import reporter
from .library import LocalLibrary
from .pairing import FirebasePairingProvider, PairingProvider
from .paths import AGENT_PORT, APP_VERSION, data_root
from .picker import NativeBookPicker, NativeVoicePicker
from .preview import VoicePreviewService, default_qwen_factory
from .security import DEFAULT_ALLOWED_ORIGINS, CsrfTokenStore
from .voice import VoiceError, VoiceRegistry


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_as_copy: bool = False
    rights_confirmed: bool = False


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript: str


class VoiceConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_version: str


class RepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str


class LocalGenerationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str
    chapter_ids: list[str]
    task_ids: list[str] = Field(default_factory=list)


class LocalGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_uid: str
    voice_version: str
    selections: list[LocalGenerationSelection]


class LocalQueueActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ids: list[str]
    action: str


class LocalQueueReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ids: list[str]


def create_app(
    *,
    library: LocalLibrary | None = None,
    allowed_origins: frozenset[str] = DEFAULT_ALLOWED_ORIGINS,
    tokens: CsrfTokenStore | None = None,
    pairing: PairingProvider | None = None,
    voices: VoiceRegistry | None = None,
    previews: VoicePreviewService | None = None,
    worker_model_loaded: Callable[[], bool] | None = None,
    worker: object | None = None,
    diagnostics: SystemDiagnostics | None = None,
) -> FastAPI:
    app = FastAPI(title="米兰读书 Mac Agent", docs_url=None, redoc_url=None, openapi_url=None)
    token_store = tokens or CsrfTokenStore()
    root = data_root()
    local_library = library or LocalLibrary(root / "books", NativeBookPicker())
    pairing_provider = pairing or FirebasePairingProvider.from_default_config()
    voice_registry = voices or VoiceRegistry(root / "voices", NativeVoicePicker())
    preview_service = previews or VoicePreviewService(voice_registry, default_qwen_factory)
    generation_model_loaded = worker_model_loaded or (lambda: False)
    system_diagnostics = diagnostics or SystemDiagnostics()

    def consume_csrf(request: Request) -> bool:
        origin = request.headers["origin"]
        token = request.headers.get("x-audiobook-csrf", "")
        return token_store.consume(token, origin)

    @app.middleware("http")
    async def protect_loopback_api(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        origin = request.headers.get("origin", "")
        if origin not in allowed_origins:
            return JSONResponse(
                status_code=403,
                content={"code": "ORIGIN_MISMATCH", "error": "不允许的网站来源。"},
            )
        if request.method == "OPTIONS":
            response: Response = Response(status_code=204)
        else:
            try:
                response = await call_next(request)
            except Exception as error:  # noqa: BLE001
                reporter.record(
                    f"http.{request.method.lower()} {request.url.path}",
                    error,
                    code=getattr(error, "code", "AGENT_REQUEST_FAILED"),
                )
                response = JSONResponse(
                    status_code=500,
                    content={
                        "code": "AGENT_REQUEST_FAILED",
                        "error": "Mac Agent 操作失败，完整原因已写入本机日志。",
                    },
                )
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Audiobook-CSRF"
        if request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    @app.get("/v1/health")
    async def health() -> dict[str, str | int]:
        preview_status = preview_service.status()
        return {
            "status": "ok",
            "service": "mac-agent",
            "version": APP_VERSION,
            "port": AGENT_PORT,
            "tts": "loaded" if preview_status["model_loaded"] or generation_model_loaded() else "not-loaded",
            "cloud": "configured" if pairing_provider.configured else "local-only",
        }

    @app.get("/v1/diagnostics")
    async def diagnostics_report() -> Response:
        def collect() -> dict[str, object]:
            paired: bool | None = None
            if pairing_provider.configured:
                try:
                    paired = pairing_provider.is_linked()
                except Exception as error:  # noqa: BLE001
                    reporter.record(
                        "diagnostics.pairing",
                        error,
                        code=getattr(error, "code", "FIREBASE_PAIRING_CHECK_FAILED"),
                    )
            return system_diagnostics.report(
                cloud_configured=pairing_provider.configured,
                paired=paired,
                worker=worker,
            )

        return JSONResponse(content=await run_in_threadpool(collect))

    @app.get("/v1/local-generation/status")
    async def local_generation_status() -> Response:
        status = getattr(worker, "local_status", None)
        if not callable(status):
            return JSONResponse(
                status_code=503,
                content={"code": "LOCAL_GENERATION_UNAVAILABLE", "error": "本地生成服务尚未启用。"},
            )
        return JSONResponse(content=await run_in_threadpool(status))

    @app.post("/v1/local-generation/enqueue")
    async def enqueue_local_generation(
        payload: LocalGenerationRequest,
        request: Request,
    ) -> Response:
        if not consume_csrf(request):
            return JSONResponse(
                status_code=403,
                content={"code": "CSRF_EXPIRED", "error": "操作凭证已过期，请重新点击。"},
            )
        enqueue = getattr(worker, "enqueue_local", None)
        if not callable(enqueue):
            return JSONResponse(
                status_code=503,
                content={"code": "LOCAL_GENERATION_UNAVAILABLE", "error": "本地生成服务尚未启用。"},
            )
        try:
            result = await run_in_threadpool(
                enqueue,
                payload.owner_uid,
                [selection.model_dump() for selection in payload.selections],
                payload.voice_version,
            )
        except ValueError as error:
            return JSONResponse(
                status_code=422,
                content={"code": "LOCAL_GENERATION_INVALID", "error": str(error)},
            )
        return JSONResponse(status_code=202, content=result)

    @app.post("/v1/local-generation/action")
    async def update_local_generation(
        payload: LocalQueueActionRequest,
        request: Request,
    ) -> Response:
        if not consume_csrf(request):
            return JSONResponse(
                status_code=403,
                content={"code": "CSRF_EXPIRED", "error": "操作凭证已过期，请重新点击。"},
            )
        action = getattr(worker, "local_action", None)
        if not callable(action):
            return JSONResponse(status_code=503, content={"error": "本地生成服务尚未启用。"})
        try:
            changed = await run_in_threadpool(action, payload.task_ids, payload.action)
        except ValueError as error:
            return JSONResponse(status_code=422, content={"error": str(error)})
        return JSONResponse(content={"changed": changed})

    @app.post("/v1/local-generation/reorder")
    async def reorder_local_generation(
        payload: LocalQueueReorderRequest,
        request: Request,
    ) -> Response:
        if not consume_csrf(request):
            return JSONResponse(
                status_code=403,
                content={"code": "CSRF_EXPIRED", "error": "操作凭证已过期，请重新点击。"},
            )
        reorder = getattr(worker, "local_reorder", None)
        if not callable(reorder):
            return JSONResponse(status_code=503, content={"error": "本地生成服务尚未启用。"})
        changed = await run_in_threadpool(reorder, payload.task_ids)
        return JSONResponse(content={"changed": changed})

    @app.get("/v1/local-generation/assets/{task_id}/{kind}")
    async def local_generation_asset(task_id: str, kind: str) -> Response:
        resolve = getattr(worker, "local_asset", None)
        path = await run_in_threadpool(resolve, task_id, kind) if callable(resolve) else None
        if not isinstance(path, Path) or not path.is_file():
            return JSONResponse(status_code=404, content={"error": "本地音频尚未生成完成。"})
        media_type = "audio/mp4" if kind == "audio" else "application/gzip"
        return FileResponse(path, media_type=media_type)

    @app.post("/v1/diagnostics/repair")
    async def repair_diagnostics(payload: RepairRequest, request: Request) -> Response:
        if not consume_csrf(request):
            return JSONResponse(
                status_code=403,
                content={"code": "CSRF_EXPIRED", "error": "操作凭证已过期，请重新点击。"},
            )
        try:
            return JSONResponse(status_code=202, content=system_diagnostics.start_repair(payload.action))
        except (FileNotFoundError, ValueError) as error:
            reporter.record("diagnostics.repair", error, code="AUTO_REPAIR_UNAVAILABLE")
            return JSONResponse(
                status_code=422,
                content={"code": "AUTO_REPAIR_UNAVAILABLE", "error": str(error)},
            )

    @app.get("/v1/session")
    async def session(request: Request) -> dict[str, str | int]:
        origin = request.headers["origin"]
        return {"csrf_token": token_store.issue(origin), "expires_in": token_store.ttl_seconds}

    @app.post("/v1/books/choose")
    async def choose_book(payload: ImportRequest, request: Request) -> Response:
        if not consume_csrf(request):
            return JSONResponse(status_code=403, content={"error": "操作凭证已过期，请重新点击添加书籍。"})
        try:
            book = local_library.choose_and_import(
                import_as_copy=payload.import_as_copy,
                rights_confirmed=payload.rights_confirmed,
            )
        except BookParseError as exc:
            return JSONResponse(status_code=422, content={"code": exc.code, "error": exc.message})
        except RuntimeError as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})
        if book is None:
            return Response(status_code=204)
        return JSONResponse(content=book.to_dict())

    @app.post("/v1/pairing/start")
    async def start_pairing(payload: EmptyRequest, request: Request) -> Response:
        del payload
        if not consume_csrf(request):
            return JSONResponse(status_code=403, content={"error": "操作凭证已过期，请重新连接。"})
        try:
            code = pairing_provider.start()
        except RuntimeError as exc:
            return JSONResponse(status_code=503, content={"error": str(exc)})
        return JSONResponse(content={"code": code.code, "expires_in": code.expires_in})

    @app.get("/v1/pairing/status")
    async def pairing_status() -> Response:
        if not pairing_provider.configured:
            return JSONResponse(content={"configured": False, "linked": False})
        try:
            linked = pairing_provider.is_linked()
        except RuntimeError as exc:
            return JSONResponse(status_code=503, content={"error": str(exc)})
        return JSONResponse(content={"configured": True, "linked": linked})

    @app.get("/v1/voice/status")
    async def voice_status() -> Response:
        try:
            profile = voice_registry.load()
            status = profile.public_status() if profile else {"configured": False}
            if profile:
                status["configured"] = True
            return JSONResponse(content={**status, "preview": preview_service.status()})
        except VoiceError as exc:
            return JSONResponse(status_code=422, content={"code": exc.code, "error": str(exc)})

    @app.post("/v1/voice/choose")
    async def choose_voice(payload: VoiceRequest, request: Request) -> Response:
        if not consume_csrf(request):
            return JSONResponse(status_code=403, content={"error": "操作凭证已过期，请重新点击。"})
        try:
            preview_service.unload()
            profile = voice_registry.choose_and_create(payload.transcript)
        except VoiceError as exc:
            return JSONResponse(status_code=422, content={"code": exc.code, "error": str(exc)})
        if profile is None:
            return Response(status_code=204)
        return JSONResponse(content=profile.public_status())

    @app.post("/v1/voice/preview")
    async def start_voice_preview(payload: EmptyRequest, request: Request) -> Response:
        del payload
        if not consume_csrf(request):
            return JSONResponse(status_code=403, content={"error": "操作凭证已过期，请重新点击。"})
        try:
            return JSONResponse(status_code=202, content=preview_service.start())
        except VoiceError as exc:
            return JSONResponse(status_code=422, content={"code": exc.code, "error": str(exc)})

    @app.get("/v1/voice/preview.m4a")
    async def voice_preview_audio() -> Response:
        try:
            profile = voice_registry.load()
        except VoiceError as exc:
            return JSONResponse(status_code=422, content={"code": exc.code, "error": str(exc)})
        if profile is None or not profile.preview_path or not Path(profile.preview_path).is_file():
            return JSONResponse(status_code=404, content={"error": "试听还没有生成完成。"})
        return FileResponse(profile.preview_path, media_type="audio/mp4", filename="voice-preview.m4a")

    @app.post("/v1/voice/confirm")
    async def confirm_voice(payload: VoiceConfirmRequest, request: Request) -> Response:
        if not consume_csrf(request):
            return JSONResponse(status_code=403, content={"error": "操作凭证已过期，请重新点击。"})
        try:
            profile = voice_registry.confirm(payload.voice_version)
        except VoiceError as exc:
            return JSONResponse(status_code=422, content={"code": exc.code, "error": str(exc)})
        return JSONResponse(content=profile.public_status())

    return app
