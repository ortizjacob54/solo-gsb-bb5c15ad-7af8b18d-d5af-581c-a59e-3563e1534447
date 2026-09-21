"""刀缝补偿核心几何逻辑（基于 Shapely / GEOS）。

工艺语义
--------
- 外轮廓向**外**偏移 ``kerf_width / 2``，保证切去的刀缝落在坯料余量侧，
  完工后外轮廓尺寸不偏小。
- 孔洞向**内**偏移 ``kerf_width / 2``，同理保证孔洞切完后不偏大。
- 输出为闭合的**刀具中心轨迹**（环），孔洞仍归属其原零件。

实现要点：对一条闭合 ``LinearRing`` 做双侧 buffer（带状多边形），外轮廓取
带的外边界，孔洞取带的内边界。这样无需依赖输入环的绕向，偏移方向语义固定。

失败策略：任何输入自交、偏移后环消失/分裂/拓扑改变，都抛出
:class:`KerfError`，由调用方整单拒绝，绝不返回部分轨迹。
"""

from __future__ import annotations

from shapely.geometry import LinearRing, LineString, MultiPolygon, Polygon
from shapely.strtree import STRtree
from shapely import __version__ as shapely_version, geos_version

from .errors import KerfError
from .schemas import Contour, KerfRequest, KerfResponse, OutputContour, OutputPart, Part, Point

# 输出坐标保留位数（单位通常为 mm，1e-9 即纳米级，远高于机床精度）
_COORD_DECIMALS = 9

#: 环双侧 buffer 参数：尖角斜接并限制斜接长度，避免锐角产生极长尖刺
_JOIN_STYLE = "mitre"
_MITRE_LIMIT = 2.0


def library_versions() -> dict[str, str]:
    """供健康检查展示底层库版本。"""
    return {
        "shapely": shapely_version,
        "geos": ".".join(str(v) for v in geos_version),
    }


# --------------------------------------------------------------------------- #
# 输入校验与环构造
# --------------------------------------------------------------------------- #
def _find_self_intersection(ring: LinearRing) -> tuple[float, float] | None:
    """返回环的自交/自切点；简单环返回 ``None``。

    用 STRtree 对所有边段做两两相交检测；相邻边段在公共端点处的接触属于
    正常拓扑，跳过。非相邻边段的任何接触（含端点重合、重叠、穿叉）都算自交。
    """
    coords = ring.coords[:-1]  # 去掉闭合重复点
    n = len(coords)
    segments = [LineString([coords[i], coords[(i + 1) % n]]) for i in range(n)]
    tree = STRtree(segments)

    for i, seg in enumerate(segments):
        for j in tree.query(seg):
            if j <= i:
                continue
            # 相邻（含首尾相邻）边段共享端点是正常的
            if j == (i - 1) % n or j == (i + 1) % n:
                continue
            other = segments[j]
            if not seg.intersects(other):
                continue
            inter = seg.intersection(other)
            if inter.is_empty:
                continue
            # Point / MultiPoint / LineString(重叠边) 等，统一取一个代表点
            probe = inter.representative_point()
            if not probe.is_empty:
                return (probe.x, probe.y)
    return None


