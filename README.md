# 激光切割刀缝补偿 API

基于 **Python + FastAPI + Shapely (GEOS)** 的刀缝（kerf）补偿服务。

给定由**一条外轮廓 + 若干孔洞**组成的零件与刀缝宽度，服务计算闭合的**刀具中心轨迹**：

- 外轮廓向 **外** 偏移 `kerf_width / 2`；
- 孔洞向 **内** 偏移 `kerf_width / 2`（切出的孔尺寸与设计一致）；
- 输出保持**孔洞归属**（孔洞始终嵌在所属零件下），并对零件与轮廓**稳定排序**；
- 任一原始轮廓**自交**，或偏移导致轮廓**消失 / 分裂**时，**整单拒绝**并定位到原始零件与轮廓，
  **绝不返回部分轨迹**。

---

## 单条命令启动（Docker Compose）

```bash
docker compose up --build -d
```

依赖（FastAPI、Shapely、uvicorn 及内置 GEOS）全部构建进镜像，无需在宿主机安装任何 Python 包。

- 默认访问地址：<http://localhost:8000>
- 健康检查：<http://localhost:8000/health>
- 接口文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI JSON：<http://localhost:8000/openapi.json>

停止：

```bash
docker compose down
```

> 不用 Compose 时也可直接运行（等价）：
> `docker build -t laser-kerf-compensation . && docker run --rm -p 8000:8000 laser-kerf-compensation`

---

## 必要配置（均有默认值，零配置即可运行）

通过环境变量或 `.env` 文件配置（可复制 `.env.example` 为 `.env`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `KERF_API_PORT` | `8000` | 宿主机映射端口，容器内固定监听 8000 |
| `UVICORN_WORKERS` | `1` | uvicorn worker 进程数，CPU 密集可按核数调整 |

例：以 9000 端口、4 个 worker 启动：

```bash
KERF_API_PORT=9000 UVICORN_WORKERS=4 docker compose up --build -d
```

---

## API

### `GET /health`

健康检查，返回服务状态与 GEOS 版本：

```json
{ "status": "ok", "service": "laser-kerf-compensation", "geos_version": "3.12.1-CAPI-1.18.1" }
```

### `POST /api/v1/kerf-compensation`

**请求体**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kerf_width` | number, `> 0` | 刀缝宽度，实际单侧偏移量为其一半 |
| `parts[]` | array, 至少 1 个 | 零件列表 |
| `parts[].id` | string? | 可选零件标识 |
| `parts[].outer` | object | 外轮廓 |
| `parts[].holes[]` | object[] | 孔洞列表（可为空） |
| 轮廓 `.id` | string? | 可选轮廓标识，出错时原样回传以便定位 |
| 轮廓 `.points[]` | array | 闭合点列，至少 3 个点；按顺序首尾相连，**不要**重复末点 |
| 点 `.x` / `.y` | number | 有限数字（不接受 NaN / Infinity） |

示例：

```bash
curl -sS http://localhost:8000/api/v1/kerf-compensation \
  -H 'Content-Type: application/json' \
  --data @examples/request.json
```

**响应要点**

- `offset`：实际单侧偏移量（`kerf_width / 2`）；
- `parts[].outer`：外扩后的闭合刀具轨迹；
- `parts[].holes[]`：内缩后的孔洞轨迹，嵌套在所属零件下，保持归属；
- 每条轨迹含 `role`（`outer`/`hole`）、`orientation`（外轮廓 `CCW`、孔洞 `CW`）、
  `part_index`、`source_contour_index`（外轮廓为 `-1`，孔洞为其在原始 `holes` 中的下标）；
- 点列闭合但不重复末点；起点旋转到距原始轮廓首点最近的点，便于与输入对照。

### 排序规则（确定性输出）

- 零件：有 `id` 的按 `id` 字典序在前，无 `id` 的按原始请求顺序在后；`id` 相同则按原始下标；
- 零件内孔洞：同样按孔洞 `id` 字典序、再按原始下标。
- 无论如何排序，响应中的 `part_index` / `source_contour_index` 始终指向**原始请求下标**。

---

## 错误处理：整单拒绝 + 定位原轮廓

出现下列情况时返回 `HTTP 422`，响应只包含错误信息与定位，**不返回任何轨迹**：

| `error.code` | 触发条件 |
| --- | --- |
| `SELF_INTERSECTING_CONTOUR` | 原始轮廓自交（含自切、蝴蝶结等非简单环） |
| `CONTOUR_VANISHED` | 孔洞相对刀缝过小，内缩后轮廓消失（或退化为线/点） |
| `CONTOUR_SPLIT` | 偏移后轮廓分裂为多个闭合环（典型：哑铃形细腰孔洞内缩） |

`error.location` 给出原始位置：

```json
{
  "error": {
    "code": "CONTOUR_SPLIT",
    "message": "刀缝偏移后轮廓分裂为多个闭合环，整单拒绝: 偏移结果类型为 MultiPolygon，不是唯一闭合轮廓",
    "location": {
      "part_index": 0,
      "contour_index": 0,
      "part_id": "bracket",
      "contour_id": "slot"
    }
  }
}
```

- `part_index`：零件在请求 `parts[]` 中的下标；
- `contour_index`：外轮廓固定为 `-1`；孔洞为其在该零件 `holes[]` 中的下标；
- `part_id` / `contour_id`：请求中提供了则原样回传，否则为 `null`。

请求体本身的字段校验失败（如刀缝宽度 ≤ 0、点数不足、连续重复点、多余字段等）同样返回 `422`。

---

## 几何约定与说明

- 坐标单位任意但需自洽（`kerf_width` 与坐标同单位）；假定为数学坐标系（y 向上）。
- 轮廓按简单环（simple ring）处理，不要求输入绕向，服务统一规范化输出绕向。
- 尖角连接采用 GEOS 的 **mitre（斜接）** 方式，斜接上限为 2；圆弧分段精度 `quad_segs = 16`。
- 服务不做孔与外轮廓的包含关系校验：调用方需保证孔洞确实位于所属零件外轮廓内部
  且孔洞之间互不重叠（与常规 CAM 输入约定一致）。

---

## 本地开发（不用 Docker）

需要 Python 3.11+：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt

uvicorn app.main:app --reload --port 8000
pytest
```

测试通过 FastAPI TestClient 覆盖：健康检查、外扩/内缩数值、孔洞归属、
稳定排序、自交/消失/分裂三类整单拒绝与定位、请求校验等。

## 项目结构

```
app/
  main.py        # FastAPI 路由、异常处理、排序
  geometry.py    # Shapely 偏移核心与整单拒绝逻辑
  schemas.py     # 请求/响应模型
  errors.py      # 错误码与带定位的补偿异常
tests/           # pytest 测试
examples/        # 请求示例
Dockerfile
docker-compose.yml
requirements.txt
```
