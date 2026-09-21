"""刀缝补偿的几何核心（基于 Shapely / GEOS）。

补偿规则
--------
- 外轮廓向**外**偏移 ``kerf_width / 2``；
- 孔洞向**内**偏移（孔洞自身面积缩小）``kerf_width / 2``。

实现方式不依赖输入环的绕向：把每条轮廓构造为一个 ``Polygon``，
外轮廓用正向 buffer、孔洞用负向 buffer。GEOS 会自动保证外扩 / 内缩方向。

失败策略（整单拒绝）
--------------------
偏移前若环自交，或偏移结果为空（轮廓消失）、为 MultiPolygon / GeometryCollection
（轮廓分裂）、或结果几何无效，立即抛出 :class:`CompensationError`，
调用方据此返回错误，不输出任何部分轨迹。
"""

from __future__ import annotations

from shapely.geometry import (
    LinearRing,
    Polygon,
)
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from .errors import CompensationError, ErrorCode
from .schemas import Contour, Part

# 尖角使用斜接（mitre），匹配常见激光切割 CAM 的刀路风格；
# mitre_limit 限制极尖角处斜接长度，避免产生异常长的尖刺。
_JOIN_STYLE = "mitre"
_MITRE_LIMIT = 2.0


def _coords(contour: Contour) -> list[tuple[float, float]]:
    return [(p.x, p.y) for p in contour.points]


def signed_area(coords: list[tuple[float, float]]) -> float:
    """鞋带公式求有符号面积；正值为 CCW（数学坐标系，y 向上）。"""
    s = 0.0
    n = len(coords)
    for i in range(n):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def build_ring(
    contour: Contour,
    *,
    part_index: int,
    contour_index: int,
    part_id: str | None,
) -> LinearRing:
    """构造并校验线性环，自交时抛出带定位的 ``CompensationError``。"""
    ring = LinearRing(_coords(contour))
    if not ring.is_valid:
        # 典型原因："Ring Self-intersection[... ]"
        reason = explain_validity(ring)
        raise CompensationError(
            ErrorCode.SELF_INTERSECTING,
            part_index=part_index,
            contour_index=contour_index,
            part_id=part_id,
            contour_id=contour.id,
            detail=reason,
        )
    return ring


def _offset_polygon(polygon: Polygon, distance: float) -> BaseGeometry:
    """对单环多边形做单侧偏移。

    distance > 0 外扩；distance < 0 内缩。
    单连通多边形外扩在拓扑上不可能分裂；内缩则可能消失或分裂，
    因此结果类型必须在调用方校验。
    """
    return polygon.buffer(
        distance,
        quad_segs=16,
        join_style=_JOIN_STYLE,
        mitre_limit=_MITRE_LIMIT,
    )


def offset_ring(
    ring: LinearRing,
    *,
    role: str,
    offset: float,
    part_index: int,
    contour_index: int,
    part_id: str | None,
    contour_id: str | None,
) -> LinearRing:
    """按角色偏移一条环，返回规范化后的新环。

    外轮廓：``Polygon(ring).buffer(+offset)``，取外边界。
    孔洞：``Polygon(ring).buffer(-offset)``，取缩小后的外边界。
    """
    polygon = Polygon(ring)
    if not polygon.is_valid:
        # 理论上 valid 的简单环构造的多边形也有效，这里双保险。
        raise CompensationError(
            ErrorCode.SELF_INTERSECTING,
            part_index=part_index,
            contour_index=contour_index,
            part_id=part_id,
            contour_id=contour_id,
            detail=explain_validity(polygon),
        )

    distance = offset if role == "outer" else -offset

    try:
        result = _offset_polygon(polygon, distance)
    except Exception as exc:  # GEOS 层面的意外错误
        raise CompensationError(
            ErrorCode.INVALID_RESULT,
            part_index=part_index,
            contour_index=contour_index,
            part_id=part_id,
            contour_id=contour_id,
            detail=f"GEOS 偏移失败: {exc}",
        ) from exc

    geom_type = result.geom_type

    if result.is_empty or (geom_type == "Polygon" and result.exterior.length == 0):
        raise CompensationError(
            ErrorCode.CONTOUR_VANISHED,
            part_index=part_index,
            contour_index=contour_index,
            part_id=part_id,
            contour_id=contour_id,
        )

    if geom_type != "Polygon":
        # 偏移结果不再是“面”：
        # - MultiPolygon / GeometryCollection：轮廓分裂，按需求拒绝；
        # - LineString / Point（孔洞内缩到退化为线 / 点）：视为轮廓消失。
        if geom_type in ("MultiPolygon", "GeometryCollection"):
            code = ErrorCode.CONTOUR_SPLIT
            detail = f"偏移结果类型为 {geom_type}，不是唯一闭合轮廓"
        else:
            code = ErrorCode.CONTOUR_VANISHED
            detail = f"偏移后退化为 {geom_type}，闭合轮廓消失"
        raise CompensationError(
            code,
            part_index=part_index,
            contour_index=contour_index,
            part_id=part_id,
            contour_id=contour_id,
            detail=detail,
        )

    assert isinstance(result, Polygon)

    if not result.is_valid:
        raise CompensationError(
            ErrorCode.INVALID_RESULT,
            part_index=part_index,
            contour_index=contour_index,
            part_id=part_id,
            contour_id=contour_id,
            detail=explain_validity(result),
        )

    # 外扩理论上可能因形状极度凹陷产生内部环；出现即说明拓扑不再是单一边界，
    # 不能作为一条刀具中心轨迹返回，按分裂拒绝。
    if len(list(result.interiors)) > 0:
        raise CompensationError(
            ErrorCode.CONTOUR_SPLIT,
            part_index=part_index,
            contour_index=contour_index,
            part_id=part_id,
            contour_id=contour_id,
            detail="外扩结果产生内部环，拓扑不再是单一闭合轮廓",
        )

    return result.exterior


