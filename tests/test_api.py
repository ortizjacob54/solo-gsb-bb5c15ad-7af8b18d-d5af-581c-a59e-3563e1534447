"""端到端 API 测试（通过 FastAPI TestClient）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.geometry import signed_area
from app.main import app

client = TestClient(app)


def square(cx: float = 5.0, cy: float = 5.0, half: float = 5.0) -> list[dict]:
    return [
        {"x": cx - half, "y": cy - half},
        {"x": cx + half, "y": cy - half},
        {"x": cx + half, "y": cy + half},
        {"x": cx - half, "y": cy + half},
    ]


# ---------- 健康检查 ----------


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "laser-kerf-compensation"
    assert "geos_version" in body


# ---------- 基本补偿 ----------


def test_basic_outer_and_hole_offset() -> None:
    # 外轮廓 10x10，孔洞 4x4；刀缝 2 -> 单侧偏移 1
    req = {
        "kerf_width": 2.0,
        "parts": [
            {
                "id": "p1",
                "outer": {"id": "o1", "points": square()},
                "holes": [{"id": "h1", "points": square(5, 5, 2)}],
            }
        ],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["offset"] == 1.0

    part = body["parts"][0]
    outer = part["outer"]
    hole = part["holes"][0]

    # 外扩 1 -> 12x12；孔洞内缩 1 -> 2x2
    assert outer["area"] == pytest.approx(144.0, abs=1e-6)
    assert hole["area"] == pytest.approx(4.0, abs=1e-6)

    # 绕向规范化：外 CCW，孔 CW
    assert outer["orientation"] == "CCW"
    assert hole["orientation"] == "CW"

    # 返回的是闭合轨迹点列（不重复末点，>=4 点）
    assert len(outer["points"]) == 4
    assert outer["points"][0] != outer["points"][-1]

    # 孔洞归属保持
    assert hole["part_index"] == 0
    assert hole["source_contour_index"] == 0
    assert outer["source_contour_index"] == -1


def test_holes_stay_inside_their_part() -> None:
    req = {
        "kerf_width": 1.0,
        "parts": [
            {
                "id": "A",
                "outer": {"points": square(0, 0, 10)},
                "holes": [
                    {"id": "h-a", "points": [
                        {"x": -8, "y": -8}, {"x": -4, "y": -8},
                        {"x": -4, "y": -4}, {"x": -8, "y": -4},
                    ]},
                ],
            },
            {
                "id": "B",
                "outer": {"points": square(30, 30, 10)},
                "holes": [
                    {"id": "h-b", "points": [
                        {"x": 22, "y": 22}, {"x": 26, "y": 22},
                        {"x": 26, "y": 26}, {"x": 22, "y": 26},
                    ]},
                ],
            },
        ],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 200, r.text
    parts = r.json()["parts"]
    assert len(parts) == 2
    assert parts[0]["id"] == "A"
    assert parts[0]["holes"][0]["id"] == "h-a"
    assert parts[1]["holes"][0]["id"] == "h-b"


# ---------- 排序 ----------


def test_parts_and_holes_sorted_stably() -> None:
    req = {
        "kerf_width": 1.0,
        "parts": [
            {
                "id": "zeta",
                "outer": {"points": square(0, 0, 10)},
                "holes": [
                    {"id": "z-hole", "points": square(0, 0, 2)},
                    {"id": "a-hole", "points": square(3, 0, 1.5)},
                ],
            },
            {
                "id": "alpha",
                "outer": {"points": square(30, 30, 10)},
                "holes": [],
            },
            {
                # 无 id 的零件按原始下标排到有 id 之后
                "outer": {"points": square(60, 60, 10)},
                "holes": [],
            },
        ],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 200, r.text
    parts = r.json()["parts"]
    assert [p["id"] for p in parts] == ["alpha", "zeta", None]
    # 原始下标不被排序破坏
    assert [p["part_index"] for p in parts] == [1, 0, 2]
    assert [h["id"] for h in parts[1]["holes"]] == ["a-hole", "z-hole"]


# ---------- 整单拒绝 ----------


def test_self_intersecting_outer_is_rejected_and_located() -> None:
    # 蝴蝶结
    bowtie = [
        {"x": 0, "y": 0}, {"x": 4, "y": 4},
        {"x": 4, "y": 0}, {"x": 0, "y": 4},
    ]
    req = {
        "kerf_width": 1.0,
        "parts": [
            {"id": "good", "outer": {"points": square(30, 30, 10)}, "holes": []},
            {"id": "bad", "outer": {"id": "bow", "points": bowtie}, "holes": []},
        ],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "SELF_INTERSECTING_CONTOUR"
    loc = err["location"]
    assert loc["part_index"] == 1
    assert loc["contour_index"] == -1
    assert loc["part_id"] == "bad"
    assert loc["contour_id"] == "bow"


def test_self_intersecting_hole_is_located_by_hole_index() -> None:
    bowtie = [
        {"x": 0, "y": 0}, {"x": 2, "y": 2},
        {"x": 2, "y": 0}, {"x": 0, "y": 2},
    ]
    req = {
        "kerf_width": 1.0,
        "parts": [
            {
                "id": "p",
                "outer": {"points": square()},
                "holes": [
                    {"id": "ok-hole", "points": square(5, 5, 1)},
                    {"id": "bow-hole", "points": bowtie},
                ],
            }
        ],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 422
    loc = r.json()["error"]["location"]
    assert loc["part_index"] == 0
    assert loc["contour_index"] == 1
    assert loc["contour_id"] == "bow-hole"


def test_hole_vanishes_rejects_whole_order() -> None:
    # 1x1 孔洞内缩 0.6*2? -> kerf=1.2 offset=.6，对边距 < 1.2 -> 消失
    req = {
        "kerf_width": 1.2,
        "parts": [
            {
                "id": "p1",
                "outer": {"points": square()},
                "holes": [{"id": "tiny", "points": square(5, 5, 0.5)}],
            },
            {"id": "p2", "outer": {"points": square(30, 30, 10)}, "holes": []},
        ],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "CONTOUR_VANISHED"
    assert err["location"] == {
        "part_index": 0,
        "contour_index": 0,
        "part_id": "p1",
        "contour_id": "tiny",
    }


def test_narrow_hole_degenerates_and_is_rejected_as_vanished() -> None:
    # 10x2 条带孔洞，内缩 1 后退化为线（空面）-> CONTOUR_VANISHED
    strip = [
        {"x": 2, "y": 8}, {"x": 12, "y": 8},
        {"x": 12, "y": 10}, {"x": 2, "y": 10},
    ]
    req = {
        "kerf_width": 2.0,
        "parts": [
            {
                "id": "p",
                "outer": {"points": square(7, 5, 20)},
                "holes": [{"id": "strip", "points": strip}],
            }
        ],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "CONTOUR_VANISHED"
    assert err["location"]["contour_id"] == "strip"


def test_hole_splits_rejects_whole_order() -> None:    # 哑铃形孔洞：两个 4x4 凸耳，2 宽通道；内缩 1.1 必然分裂
    dumbbell = [
        (0, 0), (4, 0), (4, 1), (6, 1), (6, 0), (10, 0),
        (10, 4), (6, 4), (6, 3), (4, 3), (4, 4), (0, 4),
    ]
    req = {
        "kerf_width": 2.2,
        "parts": [
            {
                "id": "p",
                "outer": {"points": square(10, 5, 20)},
                "holes": [{"id": "dumb", "points": [{"x": x, "y": y} for x, y in dumbbell]}],
            }
        ],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "CONTOUR_SPLIT"
    assert err["location"]["contour_index"] == 0
    assert err["location"]["contour_id"] == "dumb"


# ---------- 请求校验 ----------


def test_invalid_kerf_width() -> None:
    req = {"kerf_width": 0, "parts": [{"outer": {"points": square()}}]}
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 422


def test_too_few_points() -> None:
    req = {
        "kerf_width": 1.0,
        "parts": [{"outer": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 0}]}}],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 422


def test_consecutive_duplicate_points() -> None:
    pts = square()
    pts[1] = pts[0]
    req = {"kerf_width": 1.0, "parts": [{"outer": {"points": pts}}]}
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 422


def test_implicitly_closed_input_is_accepted() -> None:
    # 不重复末点即可（square() 即如此）
    req = {"kerf_width": 1.0, "parts": [{"outer": {"points": square()}}]}
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 200


def test_extra_field_rejected() -> None:
    req = {
        "kerf_width": 1.0,
        "parts": [{"outer": {"points": square()}, "unexpected": 1}],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 422


# ---------- 输出闭合性与几何性质 ----------


def test_output_rings_are_simple_closed() -> None:
    from shapely.geometry import LinearRing

    req = {
        "kerf_width": 1.0,
        "parts": [
            {
                "outer": {
                    "points": [
                        {"x": 0, "y": 0}, {"x": 10, "y": 0},
                        {"x": 10, "y": 8}, {"x": 6, "y": 8},
                        {"x": 6, "y": 4}, {"x": 3, "y": 4},
                        {"x": 3, "y": 10}, {"x": 0, "y": 10},
                    ]
                },
                "holes": [{"points": square(2, 2, 1)}],
            }
        ],
    }
    r = client.post("/api/v1/kerf-compensation", json=req)
    assert r.status_code == 200, r.text
    part = r.json()["parts"][0]
    for ring_out in [part["outer"], *part["holes"]]:
        coords = [(p["x"], p["y"]) for p in ring_out["points"]]
        ring = LinearRing(coords)  # 不抛异常即说明可闭合
        assert ring.is_valid
        # 面积符号与声明绕向一致
        area = signed_area(coords)
        if ring_out["role"] == "outer":
            assert area > 0
        else:
            assert area < 0


def test_openapi_available() -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/health" in r.json()["paths"]
