# 市政道路桥梁养护管理平台

面向市政道路桥梁日常巡查、定期检测、病害维修、除雪防汛与占道施工的一体化养护管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 设施台账 | `facility` | 设施 | 设施编号、设施名称、设施类型 |
| 桥梁档案 | `bridge` | 桥梁 | 桥梁编号、桥梁名称、桥型结构 |
| 隧道管理 | `tunnel` | 隧道 | 隧道编号、隧道名称、隧道长度 |
| 路面状况 | `pavement` | 路面评价 | 评价编号、道路名称、评价路段、三项指标、自动分档（优良中差） |
| 日常巡查 | `patrol` | 巡查记录 | 巡查编号、巡查路段、巡查人员 |
| 病害记录 | `disease` | 病害 | 病害编号、所属设施、病害类型 |
| 养护维修 | `repair` | 维修任务 | 任务编号、任务类型、维修对象 |
| 养护材料 | `material2` | 养护材料 | 材料编号、材料名称、规格型号 |
| 养护机械 | `machine` | 养护机械 | 机械编号、机械名称、规格型号 |
| 应急抢险 | `emergency` | 应急事件 | 事件编号、事件类型、发生地点 |
| 除雪防汛 | `deicing` | 除雪防汛 | 作业编号、作业类型、作业路段 |
| 占道施工 | `occupy` | 占道施工 | 施工编号、施工位置、占用范围 |
| 绿化管护 | `greening` | 绿化管护 | 管护编号、管护区域、植被类型 |
| 交安设施 | `safety2` | 交安设施 | 设施编号、设施类型、所在路段 |
| 边坡挡墙 | `geom` | 边坡挡墙 | 边坡编号、所属路段、边坡类型 |
| 路灯管养 | `light` | 路灯设施 | 灯杆编号、所在路段、灯型类别 |
| 排水设施 | `drain` | 排水设施 | 设施编号、设施类型、所在路段 |
| 养护计划 | `plan` | 养护计划 | 计划编号、计划周期、计划类型 |
| 市民热线 | `complaint` | 热线记录 | 记录编号、来电人、来电内容 |
| 车辆超限 | `load` | 超限记录 | 记录编号、抓拍路段、车辆类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

### 路面状况自动分档

- 判定口径固化在 `app/services/pavement_grading.py`：路面损坏指数、平整度指数（越低越好）、
  抗滑系数（越高越好）各入一档，路段结论按就低原则取最差项；损坏指数超过中上限（默认 80）
  一律定差；抗滑系数低于差档下限时，依据中写明偏离值与偏离比例。
- 指标缺失、格式不正确或超出物理合理范围时不生成分档，记录保留为「待评定」并逐条说明原因。
- 规则以版本形式管理（`GET/POST /api/pavement/rules`、`/rules/release`），只增不改；
  发布新版本自动对既有记录全量重评（`POST /api/pavement/regrade` 可手工补跑）。
- 每条评价编号对应一个评价路段，分档生成不可变依据快照（SHA-256 指纹），
  重评另存新快照、旧依据留在分档历史；复核走 `GET /api/pavement/{id}/verify` 做指纹比对。
