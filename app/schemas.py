"""请求/响应数据模型。

输入使用显式的 :class:`Point`（而非裸 ``[x, y]`` 数组），同时附带坐标下标，
便于错误信息精确定位到顶点。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Point(BaseModel):
    """二维点。

    ``index`` 是该点在所属轮廓 ``points`` 数组中的下标，由服务端在反序列化
    后回填，请求方无需提供。
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(..., description="X 坐标")
    y: float = Field(..., description="Y 坐标")
    index: int | None = Field(default=None, exclude=True)

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, v: float) -> float:
        # 拒绝 NaN / Infinity（float("nan") != 自身）
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("坐标必须为有限数值")
        return v


class Contour(BaseModel):
    """一条轮廓（外轮廓或孔洞），顶点按顺序排列；闭合由系统保证。"""

    model_config = ConfigDict(extra="forbid")

    points: list[Point] = Field(..., min_length=3, description="轮廓顶点，按顺序排列，无需重复首点闭合")


class Hole(Contour):
    """孔洞轮廓。"""


class Part(BaseModel):
    """单个零件：一条外轮廓 + 若干孔洞。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., ge=0, description="零件唯一业务 id（非负整数），用于稳定排序")
    outer: Contour = Field(..., description="零件外轮廓")
    holes: list[Hole] = Field(default_factory=list, description="孔洞轮廓列表")


class KerfRequest(BaseModel):
    """刀缝补偿请求。"""

    model_config = ConfigDict(extra="forbid")

    kerf_width: float = Field(
        ...,
        gt=0,
        description="刀缝宽度（> 0）；轮廓偏移量为其一半",
    )
    parts: list[Part] = Field(..., min_length=1, description="待切割零件列表（至少 1 个）")


class OutputContour(BaseModel):
    """响应中的一条闭合刀具中心轨迹环。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["outer", "hole"] = Field(..., description="环角色：外轮廓偏移结果或孔洞偏移结果")
    hole_index: int | None = Field(
        default=None,
        description="所属孔洞在零件 holes 数组中的下标；role=outer 时为 null",
    )
    source_vertex_count: int = Field(..., description="原轮廓顶点数（闭合重复点归一化后）")
    points: list[Point] = Field(..., description="闭合刀具中心轨迹顶点（已闭合，末点等于首点）")


class OutputPart(BaseModel):
    """单个零件的偏移结果，保持其孔洞归属。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="零件 id")
    contours: list[OutputContour] = Field(..., description="刀具轨迹环：外轮廓在前，其后按孔洞下标排列")


class KerfResponse(BaseModel):
    """刀缝补偿响应。整单成功或整单失败，不存在部分结果。"""

    model_config = ConfigDict(extra="forbid")

    kerf_width: float
    offset: float = Field(..., description="实际单边偏移量（kerf_width 的一半）")
    parts: list[OutputPart] = Field(..., description="零件结果，按零件 id 升序稳定排序")


class ErrorResponse(BaseModel):
    """统一错误信封。"""

    model_config = ConfigDict(extra="forbid")

    error: "ErrorBody"


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    location: dict = Field(default_factory=dict)


ErrorResponse.model_rebuild()