def build_ring(
    contour: Contour,
    *,
    part_index: int,
    part_id: int,
    role: str,
    hole_index: int | None,
    max_points: int,
) -> LinearRing:
    """把输入轮廓归一化为简单 :class:`LinearRing`，失败即定位拒绝。"""
    raw = [(p.x, p.y) for p in contour.points]
    if len(raw) > max_points:
        raise KerfError(
            "LIMIT_EXCEEDED",
            f"轮廓顶点数 {len(raw)} 超过上限 {max_points}",
            part_index=part_index,
            part_id=part_id,
            contour=role,
            hole_index=hole_index,
        )

    # 归一化：去掉连续重复点以及末尾对首点的重复闭合
    pts: list[tuple[float, float]] = []
    for p in raw:
        if not pts or p != pts[-1]:
            pts.append(p)
    while len(pts) >= 3 and pts[0] == pts[-1]:
        pts.pop()

    if len(pts) < 3:
        raise KerfError(
            "INVALID_CONTOUR",
            "有效顶点不足 3 个（去除重复点后无法构成闭合轮廓）",
            part_index=part_index,
            part_id=part_id,
            contour=role,
            hole_index=hole_index,
        )

    try:
        ring = LinearRing(pts)
    except Exception as exc:  # GEOS 拒绝（退化等）
        raise KerfError(
            "INVALID_CONTOUR",
            f"无法构造闭合轮廓: {exc}",
            part_index=part_index,
            part_id=part_id,
            contour=role,
            hole_index=hole_index,
        ) from exc

    point = _find_self_intersection(ring)
    if point is not None or not ring.is_simple:
        raise KerfError(
            "SELF_INTERSECTION",
            ("孔洞" if role == "hole" else "外轮廓") + "存在自交或自切，无法进行刀缝补偿",
            part_index=part_index,
            part_id=part_id,
            contour=role,
            hole_index=hole_index,
            point=point,
        )
    return ring


# --------------------------------------------------------------------------- #
# 偏移
# --------------------------------------------------------------------------- #
def _ring_to_output_coords(ring: LinearRing) -> list[tuple[float, float]]:
    coords = list(ring.coords)
    out: list[tuple[float, float]] = []
    for x, y in coords:
        out.append((0.0 if x == 0 else round(x, _COORD_DECIMALS), 0.0 if y == 0 else round(y, _COORD_DECIMALS)))
    return out


def offset_ring(
    ring: LinearRing,
    distance: float,
    *,
    part_index: int,
    part_id: int,
    role: str,
    hole_index: int | None,
) -> list[tuple[float, float]]:
    """将闭合环单侧偏移 ``distance``（带符号：外轮廓传正、孔洞传负）。

    返回闭合轨迹坐标（末点等于首点）。偏移结果消失、分裂或拓扑改变时抛错。
    """
    label = "孔洞" if role == "hole" else "外轮廓"
    try:
        band = ring.buffer(
            abs(distance),
            join_style=_JOIN_STYLE,
            mitre_limit=_MITRE_LIMIT,
        )
    except Exception as exc:  # GEOS 计算失败
        raise KerfError(
            "OFFSET_FAILED",
            f"{label}偏移计算失败: {exc}",
            part_index=part_index,
            part_id=part_id,
            contour=role,
            hole_index=hole_index,
        ) from exc

    def topology_error(code: str, message: str) -> KerfError:
        return KerfError(
            code,
            message,
            part_index=part_index,
            part_id=part_id,
            contour=role,
            hole_index=hole_index,
        )

    # 1) 消失：空结果或退化为点/线（孔洞内缩过头最常见）
    if band.is_empty or band.geom_type in ("Point", "MultiPoint", "LineString", "MultiLineString"):
        if role == "hole":
            raise topology_error(
                "HOLE_DISAPPEARED",
                f"{label}向内偏移 {distance * -1:g} 后轮廓消失（刀缝相对孔洞过大）",
            )
        raise topology_error(
            "OFFSET_TOPOLOGY_CHANGED",
            f"{label}偏移后退化为 {band.geom_type}，无法生成闭合刀具轨迹",
        )

    # 2) 分裂：带变成多个多边形（细腰被切断）或集合类型
    if isinstance(band, MultiPolygon) or band.geom_type in ("MultiPolygon", "GeometryCollection"):
        raise topology_error(
            "OFFSET_SPLIT",
            f"{label}偏移后分裂为多个独立轮廓（{band.geom_type}），整单拒绝",
        )

    if not isinstance(band, Polygon):  # 理论上不可达，保险
        raise topology_error("OFFSET_TOPOLOGY_CHANGED", f"{label}偏移结果类型异常: {band.geom_type}")

    # 3) 选取目标环并按角色校验拓扑：
    #    - 外轮廓取带的外边界（向外偏移环）；其内侧可能在尖锐/细小处坍缩，
    #      表现为带没有内孔，这对刀具外轨迹没有影响，允许 0~1 个内边界，
    #      多于 1 个说明外环自相吞并（分裂类拓扑改变）。
    #    - 孔洞取带的唯一内边界（向内偏移环）：0 个内边界即孔洞消失；
    #      多于 1 个说明孔洞分裂。
    if role == "outer":
        if len(band.interiors) > 1:
            raise topology_error(
                "OFFSET_SPLIT",
                f"{label}偏移后产生 {len(band.interiors)} 条内边界（期望至多 1 条），拓扑发生分裂或合并，整单拒绝",
            )
        target = band.exterior
    else:
        if len(band.interiors) == 0:
            raise topology_error(
                "HOLE_DISAPPEARED",
                f"{label}向内偏移后轮廓消失（刀缝相对孔洞过大），整单拒绝",
            )
        if len(band.interiors) > 1:
            raise topology_error(
                "OFFSET_SPLIT",
                f"{label}偏移后分裂为 {len(band.interiors)} 条独立内边界，整单拒绝",
            )
        target = band.interiors[0]

    if target.is_empty or not target.is_simple or band.area <= 0.0:
        raise topology_error(
            "OFFSET_TOPOLOGY_CHANGED",
            f"{label}偏移后的闭合轨迹无效，整单拒绝",
        )

    return _ring_to_output_coords(target)


