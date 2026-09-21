# 激光切割刀缝补偿 API

基于 **Python 3.11 + FastAPI + Shapely (GEOS)** 的刀缝补偿（kerf compensation）服务：
给定由**外轮廓**与**孔洞**组成的零件以及刀缝宽度，自动计算闭合的**刀具中心轨迹**。

- 外轮廓向**外**偏移 `kerf_width / 2`（外切留余量）
- 孔洞向**内**偏移 `kerf_width / 2`（孔洞不切大）
- 输出闭合环；孔洞始终归属原零件
- 结果按**零件 id 升序**稳定排序；零件内轮廓顺序固定为「外轮廓在前，孔洞按原下标排列」
- **整单原子性**：输入自交，或偏移导致轮廓消失/分裂/拓扑改变时，整单拒绝（HTTP 422）并定位到原轮廓，不返回任何部分轨迹

## 一条命令启动

需要本机已安装 Docker（含 Compose v2）。在项目根目录执行：

```bash
docker compose up --build -d
```

镜像内已包含全部 Python 依赖与 Shapely 自带的 GEOS 原生库，构建时无需访问系统包管理器。

默认访问地址与端口：

| 用途 | 地址 |
| --- | --- |
| 服务基础地址 | http://localhost:8000 |
| 健康检查 | http://localhost:8000/health |
| 交互式接口文档 (Swagger UI) | http://localhost:8000/docs |
| OpenAPI 描述 | http://localhost:8000/openapi.json |

停止服务：`docker compose down`；查看日志：`docker compose logs -f`。

> 端口被占用时，编辑 `docker-compose.yml` 中 `ports`（如 `"18000:8000"`）后重启即可，容器内默认监听 `8000`。

## 必要配置

所有配置均通过环境变量提供，均有默认值，不改任何配置即可启动：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_HOST` | `0.0.0.0` | 容器内监听地址，一般无需修改 |
| `APP_PORT` | `8000` | 容器内监听端口 |
| `MAX_CONTOUR_POINTS` | `100000` | 单条轮廓最大顶点数，防止超大输入耗尽计算资源 |

坐标与 `kerf_width` 必须使用**同一长度单位**（如 mm），服务不做单位换算。

## 接口

### `GET /health`

健康检查，返回服务状态、版本及底层 Shapely / GEOS 版本。可直接用于容器编排与负载均衡探针（镜像内置 HEALTHCHECK）。

### `POST /api/v1/kerf/offset`

请求体：

```json
{
  "kerf_width": 1.0,
  "parts": [
    {
      "id": 2,
      "outer": {
        "points": [
          {"x": 10, "y": 10}, {"x": 30, "y": 10},
          {"x": 30, "y": 20}, {"x": 10, "y": 20}
        ]
      },
      "holes": [
        {
          "points": [
            {"x": 14, "y": 13}, {"x": 14, "y": 17},
            {"x": 20, "y": 17}, {"x": 20, "y": 13}
          ]
        }
      ]
    }
  ]
}
```

字段约束：

- `kerf_width`：正数，单边偏移量为其一半。
- `parts`：至少 1 个零件；`id` 为非负整数且全单唯一。
- 每条轮廓 `points` ≥ 3 个顶点，按顺序给出；无需重复首点来闭合（显式闭合也会被归一化接受）。
- 连续重复顶点会被去除；有效顶点不足 3 个、轮廓自交/自切一律拒绝。

成功响应（HTTP 200）：

```json
{
  "kerf_width": 1.0,
  "offset": 0.5,
  "parts": [
    {
      "id": 2,
      "contours": [
        { "role": "outer", "hole_index": null, "source_vertex_count": 4, "points": [ {"x": 9.5, "y": 9.5}, "...", {"x": 9.5, "y": 9.5} ] },
        { "role": "hole",  "hole_index": 0,    "source_vertex_count": 4, "points": [ "...闭合环..." ] }
      ]
    }
  ]
}
```

- 每个 `points` 均为闭合轨迹，末点与首点相同。
- 尖角采用斜接（mitre）处理并限制斜接长度（mitre_limit = 2），避免锐角产生异常长尖刺。

快速试用：

```bash
curl -X POST http://localhost:8000/api/v1/kerf/offset \
  -H 'Content-Type: application/json' \
  --data @examples/request.json
```

### 错误响应（整单拒绝）

HTTP 状态码统一为 `422`，信封如下，`location` 精确定位原零件 / 原轮廓：

```json
{
  "error": {
    "code": "SELF_INTERSECTION",
    "message": "外轮廓存在自交或自切，无法进行刀缝补偿",
    "location": { "part_index": 0, "part_id": 1, "contour": "outer", "point": [1.0, 1.0] }
  }
}
```

| code | 含义 |
| --- | --- |
| `INVALID_REQUEST` | 请求体不符合接口规范（缺字段、类型错误、刀缝宽度 ≤ 0 等） |
| `DUPLICATE_PART_ID` | 零件 id 重复，无法稳定排序 |
| `INVALID_CONTOUR` | 有效顶点不足 3 个或轮廓无法构造 |
| `SELF_INTERSECTION` | 原轮廓自交/自切；尽量在 `location.point` 给出交点坐标 |
| `HOLE_DISAPPEARED` | 孔洞向内偏移后消失（刀缝相对孔洞过大） |
| `OFFSET_SPLIT` | 偏移后轮廓分裂为多条独立环（窄腰被切断等） |
| `OFFSET_TOPOLOGY_CHANGED` | 偏移结果退化为点/线或环无效 |
| `OFFSET_FAILED` | GEOS 偏移计算内部失败 |
| `LIMIT_EXCEEDED` | 单轮廓顶点数超过 `MAX_CONTOUR_POINTS` |

`location.contour` 取值为 `outer`（外轮廓）或 `hole`（孔洞）；孔洞错误附带 `hole_index`。

## 本地开发（不使用 Docker）

需要 Python 3.11：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
```

运行测试：

```bash
.venv/bin/python -m pytest -q
```

## 项目结构

```
app/
  main.py       # FastAPI 应用、路由与异常处理
  schemas.py    # 请求/响应 Pydantic 模型
  geometry.py   # 自交检测、刀缝偏移、整单两阶段处理
  errors.py     # 业务错误类型与定位信息
  config.py     # 环境变量配置
examples/       # 请求示例
tests/          # pytest 测试（健康检查、正常补偿、各类整单拒绝）
Dockerfile
docker-compose.yml
requirements.txt / requirements-dev.txt
```
