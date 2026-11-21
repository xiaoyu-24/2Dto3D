# 机械图纸工作台

当前完成实施方案第 5 步的批量、确认和交付流程验收。网页支持上传多份图纸、多页共享读图、选型派生、带页区的确认、局部重试和历史 ZIP。M12 四针、五针已在独立 Compose 环境通过离线 HTML 与 STEP 验收。范围和实测证据见 [M2 验收记录](m2/README.md)。

## 本地运行

需要 Docker Engine / Docker Desktop Linux 容器和 Compose。复制 `.env.example` 为 `.env`，分别设置数据库密码、root 密码和初始账号密码（至少 12 字符），再执行：

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

访问 <http://localhost:8080>，使用 `.env` 中的 `BOOTSTRAP_LOGIN`、`BOOTSTRAP_PASSWORD` 登录。MySQL 无宿主机端口，网页只绑定本机回环地址。此配置用于本地联调；后续生产 HTTPS 接入时使用 `COOKIE_SECURE=1`。

首次启动的迁移服务执行 `alembic upgrade head` 并创建初始账号、项目与成员关系。已有账号时保留原密码和数据。后续修改 `.env` 的初始密码不会重置账号密码。数据库保存在命名卷 `drawing-platform_mysql-data`。

```sh
docker compose logs --tail 50 worker api
docker compose run --rm migrate
docker compose stop
docker compose up -d
```

`stop` 保留数据；恢复运行后 Worker 重新领取就绪或租约过期的任务。所有命令仅针对本 Compose 项目。

## 上传与任务契约

1. 项目成员登录后创建/选择批次，逐份上传 PDF。上传最大 32 MiB，网关整体请求限制 33 MiB，MySQL `max_allowed_packet=256M`。
2. 原始文件以 SHA-256 存入 `file_bytes.body`（LONGBLOB）。原文、`source_file`、文件级 `product_task`、首个 `task_stage` 和接收事件同事务提交。事务成功退出后接口才返回 HTTP 202 与 `message: 已接收`。
3. 批次幂等键为 `(project_id, request_key)`；文件幂等键为 `(batch_id, client_file_key)`。网页使用文件原始字节 SHA-256 作为文件键，保存批次 ID 和未获回执的批次请求键。同批次重复上传相同内容复用原任务；同键异内容返回 409。同名异内容使用新键独立创建。新建批次表示新任务，不跨项目/批次合并。
4. 上传超时或收到 503 时，重试原批次与文件键，核对既有提交结果。失败响应不会声称“已接收”。
5. 独立 `worker` 服务通过 `READ COMMITTED` + `FOR UPDATE SKIP LOCKED` 领取阶段；租约 90 秒、每 20 秒续租，数据库 UTC 时间判定有效性。完成时检查 owner、fence、租约和输入指纹，旧持有者无法提交结果。
6. Worker 在事务外解析 PDF；页数、原始页号、MediaBox、CropBox、旋转、UserUnit 和文本形成 JSON 检查点，与阶段完成、任务状态和完成事件同事务保存。扫描 PDF 可无文本；不把文本提取当作视觉读图或尺寸验收。

网页上传启用 `workflow=m2`。Worker 解析后对同一份 PDF 的全部页做一次联合读图，再等待用户选择型号。产品任务共享读图指纹，分别保存确认及状态。历史上传和首件下载继续使用原接口。HTML 通过独立验收即可按「部分完成」下载。同一几何的 HTML、STEP、报告均通过指纹门禁后，版本进入 `published`。

M2 读图继续使用 `M1_VISION_PROJECT_ID` 和 `M1_VISION_BUDGET_CNY` 配置。CAD 阶段使用本机信任的执行器和锁定镜像。已有 `drawing-m0-cad` 镜像时可直接开工。本机需安装 Microsoft Edge 和 Python Playwright。

```powershell
docker compose -f compose.m0.yaml build cad
python m2/cad_worker.py --project drawing-platform
```