# --------------------------------------------------------------------------- #
# 整单处理
# --------------------------------------------------------------------------- #
def process_request(request: KerfRequest, *, max_points: int) -> KerfResponse:
    """执行整单刀缝补偿。

    两阶段处理保证原子性：先构造并校验**所有**输入环，再对**所有**环做偏移；
    任一环节失败立即抛 :class:`KerfError`，不会产出部分轨迹。
    """
    # 整单级校验：零件 id 必须唯一（排序与归属依赖它）
    seen: set[int] = set()
    for idx, part in enumerate(request.parts):
        if part.id in seen:
            raise KerfError(
                "DUPLICATE_PART_ID",
                f"零件 id {part.id} 重复，无法保证稳定排序与归属",
                part_index=idx,
                part_id=part.id,
            )
        seen.add(part.id)

    offset = request.kerf_width / 2.0

    # 阶段一：全部输入环构造 + 自交校验
    prepared: list[tuple[Part, LinearRing, list[LinearRing]]] = []
    for idx, part in enumerate(request.parts):
        outer_ring = build_ring(
            part.outer,
            part_index=idx,
            part_id=part.id,
            role="outer",
            hole_index=None,
            max_points=max_points,
        )
        hole_rings = [
            build_ring(
                hole,
                part_index=idx,
                part_id=part.id,
                role="hole",
                hole_index=hi,
                max_points=max_points,
            )
            for hi, hole in enumerate(part.holes)
        ]
        prepared.append((part, outer_ring, hole_rings))

    # 阶段二：全部偏移；外轮廓 +offset（取带外边界），孔洞 -offset（取带内边界）
    output_parts: list[OutputPart] = []
    for idx, (part, outer_ring, hole_rings) in enumerate(prepared):
        outer_coords = offset_ring(
            outer_ring,
            offset,
            part_index=idx,
            part_id=part.id,
            role="outer",
            hole_index=None,
        )
        contours: list[OutputContour] = [
            OutputContour(
                role="outer",
                hole_index=None,
                source_vertex_count=len(outer_ring.coords) - 1,
                points=[Point(x=x, y=y) for x, y in outer_coords],
            )
        ]
        for hi, hole_ring in enumerate(hole_rings):
            hole_coords = offset_ring(
                hole_ring,
                -offset,
                part_index=idx,
                part_id=part.id,
                role="hole",
                hole_index=hi,
            )
            contours.append(
                OutputContour(
                    role="hole",
                    hole_index=hi,
                    source_vertex_count=len(hole_ring.coords) - 1,
                    points=[Point(x=x, y=y) for x, y in hole_coords],
                )
            )
        output_parts.append(OutputPart(id=part.id, contours=contours))

    # 零件按 id 升序稳定排序；轮廓顺序固定为 outer 在前、孔洞按原下标排列
    output_parts.sort(key=lambda p: p.id)

    return KerfResponse(kerf_width=request.kerf_width, offset=offset, parts=output_parts)
