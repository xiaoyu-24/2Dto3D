# 第 2 步：网页上传与独立 Worker 实施计划

**授权与依据：** 用户已确认继续实施第 2 步；沿用《平台实施方案》第 5–8 节。此次不执行 Git 提交、推送或发布。

**目标：** React 登录项目并上传真实 M12 PDF；原文字节、源记录、文件任务、首阶段队列项同一 MySQL 事务提交后返回“已接收”；重复请求复用任务；关闭浏览器后独立 Worker 继续完成 PDF 解析。

**领域契约检查：** 仓库未提供 `CONTEXT.md` 和 `docs/engineering-ai-guardrails.md`，以现有实施方案和 `AGENTS.md` 为依据。交付种类保持 HTML、STEP、报告。第 2 步的终点是 PDF 解析检查点，真实模型读图属于第 3 步，界面不得将解析完成标作建模或成品完成。

**实现选择：** 沿用 MySQL LONGBLOB + 事务队列；对象存储不能参与同一个 MySQL 事务，浏览器或 API 内后台任务不能满足独立进程要求，因此不采用。React 使用同源 API、HttpOnly 会话 Cookie。数据库只启用本步必需的表，使用 Alembic 迁移。Worker 用数据库时间、90 秒租约、20 秒续租与 fence 校验，事务外解析 PDF，结果以 SHA-256 引用的 JSON 检查点入库。

**幂等边界：** 批次由项目与 request_key 防重；文件由批次与 client_file_key 防重。网页用原始字节 SHA-256 作为文件键并保存当前批次；同批次同内容重复上传返回同一 source/task/stage，同名不同内容另建任务。同一个键提交不同字节返回 409。新建批次表示显式的新任务，不跨批次或项目偷偷合并。

**影响范围：** 新增 `backend/`、`frontend/`、`compose.yaml`、配置示例、运行文档和验收记录；保留 M0 文件及工作区已有修改。

- [x] 数据基础：`backend/app/db.py`、`backend/migrations/` 建立用户/会话、项目/成员、文件/批次/源记录/任务/阶段/事件；提供显式初始化账号命令。实际运行 `alembic upgrade head` 两次，验证新建和已有数据路径。
- [x] 登录与事务上传：`backend/app/auth.py`、`ingest.py`、`main.py` 实现登录、退出、项目/批次/任务查询和上传。身份与成员权限覆盖所有读取和写入。`engine.begin()` 退出成功后才生成回执。
- [x] 队列与解析：`backend/app/queue.py`、`pdf.py`、`worker.py` 使用 `FOR UPDATE SKIP LOCKED` 领取和超时接管，保存页数、页面框、旋转及文本；过期或旧 fence 的结果不得提交。
- [x] 网页与启动：`frontend/src/` 实现登录、项目选择、批次上传、回执和持久状态查询；构建 React 静态页面，经 nginx 反向代理 FastAPI。Compose 分开运行 MySQL、迁移、API、Worker 与网页。
- [x] MySQL 回归：`backend/tests/` 使用隔离测试库，验证事务中途异常全部回滚、提交失败无回执、并发重复上传、同键异内容、未登录/跨项目/只读权限、真实 M12 解析与租约 fence。运行 `pytest -q`，失败时按证据最小修复。
- [x] 浏览器验收：Playwright 显式使用 `msedge` 通道，真实登录、上传 `D:/desktop/测试文件.pdf`（104504 字节，SHA-256 `be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c`）、重复上传、关闭浏览器、观察 Worker 完成，重新登录查看同一任务。通过暂停本任务 Worker 使关闭顺序可确定，关闭后恢复，数据库事件证明处理发生在关闭之后。
- [x] 交付记录：`m1/README.md` 记录实际命令、退出码、任务 ID、哈希、浏览器关闭与 Worker 完成时间、覆盖范围和未验证项；`README.md` 说明启动和 API 契约。

**风险与验收限制：** 数据库文件包大小设为 256 MiB，上传最大 32 MiB。此次验证的是本机 Linux Compose 联调；不等同生产部署、视觉识图、CAD 生成或 M3 故障矩阵验收。原 PDF 只读引用，不纳入 Git。无待用户确认的实施事项。
