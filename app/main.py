"""激光切割刀缝补偿 HTTP 服务。"""

from __future__ import annotations

import shapely
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .errors import CompensationError
from .geometry import process_part, sort_key
from .schemas import (
    CompensationRequest,
    CompensationResponse,
    HealthResponse,
)

SERVICE_NAME = "laser-kerf-compensation"

app = FastAPI(
    title="激光切割刀缝补偿 API",
    description=(
        "接收由外轮廓与孔洞组成的零件及刀缝宽度，"
        "外轮廓向外、孔洞向内偏移刀缝宽度的一半，返回闭合刀具中心轨迹。"
        "任一原始轮廓自交，或偏移后消失、分裂时，整单拒绝并定位原轮廓。"
    ),
    version="1.0.0",
)


@app.exception_handler(CompensationError)
async def compensation_error_handler(_: Request, exc: CompensationError) -> JSONResponse:
    # 422 Unprocessable Entity：请求可解析，但几何语义不满足处理条件
    return JSONResponse(status_code=422, content=exc.to_response())


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """健康检查。"""
    return HealthResponse(status="ok", service=SERVICE_NAME, geos_version=shapely.geos_version_string)


@app.post(
    "/api/v1/kerf-compensation",
    response_model=CompensationResponse,
    tags=["compensation"],
    responses={
        422: {
            "description": "几何语义错误：自交 / 轮廓消失 / 轮廓分裂，整单拒绝",
            "content": {
                "application/json": {
                    "example": {
                        "error": {
                            "code": "CONTOUR_SPLIT",
                            "message": "刀缝偏移后轮廓分裂为多个闭合环，整单拒绝",
                            "location": {
                                "part_index": 0,
                                "contour_index": 1,
                                "part_id": "bracket",
                                "contour_id": "slot",
                            },
                        }
                    }
                }
            },
        }
    },
)
async def kerf_compensation(req: CompensationRequest) -> CompensationResponse:
    """对外轮廓外扩、孔洞内缩 ``kerf_width / 2``，返回闭合刀具中心轨迹。

    - 孔洞始终嵌在所属零件下返回（保持归属）；
    - 零件按 ``id`` 字典序稳定排序（无 id 的按原输入顺序排在后）；
      零件内部孔洞同理；
    - 任何一条轮廓不满足条件，整单拒绝，不返回部分轨迹。
    """
    offset = req.kerf_width / 2.0

    # 先全部处理成功，再统一排序输出；处理阶段抛错即整单拒绝。
    processed = [
        process_part(part, part_index=i, offset=offset)
        for i, part in enumerate(req.parts)
    ]

    # 稳定排序：零件级
    processed.sort(key=lambda p: sort_key(p["id"], p["part_index"]))
    # 稳定排序：零件内孔洞级
    for p in processed:
        p["holes"].sort(
            key=lambda h: sort_key(h["id"], h["source_contour_index"])
        )

    return CompensationResponse(
        kerf_width=req.kerf_width,
        offset=offset,
        parts=processed,
    )
