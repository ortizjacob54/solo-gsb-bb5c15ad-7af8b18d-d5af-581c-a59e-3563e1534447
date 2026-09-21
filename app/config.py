"""运行期配置（全部来自环境变量，附默认值）。"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class Settings:
    """服务配置。

    Attributes:
        host: uvicorn 监听地址。
        port: uvicorn 监听端口。
        max_contour_points: 单条轮廓允许的最大顶点数，防止超大输入拖垮 GEOS。
    """

    host: str = os.getenv("APP_HOST", "0.0.0.0")
    port: int = _env_int("APP_PORT", 8000)
    max_contour_points: int = _env_int("MAX_CONTOUR_POINTS", 100_000)


settings = Settings()
