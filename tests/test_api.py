"""API 集成测试。运行： pytest（venv 内）"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def square(cx, cy, half, **extra):
    """以 (cx, cy) 为中心、半边长 half 的轴对齐正方形（逆时针）。"""
    pts = [
        {"x": cx - half, "y": cy - half},
        {"x": cx + half, "y": cy - half},
        {"x": cx + half, "y": cy + half},
        {"x": cx - half, "y": cy + half},
    ]
    d = {"points": pts}
    d.update(extra)
    return d


# --------------------------------------------------------------------------- #
# 健康检查
# --------------------------------------------------------------------------- #
def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "shapely" in body["libraries"]
    assert "geos" in body["libraries"]


# --------------------------------------------------------------------------- #
# 正常补偿
# --------------------------------------------------------------------------- #
def test_basic_offset_and_sorting():
    payload = {
        "kerf_width": 2.0,  # offset = 1.0
        "parts": [
            {"id": 7, "outer": square(0, 0, 5), "holes": [square(0, 0, 2)]},
            {"id": 3, "outer": square(20, 20, 1), "holes": []},
        ],
    }
    r = client.post("/api/v1/kerf/offset", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["offset"] == 1.0

    # 零件按 id 升序：3 在 7 前
    assert [p["id"] for p in body["parts"]] == [3, 7]

    p7 = body["parts"][1]
    # 外轮廓在前，孔洞紧随其后，归属保持
    assert [c["role"] for c in p7["contours"]] == ["outer", "hole"]
    assert p7["contours"][1]["hole_index"] == 0

    outer_pts = p7["contours"][0]["points"]
    hole_pts = p7["contours"][1]["points"]

    # 输出闭合：末点等于首点
    assert outer_pts[0] == outer_pts[-1]
    assert hole_pts[0] == hole_pts[-1]

    xs = [pt["x"] for pt in outer_pts]
    ys = [pt["y"] for pt in outer_pts]
    # 外轮廓向外偏 1：原 [-5,5] -> [-6,6]
    assert min(xs) == -6.0 and max(xs) == 6.0
    assert min(ys) == -6.0 and max(ys) == 6.0

    hxs = [pt["x"] for pt in hole_pts]
    hys = [pt["y"] for pt in hole_pts]
    # 孔洞向内偏 1：原 [-2,2] -> [-1,1]
    assert min(hxs) == -1.0 and max(hxs) == 1.0
    assert min(hys) == -1.0 and max(hys) == 1.0


def test_output_rings_are_simple_polygons():
    """偏移输出必须能还原成无自交的有效环。"""
    from shapely.geometry import LinearRing

    payload = {
        "kerf_width": 0.6,
        "parts": [
            {
                "id": 1,
                "outer": {
                    "points": [
                        {"x": 0, "y": 0},
                        {"x": 10, "y": 0},
                        {"x": 10, "y": 8},
                        {"x": 4, "y": 8},
                        {"x": 4, "y": 4},
                        {"x": 0, "y": 4},
                    ]
                },
                "holes": [square(8, 2, 1)],
            }
        ],
    }
    r = client.post("/api/v1/kerf/offset", json=payload)
    assert r.status_code == 200, r.text
    for part in r.json()["parts"]:
        for contour in part["contours"]:
            coords = [(p["x"], p["y"]) for p in contour["points"]]
            ring = LinearRing(coords)
            assert ring.is_simple and ring.is_valid


# --------------------------------------------------------------------------- #
# 拒绝场景：整单原子性
# --------------------------------------------------------------------------- #
def _err(r):
    assert r.status_code == 422, r.text
    return r.json()["error"]


def test_self_intersection_is_rejected_and_located():
    # 蝴蝶结：边 (0,0)->(2,2) 与 (2,0)->(0,2) 在 (1,1) 相交
    bowtie = {
        "points": [
            {"x": 0, "y": 0},
            {"x": 2, "y": 2},
            {"x": 2, "y": 0},
            {"x": 0, "y": 2},
        ]
    }
    r = client.post(
        "/api/v1/kerf/offset",
        json={"kerf_width": 0.2, "parts": [{"id": 1, "outer": bowtie, "holes": []}]},
    )
    err = _err(r)
    assert err["code"] == "SELF_INTERSECTION"
    loc = err["location"]
    assert loc["part_index"] == 1 - 1
    assert loc["part_id"] == 1
    assert loc["contour"] == "outer"
    assert loc["point"] == [1.0, 1.0]


def test_hole_self_intersection_is_located():
    bowtie = {
        "points": [
            {"x": 0, "y": 0},
            {"x": 4, "y": 4},
            {"x": 4, "y": 0},
            {"x": 0, "y": 4},
        ]
    }
    r = client.post(
        "/api/v1/kerf/offset",
        json={
            "kerf_width": 0.2,
            "parts": [{"id": 5, "outer": square(0, 0, 10), "holes": [bowtie]}],
        },
    )
    err = _err(r)
    assert err["code"] == "SELF_INTERSECTION"
    loc = err["location"]
    assert loc["part_index"] == 0
    assert loc["part_id"] == 5
    assert loc["contour"] == "hole"
    assert loc["hole_index"] == 0
    assert loc["point"] == [2.0, 2.0]


def test_hole_disappears_whole_order_rejected():
    # 孔洞半边长 0.4，内偏 0.5 后消失；同单的好零件也不得返回
    payload = {
        "kerf_width": 1.0,
        "parts": [
            {"id": 1, "outer": square(0, 0, 10), "holes": []},
            {"id": 2, "outer": square(30, 0, 10), "holes": [square(30, 0, 0.4)]},
        ],
    }
    r = client.post("/api/v1/kerf/offset", json=payload)
    err = _err(r)
    assert err["code"] == "HOLE_DISAPPEARED"
    loc = err["location"]
    assert loc["part_index"] == 1
    assert loc["part_id"] == 2
    assert loc["contour"] == "hole"
    assert loc["hole_index"] == 0
    # 响应体中不存在任何轨迹
    assert "parts" not in r.json()


def test_hole_offset_split_rejected():
    # “哑铃”形孔洞：两个 r=1 的圆腔通过半宽 0.2 的窄腰相连。
    # 向内偏 0.25（> 腰半宽）后内边界被切断，分裂为两条环。
    import math

    def arc(cx, cy, r, a0, a1, n):
        return [
            {"x": cx + r * math.cos(a0 + (a1 - a0) * i / n),
             "y": cy + r * math.sin(a0 + (a1 - a0) * i / n)}
            for i in range(n + 1)
        ]

    a = 0.2
    ta = math.asin(a)
    pts = [
        {"x": -2 + math.cos(math.pi - ta), "y": a},
        {"x": 2 + math.cos(math.pi - ta), "y": a},
    ]
    pts += arc(2, 0, 1, math.pi - ta, -ta, 48)[1:]
    pts.append({"x": -2 + math.cos(-ta), "y": -a})
    pts += arc(-2, 0, 1, -ta, -math.pi - ta, 48)[1:]

    r = client.post(
        "/api/v1/kerf/offset",
        json={
            "kerf_width": 0.5,
            "parts": [{"id": 9, "outer": square(0, 0, 20), "holes": [{"points": pts}]}],
        },
    )
    err = _err(r)
    assert err["code"] == "OFFSET_SPLIT"
    assert err["location"]["contour"] == "hole"
    assert err["location"]["part_id"] == 9


def test_duplicate_part_id_rejected():
    payload = {
        "kerf_width": 0.2,
        "parts": [
            {"id": 1, "outer": square(0, 0, 5), "holes": []},
            {"id": 1, "outer": square(10, 0, 5), "holes": []},
        ],
    }
    r = client.post("/api/v1/kerf/offset", json=payload)
    err = _err(r)
    assert err["code"] == "DUPLICATE_PART_ID"
    assert err["location"]["part_index"] == 1


def test_bad_kerf_width_rejected():
    r = client.post(
        "/api/v1/kerf/offset",
        json={"kerf_width": 0, "parts": [{"id": 1, "outer": square(0, 0, 5)}]},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_too_few_points_rejected():
    r = client.post(
        "/api/v1/kerf/offset",
        json={
            "kerf_width": 0.2,
            "parts": [{"id": 1, "outer": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}}],
        },
    )
    assert r.status_code == 422


def test_closed_input_contour_accepted():
    """输入显式闭合（末点 == 首点）是合法的，会被归一化。"""
    pts = square(0, 0, 5)["points"]
    pts.append(dict(pts[0]))
    r = client.post(
        "/api/v1/kerf/offset",
        json={"kerf_width": 1.0, "parts": [{"id": 1, "outer": {"points": pts}}]},
    )
    assert r.status_code == 200, r.text
    contour = r.json()["parts"][0]["contours"][0]
    # 原轮廓顶点数按归一化后的 4 计
    assert contour["source_vertex_count"] == 4


def test_non_simple_duplicate_vertex_rejected():
    # 非相邻重复顶点（A-B-C-D-B）自切
    pts = [
        {"x": 0, "y": 0},
        {"x": 4, "y": 0},
        {"x": 4, "y": 4},
        {"x": 0, "y": 4},
        {"x": 4, "y": 0},
    ]
    r = client.post(
        "/api/v1/kerf/offset",
        json={"kerf_width": 0.2, "parts": [{"id": 1, "outer": {"points": pts}}]},
    )
    assert _err(r)["code"] == "SELF_INTERSECTION"


def test_openapi_docs_available():
    assert client.get("/openapi.json").status_code == 200
