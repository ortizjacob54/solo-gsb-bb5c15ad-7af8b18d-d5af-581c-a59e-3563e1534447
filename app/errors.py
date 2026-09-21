"""应用专属异常与错误码。

所有几何语义错误（自交、轮廓消失、轮廓分裂等）都抛出
:class:`CompensationError`，由 FastAPI 统一转换为 422 响应。
按需求，出现任一错误时整单拒绝，绝不返回部分轨迹。
"""

from __future__ import annotations


class ErrorCode:
    SELF_INTERSECTING = "SELF_INTERSECTING_CONTOUR"
    CONTOUR_VANISHED = "CONTOUR_VANISHED"
    CONTOUR_SPLIT = "CONTOUR_SPLIT"
    INVALID_RESULT = "INVALID_OFFSET_RESULT"


# 面向调用方的中文错误信息
_MESSAGES = {
    ErrorCode.SELF_INTERSECTING: "输入轮廓自交，整单拒绝",
    ErrorCode.CONTOUR_VANISHED: "刀缝偏移后轮廓消失，整单拒绝",
    ErrorCode.CONTOUR_SPLIT: "刀缝偏移后轮廓分裂为多个闭合环，整单拒绝",
    ErrorCode.INVALID_RESULT: "刀缝偏移结果无效，整单拒绝",
}


class CompensationError(Exception):
    """携带出错轮廓定位信息的补偿错误。

    ``part_index`` / ``contour_index`` 始终基于请求 JSON 中的原始下标，
    方便调用方直接定位到原始零件与轮廓。
    """

    def __init__(
        self,
        code: str,
        *,
        part_index: int,
        contour_index: int,
        part_id: str | None = None,
        contour_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        message = _MESSAGES.get(code, "刀缝补偿失败，整单拒绝")
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.code = code
        self.message = message
        self.part_index = part_index
        self.contour_index = contour_index
        self.part_id = part_id
        self.contour_id = contour_id

    def to_response(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "location": {
                    "part_index": self.part_index,
                    "contour_index": self.contour_index,
                    "part_id": self.part_id,
                    "contour_id": self.contour_id,
                },
            }
        }
