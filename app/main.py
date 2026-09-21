"""激光切割刀缝补偿 API。

启动: ``uvicorn app.main:app``（容器内默认监听 0.0.0.0:8000）。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .config import settings
from .errors import KerfError
from .geometry import library_versions, process_request
from .schemas import KerfRequest, KerfResponse

app = FastAPI(
    title="激光切割刀缝补偿 API",
    description=(
        "对外轮廓向外、孔洞向内偏移刀缝宽度的一半，返回闭合刀具中心轨迹；"
        "输入自交或偏移导致轮廓消失/分裂时整单拒绝。"
    ),
    version=__version__,
)


@app.exception_handler(KerfError)
async def kerf_error_handler(_request: Request, exc: KerfError) -> JSONResponse:
    """业务错误：统一 422 信封，含错误码与原轮廓定位。"""
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "location": exc.to_location(),
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """请求体结构错误同样返回统一信封（HTTP 422）。"""
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "请求体不符合接口规范",
                "location": {"details": exc.errors()},
            }
        },
    )


@app.get("/health", tags=["health"], summary="健康检查")
async def health() -> dict:
    """存活/就绪探针：返回服务状态与版本信息。"""
    return {
        "status": "ok",
        "service": "kerf-compensation-api",
        "version": __version__,
        "libraries": library_versions(),
    }


@app.post(
    "/api/v1/kerf/offset",
    response_model=KerfResponse,
    tags=["kerf"],
    summary="刀缝补偿：生成闭合刀具中心轨迹",
)
async def offset_kerf(payload: KerfRequest) -> KerfResponse:
    """整单计算刀缝补偿轨迹；任意轮廓失败则整单 422 拒绝。"""
    return process_request(payload, max_points=settings.max_contour_points)
