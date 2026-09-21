"""请求 / 响应 Pydantic 模型。"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Point(BaseModel):
    x: float
    y: float

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, v: float) -> float:
        # NaN / Infinity 不是合法 JSON 数字，几何上也无意义
        if not math.isfinite(v):
            raise ValueError("坐标必须为有限数字")
        return v


class Contour(BaseModel):
    """一条闭合轮廓（外轮廓或孔洞）。

    点按顺序首尾相连，服务端自动闭合（首尾重复与不重复均可）。
    至少需要 3 个点；首尾点以外的连续重复点会被拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, description="可选轮廓标识，便于定位")
    points: list[Point] = Field(min_length=3)

    @field_validator("points")
    @classmethod
    def _no_consecutive_duplicates(cls, points: list[Point]) -> list[Point]:
        for i in range(1, len(points)):
            if points[i].x == points[i - 1].x and points[i].y == points[i - 1].y:
                raise ValueError("轮廓存在连续重复点")
        # 首尾重复闭合点也视为连续重复（末点与首点）
        if len(points) >= 2:
            first, last = points[0], points[-1]
            if first.x == last.x and first.y == last.y:
                raise ValueError("轮廓首尾点重复，请勿显式重复闭合点")
        return points


class Part(BaseModel):
    """一个零件：一条外轮廓 + 若干孔洞。"""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, description="可选零件标识")
    outer: Contour
    holes: list[Contour] = Field(default_factory=list)


class CompensationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kerf_width: float = Field(
        ...,
        gt=0,
        description="刀缝宽度（与坐标同单位），实际偏移量为其一半",
    )
    parts: list[Part] = Field(min_length=1)


class RingOut(BaseModel):
    """一条闭合刀具中心轨迹。"""

    id: str | None = None
    role: Literal["outer", "hole"]
    # 所属零件下标（原始请求下标），保持孔洞归属
    part_index: int
    # 该轮廓在所属零件原始请求中的下标：外轮廓恒为 -1；孔洞为 holes 下标
    source_contour_index: int
    points: list[Point]
    orientation: Literal["CCW", "CW"]
    area: float = Field(description="轨迹包围面积（无符号，便于客户端校验）")


class PartOut(BaseModel):
    id: str | None = None
    part_index: int
    outer: RingOut
    holes: list[RingOut]


class CompensationResponse(BaseModel):
    kerf_width: float
    offset: float = Field(description="实际单侧偏移量 = kerf_width / 2")
    parts: list[PartOut]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    geos_version: str