执行器领取同一 MySQL 队列，调用断网 FreeCAD，使用 Edge 独立检查 HTML，再提交几何指纹、验收和成品。保留该进程运行即可处理后续 CAD 任务。

## API

请求采用同源 HttpOnly 会话 Cookie，8 小时过期。所有 POST 需要 `X-Requested-With: drawing-web`；服务不开放跨域写入。密码采用 Argon2id，数据库只存会话令牌 SHA-256。项目读写均检查成员权限，reader 只有查询权限。

| 接口 | 输入/用途 |
|---|---|
| `POST /api/login` | JSON `{ "login": "账号", "password": "密码" }` |
| `POST /api/logout`、`GET /api/me` | 退出、当前用户 |
| `GET /api/projects` | 当前用户所属项目 |
| `POST /api/projects/{id}/batches` | JSON `{ "request_key": "客户端稳定请求键" }` |
| `GET /api/projects/{id}/batches` | 最近 100 个批次，支持新浏览器恢复查看 |
| `POST /api/batches/{id}/files` | multipart 字段 `client_file_key`、`file`、可选 `workflow=m2` |
| `GET /api/batches/{id}` | 图纸与产品两层进度 |
| `GET /api/tasks/{id}` | 单任务状态和已保存的 PDF 解析检查点 |
| `GET /api/tasks/{id}/events?after=0` | 按事件 ID 游标读取，单次最多 100 条 |
| `GET /api/tasks/{id}/m1` | M12 冻结依据和已发布交付的元数据 |
| `GET /api/tasks/{id}/m1/delivery/{kind}` | 项目成员下载已发布的 `html`、`step` 或 `report`，响应含字节数和 `X-Content-SHA256` |
| `GET /api/tasks/{id}/m2` | 共享读图、型号、原页坐标、阶段与确认记录 |
| `POST /api/tasks/{id}/products` | `part_numbers` 数组选择型号，重复请求复用已有产品 |
| `POST /api/confirmations/{id}/answer` | `{ "answer": "确认结论" }`，最后一项确认仅续办一次 |
| `POST /api/tasks/{id}/retry`、`POST /api/batches/{id}/retry-failed` | 仅重入失败阶段 |
| `GET /api/projects/{id}/versions`、`GET /api/versions/{id}` | 使用 `batch_id`、`part_number`、`version` 查询交付及可下载文件 |
| `GET /api/artifacts/{id}/download`、`POST /api/projects/{id}/zip` | 文件下载及按 `version_ids` 打包，ZIP 内含文件大小、指纹和状态 |

请求键为 1–96 位 ASCII 字母、数字、`_` 或 `-`。上传回执包括 `batch_id`、`source_id`、`task_id`、`stage_id`、`sha256`、`reused`。

## 验证

所有浏览器验证使用 Microsoft Edge；Playwright CLI 启动时显式传入 `--browser msedge --headed`。该约定已加入 `AGENTS.md`。

测试用真实 MySQL 创建随机命名的临时数据库，结束时仅清理该测试库，不使用业务库。以只读挂载提供真实 M12 PDF：

```powershell
$env:M12_PDF_PATH = 'D:/desktop/测试文件.pdf'
docker compose -f compose.yaml -f compose.test.yaml run --rm tests
```

Linux 对应先 `export M12_PDF_PATH=/absolute/path/to/m12.pdf`，再执行同一 Compose 测试命令。测试图指纹固定为 `be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c`。

实际命令、退出码与下载指纹见 [M1 验收记录](m1/README.md)。后端直接依赖见 `backend/requirements.txt`，实际已验证依赖冻结在 `backend/requirements.lock`；前端使用 `frontend/package-lock.json` 和 `npm ci`。测试依赖随本地联调镜像安装。

实现依据：[MySQL 锁定读取](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)、[SQLAlchemy 事务上下文](https://docs.sqlalchemy.org/en/21/core/connections.html)、[pypdf 文本提取](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)。