def normalize_ring(
    ring: LinearRing,
    *,
    role: str,
    reference: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], str, float]:
    """规范化输出环。

    1. 绕向：外轮廓 CCW，孔洞 CW；
    2. 起点：旋转到离原轮廓首点最近的点，便于和输入对照；
    3. 返回 (点列, 绕向标记, 无符号面积)，点列不重复末点。
    """
    coords = list(ring.coords)
    # LinearRing 首尾重复，去掉末点
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]

    area = signed_area(coords)
    want_ccw = role == "outer"
    if (area > 0) != want_ccw:
        coords.reverse()
        area = -area

    # 反转后再以原始首点为锚旋转（反转不影响最近点选择逻辑）
    anchor = reference[0]
    best_i = min(
        range(len(coords)),
        key=lambda i: (coords[i][0] - anchor[0]) ** 2
        + (coords[i][1] - anchor[1]) ** 2,
    )
    coords = coords[best_i:] + coords[:best_i]

    return coords, ("CCW" if want_ccw else "CW"), abs(area)


def sort_key(ident: str | None, index: int) -> tuple:
    """稳定排序键：有 id 按 id 字典序，无 id 靠原始下标；同 id 再按下标。

    排序完全确定（deterministic），且不改变同 id / 无 id 元素的相对次序语义。
    """
    if ident is None:
        # 无 id 元素排后，并以原始下标保证唯一与稳定
        return (1, "", index)
    return (0, ident, index)


def process_part(part: Part, part_index: int, offset: float) -> dict:
    """处理单个零件，返回可序列化为 ``PartOut`` 的 dict。

    任何一条轮廓失败都会抛出 ``CompensationError``（整单拒绝语义）。
    """
    part_id = part.id

    # ---- 先统一构造并校验全部输入环，再做偏移 ----
    outer_ring = build_ring(
        part.outer,
        part_index=part_index,
        contour_index=-1,
        part_id=part_id,
    )
    hole_rings = [
        build_ring(
            hole,
            part_index=part_index,
            contour_index=i,
            part_id=part_id,
        )
        for i, hole in enumerate(part.holes)
    ]

    # ---- 偏移 ----
    outer_offset = offset_ring(
        outer_ring,
        role="outer",
        offset=offset,
        part_index=part_index,
        contour_index=-1,
        part_id=part_id,
        contour_id=part.outer.id,
    )
    hole_offsets = [
        offset_ring(
            ring,
            role="hole",
            offset=offset,
            part_index=part_index,
            contour_index=i,
            part_id=part_id,
            contour_id=part.holes[i].id,
        )
        for i, ring in enumerate(hole_rings)
    ]

    # ---- 规范化输出 ----
    outer_coords, outer_ori, outer_area = normalize_ring(
        outer_offset, role="outer", reference=_coords(part.outer)
    )
    holes_out: list[dict] = []
    for i, ring in enumerate(hole_offsets):
        coords, ori, area = normalize_ring(
            ring, role="hole", reference=_coords(part.holes[i])
        )
        holes_out.append(
            {
                "id": part.holes[i].id,
                "role": "hole",
                "part_index": part_index,
                "source_contour_index": i,
                "points": [{"x": x, "y": y} for x, y in coords],
                "orientation": ori,
                "area": area,
            }
        )

    return {
        "id": part_id,
        "part_index": part_index,
        "outer": {
            "id": part.outer.id,
            "role": "outer",
            "part_index": part_index,
            "source_contour_index": -1,
            "points": [{"x": x, "y": y} for x, y in outer_coords],
            "orientation": outer_ori,
            "area": outer_area,
        },
        "holes": holes_out,
    }
