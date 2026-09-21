"""业务错误类型。

任何几何/语义校验失败都会抛出 :class:`KerfError`，由 FastAPI 异常处理器
转换为统一的 422 响应信封。整单校验在收集结果阶段进行，任意一处失败即
抛出，保证不会返回部分刀具轨迹。
"""

from __future__ import annotations


class KerfError(Exception):
    """刀缝补偿业务错误。

    Attributes:
        code: 机器可读错误码。
        message: 面向人的中文错误描述。
        part_index: 出错零件在请求 ``parts`` 数组中的下标（从 0 开始）。
            整单级错误（如零件 id 重复）时为 ``None``。
        part_id: 出错零件的业务 id；缺失时为 ``None``。
        contour: 出错轮廓标识。``"outer"`` 表示外轮廓，``"hole"`` 表示孔洞，
            整零件/整单级错误时为 ``None``。
        hole_index: 当 ``contour == "hole"`` 时，孔洞在该零件 ``holes``
            数组中的下标（从 0 开始）。
        point: 出错位置的代表性坐标（自交点、无效点等），无法定位时为 ``None``。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        part_index: int | None = None,
        part_id: int | None = None,
        contour: str | None = None,
        hole_index: int | None = None,
        point: tuple[float, float] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.part_index = part_index
        self.part_id = part_id
        self.contour = contour
        self.hole_index = hole_index
        self.point = point

    def to_location(self) -> dict:
        """转换为错误响应中的 ``location`` 结构。"""
        location: dict[str, object] = {}
        if self.part_index is not None:
            location["part_index"] = self.part_index
        if self.part_id is not None:
            location["part_id"] = self.part_id
        if self.contour is not None:
            location["contour"] = self.contour
        if self.hole_index is not None:
            location["hole_index"] = self.hole_index
        if self.point is not None:
            location["point"] = [self.point[0], self.point[1]]
        return location
