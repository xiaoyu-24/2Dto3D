# 第 2 步：登录、上传与后台任务验收

验证日期：2026-11-20，以下时间均为北京时间（Asia/Shanghai）。本机 Docker Desktop Linux Engine 29.5.3 / Compose 5.1.4；数据库实测 MySQL 8.4.10、READ-COMMITTED、`max_allowed_packet=268435456`。最终浏览器验收全部使用 Microsoft Edge 157.0.4341.60，Playwright 显式指定 `--browser msedge --headed`，UA 含 `Edg/157.0.0.0`。

## 真实 M12 样本

- 文件：`D:/desktop/测试文件.pdf`，104504 字节，1 页。
- 原文 SHA-256：`be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c`。
- Edge 浏览器验收：批次 #2、源记录 #2、文件任务 #2、队列项 #2。
- 检查点 SHA-256：`73f381e1f0b51278a995fd12647da8759c768df3802b0aaf968845ed4f50ddb2`，2277 字节，提取文本 1477 字符。
- 从数据库读回原文和检查点，重新计算 SHA-256，均与引用一致。原 PDF 只读挂载，不纳入 Git。

## 命令与结果

| 实际命令/检查 | 退出码 | 结果 |
|---|---:|---|
| `git status --short --branch` | 0 | 在 `codex/m0-linux-probes` 继续；保留已有实施方案修改和 M0 未跟踪文件 |
| `docker compose config --quiet` | 首次 1，修正后 0 | 本次生成的 `.env` 最初换行格式有误，修正为每行一个键后通过；随机密码未写入源码 |
| `npm install --package-lock-only`（frontend） | 0 | 生成前端依赖锁 |
| `docker compose build` | 首次 1，修正后 0 | TypeScript 首次报 CSS 导入缺类型，加入 `vite/client` 类型声明后构建成功；`tsc --noEmit` 与 Vite 均通过 |
| `docker compose up -d mysql` | 0 | 本项目独立数据库实例和持久卷启动健康 |
| `docker compose -f compose.yaml -f compose.test.yaml run --rm tests` | 0 | 首轮 **14 passed**，5.50 秒；依赖锁定后复跑 **14 passed**，4.93 秒。真实 MySQL 随机临时库，未使用业务数据 |
| `docker compose up -d api worker web` | 0 | Alembic 建库、初始账号/项目、API、Worker、网页启动成功 |
| `alembic upgrade head` 两次（测试 fixture）；`docker compose start api` 触发已存在库迁移与初始化 | 0 | 空库创建和相同迁移重复执行均通过，已有账号和任务保留 |
| Playwright CLI `open http://localhost:8080 --browser msedge --headed`、登录、选择真实 PDF、两次点击上传 | 0 | Edge 第一次“创建任务 #2”，第二次“复用任务 #2”；批次 #2 中数据库计数 source/task/stage 均为 1 |
| `docker compose pause worker` → Edge `close` → `docker compose stop api` → `docker compose unpause worker` | 均 0 | Edge 和 API 均不运行时，Worker 完成任务 #2 并持久化检查点 |
| `docker compose start api`；Playwright 新 Edge 浏览器登录 | 0 | 新会话可查看批次 #2、同一任务 #2，状态“解析完成 · 待读图”，1 页检查点可查看 |
| Edge 桌面/390px 窄屏截图与页面宽度检查 | 0 | 接收、状态和检查点可见；窄屏无页面水平溢出，任务表格支持局部横向滚动 |
| 数据库原文/检查点重算 SHA-256 | 0 | 原图字节与检查点均完整 |
| `docker compose exec -T api python -m pip check` | 0 | `No broken requirements found` |
| `git diff --check` | 0 | 已跟踪文件未引入空白错误；未提交或暂存文件 |

测试有一条第三方弃用提示：Starlette 建议将 TestClient 的 `httpx` 迁至 `httpx2`，当前固定版本测试通过。首次匿名访问 `/api/me` 的 401 属于预期鉴权行为；页面正常进入登录表单。

## 事务与权限回归覆盖

14 项测试覆盖：提交回执后从独立连接读到完整原图/源记录/队列；6 路并发重复上传只有一个任务；分别在源记录、任务、队列、事件写入和提交时注入异常，HTTP 503 且原文/源记录/队列全部回滚；同键异内容 409；同名新内容独立任务；批次键幂等；登录/退出、跨项目读取和上传、只读成员写入拒绝；无效 PDF 和超限文件不入队；真实 M12 检查点；SKIP LOCKED、续租、过期接管、fence 隔离；损坏 PDF 失败后后续任务仍可处理。

## 关闭浏览器后的时间证据

为确定处理发生在浏览器关闭之后，先暂停本项目 Worker，完成 Edge 网页上传与重复上传，再关闭测试 Edge 并停止 API，最后恢复 Worker。首次预跑未显式指定 Edge，已按用户新增规则完整重跑；以下只使用 Edge 重跑证据。批次 #1 保留为早期预跑记录，不作为最终浏览器验收。

| 事件 | 北京时间 | 证据 |
|---|---|---|
| 接收事件写入（回执前提交） | 17:19:40.836163 | `stage_event.accepted`，fence=0；该时间为事件写入时间，非精确 COMMIT 时刻 |
| 第二次 Edge 网页上传 | 17:20:12 | 页面显示“已接收 · 复用任务 #2” |
| Edge 进程关闭完成 | 17:20:33.3947988 | Playwright `close` 返回成功后记录宿主机 UTC 时间，再换算 +08:00 |
| API 停止 | 17:20:34 前 | Compose 返回 Stopped，后续 `ps` 中 API 未运行 |
| Worker 领取任务 | 17:20:34.151849 | 数据库 `stage_event.started`，fence=1 |
| Worker 提交完成 | 17:20:34.354721 | 数据库 `stage_event.completed`，1 页，检查点指纹同上 |
| 新 Edge 浏览器重新登录 | 17:21:17 | 页面恢复批次 #2，任务 #2 显示“解析完成 · 待读图” |

原始 Edge 关闭时间：`output/playwright/edge-browser-closed.txt`。截图：`output/playwright/edge-reused.png`、`edge-completed.png`、`edge-mobile.png`；这些本地验收文件不纳入 Git。Worker 进程标识为 `e883271dc4c4:84fa6d1e-12cb-4bea-ba34-3edd5fb4d1f3`，完成时领取次数为 1。

## 范围

本次证据覆盖本机 Linux Compose 联调、网页与真实 MySQL。未进行模型视觉识图、尺寸解释、CAD 建模、HTML/STEP/报告发布、生产部署和完整 M3 故障矩阵。当前任务终点 `awaiting_read` 表示等待第 3 步识读；不得解释为三维成品已完成。没有调用供应商模型或产生模型费用。

## 2026-11-21 API 容器换址回归

- 在独立 Compose 项目 `drawing-step2-qa` 中用 `docker stop` 和 `docker start` 让 Worker 占用 API 的旧地址。`docker inspect` 确认 API 换址。旧配置在 API 已健康时仍连续返回 `502`。
- 新配置通过 `docker exec drawing-step2-qa-web-1 nginx -t`（退出码 `0`）。API 从 `172.22.0.3` 换到 `172.22.0.5` 后，网页 `/api/health` 从 API 启动算起约 2.4 秒恢复 `200`，Nginx 容器 ID 和启动时间一致。
- Playwright Edge 登录、查看原批次的任务 #2 和读取 `/api/tasks/2/events?after=0` 均通过。后端 Compose 回归 `14 passed`（退出码 `0`），仍有一条 Starlette 弃用提示。
- 最终配置的全量镜像重建因 Docker Hub 连接超时退出 `1`。回归使用已经构建的 Nginx 1.28.3 前端镜像，以只读挂载加载最终配置。

## 第 3 步：M1 真实读图与证据确认

2026-11-21 在独立 Compose 项目 `drawing-step3-qa` 接续项目 #1 的任务 #2。先从第 2 步 MySQL 导出原图与解析检查点，分别重算 SHA-256，再以同一任务号恢复到 QA。没有重新上传。工作台在 `http://127.0.0.1:8083`，登录后打开批次 #2 的任务详情。

| 持久记录 | 值 |
|---|---|
| 原 PDF 104,504 字节 | `be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c` |
| 检查点 2,277 字节 | `73f381e1f0b51278a995fd12647da8759c768df3802b0aaf968845ed4f50ddb2` |
| 第 1 页 | MediaBox/CropBox `[0, 0, 841.92, 595.32]` pt，旋转 0°，UserUnit 1，整页与 9 张局部图均入库 |
| Worker | `vision_read` 阶段 #3，领取次数 1，fence 1，任务状态 `basis_frozen` |
| 预算 | 用户确认可调用已配置的 DeepSeek。沿用任务上限 CNY 10 和项目日预算 CNY 500，可用 `M1_VISION_BUDGET_CNY` 设置更低的任务上限 |
| 同阶段图片能力题 | `deepseek-flash`，`37d44e2f2b38987b0b53f3cb293a6ebd`，9,053 ms，625 输入 / 30 输出 token。随机答案 2 圆、2 方、2 三角、`961`、向左，与期望完全一致 |
| 同阶段真实读图 | `deepseek-flash`，`435404b9b7452711a2dad8418f26bd50`，16,845 ms，5,064 输入 / 1,218 输出 token。完整第 1 页和所需局部图通过 M0 锁定的 Harness SDK/Runtime `0.2.2rc2` 发送 |
| 读图与原图复核 | `M12A04-03-301-M16`、ENDE05 A0、4 针、24.70 mm。复核纠正焊线端 13.40 mm 的量测方向、24.70 mm 的通用公差以及 PG9 与 M16 的适用关系 |

页区保存 PDF 左下原点坐标和 PNG 左上原点像素坐标，以及原图和裁图各自的指纹。开发机证据在 `D:/projects/2Dto3D/output/m1-step3-evidence/`，包括 `task2-deepseek-read.json`、`task2-read-audit.json`、`db-*.png` 和 Edge 截图。独立验收参考值与历史模型未拼入提示词。

| 实际命令或检查 | 退出码 | 结果 |
|---|---:|---|
| `docker compose -f compose.m0.yaml run --rm vision-probe`，当时的初版探针 | 1 | 出参达到 128 token 上限，未能完整作答 |
| `docker compose --ansi never -f compose.m0.yaml run --rm -T -v './m0/harness/m1_service.py:/opt/probe/m1_service.py:ro' vision-probe python -c …` | 0 | 修正后独立联网探针通过。请求 `2674c05f0603da3c1ae29cd15115b4f1`，3,553 ms，626 / 21 token |
| `docker compose -f compose.m0.yaml build vision-probe`；`docker compose -f compose.m0.yaml run --rm -T vision-probe python vision_probe.py --self-test` | 均 0 | 镜像构建及随机 PNG 自检通过 |
| `docker compose -f compose.m0.yaml run --rm -T -e DEEPSEEK_API_KEY= -e M1_VISION_BUDGET_CNY= vision-probe` | 2，预期 | 标记真实读图待联调 |
| `docker compose -p drawing-step3-qa up -d --build mysql api worker` | 0 | 独立 MySQL、迁移、API、Worker、视觉容器启动 |
| 原库导出脚本与 QA `docker compose -p drawing-step3-qa exec -T api python -c …` | 0 | 校验原文、检查点、恢复任务 #2，入队阶段 #3，读回供应商记录并导出图像 |
| 设置 `$env:WEB_PORT='8083'` 后运行 `docker compose -p drawing-step3-qa up -d --build --no-deps api worker web` | 0 | API、Worker 和前端构建通过，保留已完成的阶段和证据 |
| 设置 `$env:M12_PDF_PATH='D:/desktop/测试文件.pdf'` 后运行 `docker compose -p drawing-step3-qa -f compose.yaml -f compose.test.yaml run --rm -T tests` | 0 | 17 项通过。一条既有 Starlette `httpx` 弃用提示。两个错长都被挡在冻结之前。临时测试库验证 Worker 自动冻结、重试复用及两个文件的 SHA-256 |
| PowerShell 将 Playwright Python 脚本传给 `python -`，`chromium.launch(channel='msedge', headless=True)` | 0 | Edge 157.0.4341.60。验证重新登录回显同一资料、选区切换、局部图哈希及 390 px 窄屏。之后复用任务 #2 的已完成读图生成 M1.2，下载两个冻结文件并复算哈希。前后同阶段供应商请求数均为 2 |
| 同一测试命令附加 `python -m pytest -q -p no:cacheprovider tests/test_m1.py -k end_views`，修复前 | 1 | 正确视向通过，端面对调仍进入 `basis_frozen`，复现审核发现 |
| 同一 Compose `run --rm -T tests`，修复后 | 0 | 21 项通过，既有 Starlette 弃用提示仍在。正确视向两个文件均返回 HTTP 200，端面对调、视向矛盾或缺失均返回 404。对调而误冻的 M1.2 经重验后仍不能下载 |
| `docker compose -p drawing-step3-qa --progress plain up -d --build --no-deps api worker web` | 0 | 初次构建因 Docker Hub 连接超时退出 1，重试成功 |
| PowerShell 将端面复验脚本传给 `python -`，使用 `chromium.launch(channel='msedge', headless=True)` | 0 | Edge 中任务 #2 复验前两个下载均为 404。复用存档读图后，两个 M1.3 下载均为 200，哈希与数据库一致。页区指纹一致，供应商请求数仍为 2 |

任务 #2 已冻结版本 `M12A04-03-301-M16/A0/M1.3`。冻结前检查模型分别把带键配合端和焊线端对应到正确的原图页区。以配合端指向焊线端为 +Z，进入配合端的视线沿 +Z，进入焊线端的视线沿 −Z。冻结文件记录两条原始识别的标签和页区，验收清单按同一轴向要求独立回读 STEP。M1.2 保留为历史文件，下载接口仅提供重验通过的 M1.3。

A 键轮廓、针心间距、O 形圈及台阶沿用原图估读和试建设定，环氧灌封记录在说明中。电气针号、O 形圈自由状态规格和独立螺母安装变换在图纸中未定。验收清单对试建项检查几何一致性，对图纸标注与有证据的推导检查原图符合性。

| QA 下载文件 | SHA-256 |
|---|---|
| `D:/projects/2Dto3D/output/m1-step3-evidence/M12A04-03-301-M16-A0-M1.3-basis.json` | `2aca49563740fe8a90ae8418b3fd202d046a24911305948073dd8eb66a9946f0` |
| `D:/projects/2Dto3D/output/m1-step3-evidence/M12A04-03-301-M16-A0-M1.3-checklist.json` | `1ab1154c40edfc42494a1c5955924bfcca2b67c4bc721d2b0ef6f44e36c4e4ec` |

第 4 步消费以上两个文件，由 Harness 调用模型选择或生成 CAD 作业，隔离执行器建模并从同一几何导出 STEP 和离线 HTML。第 3 步的测试验证读图、冻结和清单生成，STEP 实测和离线 HTML 验证属于第 4 步。

## 第 4 步：M1 首件交付

2026-11-21（北京时间），基线 `c815621`，接续任务 #2，发布版本 `M12A04-03-301-M16/A0/M1.3 / G1`。独立 Compose 项目为 `drawing-step4-qa`，工作台为 <http://127.0.0.1:8084>。`m1_delivery` 通过现有 `file_bytes` 持久化几何及三项交付，独立部件和变换可由冻结清单追溯。任务详情展示发布状态及下载链接。

- 核对上节两份文件的 SHA-256、清单指向的建模依据、104,504 字节原图及 10 张原页/页区图。两个本地文件与 QA 工作台任务 #2 重新登录所得字节一致。
- Harness SDK/Runtime `0.2.2rc2` 通过 `deepseek-flash` 选择 `m12-a04-m16-a0`。请求 `4dd9a0c0b1d21692c979cc4a4f15d816`，3,860 ms，416 / 8 token。第 3 步两条读图调用记录继续复用。
- FreeCAD `1.1.4` / OCCT `7.8.1` 使用 M0 镜像 `3f38a3ed15f289551177a9a5540853932d6cedf631fd71e3ce690ead80429373`。建模及回读实测 UID 65534、仅 `lo`、根文件系统只读。导出进程重开已校验指纹的 BREP，按同一部件与展示变换清单生成 STEP 和 27,440 三角面的离线 HTML。冻结几何 SHA-256 为 `cee5658086f47e6a0de23a6cf98cfbb91201bdb4910de2e051f264b82ab38421`。
- STEP 清单 15 项通过。沿配合端到焊线端为 +Z，分别确认正上方 A 键和安装扁面端，四针总长 24.70 mm，公差 ±0.25 mm。外壳长 21.00、六角前肩 10.00、针尖退距 2.20、密封台阶偏移 3.00、圆径 10.60 / 8.35、扁宽 13.40、针径 1.00、对边 18、螺母厚度 2.80 mm。实测螺距 1.00 / 1.50 / 1.50 mm。7 个本体实体与 1 个独立螺母实体均有效，D 安装位姿为 `null`。面板开孔另作参考。
- Edge `157.0.4341.60` 使用 `msedge` 从 `file://` 打开。断网时读取实际绘制像素，确认模型可见、拖动旋转、滚轮放大、按钮缩小、两端视角切换及螺母/触点的显隐恢复。记录了 0 次 HTTP(S) 请求与 0 次页面脚本异常。

### 发布和下载指纹

MySQL `8.4.10` 于 12:26:24.219801 发布。单独从数据库执行 `OCTET_LENGTH(body)`、`SHA2(body, 256)`，核验冻结内容、产物字节和已有指纹。12:27:50 确认临时目录已清除，随后新 Edge 进程再次登录，并通过任务页的三个链接下载。下表为数据库与重新下载共同核验的结果。

| `output/m1-step4/` 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `M12A04-03-301-M16-A0-M1.3-G1.html` | 1,329,332 | `69440e5f1d28958c37938958de8bc78a24e140dee96ae68fc368ab04490b2740` |
| `M12A04-03-301-M16-A0-M1.3-G1.step` | 1,013,783 | `5ade87ca1537c1eea21eda2c89c48710a0ef57d63886b0bb1ff6d04bec696403` |
| `M12A04-03-301-M16-A0-M1.3-G1.md` | 7,236 | `ed07e47bc0cc2111ea2588bfc951605f11c1fa0f1e9afb36c92a029ffc1861c3` |

冻结清单、选择记录、正负例实测、Edge 截图、发布回执、数据库字节清单及清理时间在 `output/m1-step4-evidence/`。

### 实际命令与退出码

以下 CAD 命令在仓库根目录运行。`$work` 当时为 `D:/projects/2Dto3D/output/m1-step4-work`，`$stem` 为 `M12A04-03-301-M16-A0-M1.3`。宿主创建工作目录，随后为导出和回读分别创建空的 `export`、`verify` 子目录。复跑可从保留的 `selection.json` 复用当前作业选择，指纹变化时重新运行 Harness。

```powershell
docker compose -f compose.m0.yaml run --rm --no-deps -T -v './m1/cad:/job:ro' -v './output/m1-step3-evidence:/evidence:ro' -v "${work}:/work" -w /job -e PYTHONPATH=/opt/probe vision-probe timeout 120 python /job/select_job.py "/evidence/$stem-basis.json" /work/selection.json

docker compose -f compose.m0.yaml run --rm --no-deps -T -v './m1/cad:/job:ro' -v "./output/m1-step3-evidence/${stem}-basis.json:/basis.json:ro" -v "${work}:/work" -w /job cad timeout 240 sh -ec 'for variant in nominal swapped 26.70 26.90; do /opt/freecad/usr/bin/python job.py /basis.json /work/selection.json /work/$variant $variant; done'

docker compose -f compose.m0.yaml run --rm --no-deps -T -v './m1/cad:/job:ro' -v "${work}:/freeze:ro" -v "${work}/export:/out" -w /job cad timeout 240 sh -ec 'for variant in nominal swapped 26.70 26.90; do /opt/freecad/usr/bin/python export.py /freeze/$variant /out/$variant.step $(if [ "$variant" = nominal ]; then echo /out/nominal.html; fi); done'

docker compose -f compose.m0.yaml run --rm --no-deps -T -v './m1/cad:/job:ro' -v "./output/m1-step3-evidence/${stem}-checklist.json:/checklist.json:ro" -v "${work}:/evidence:ro" -v "${work}/verify:/out" -w /job cad timeout 240 sh -c 'for variant in nominal swapped 26.70 26.90; do /opt/freecad/usr/bin/python verify.py /evidence/export/$variant.step /checklist.json /evidence/$variant/geometry.json /out/$variant.json; code=$?; echo verifier:$variant:exit=$code; if [ "$variant" = nominal ]; then test "$code" = 0 || exit 1; else test "$code" = 1 && test -s "/out/$variant.json" || exit 1; fi; done'

python m1/qa_browser.py offline output/m1-step4-work/export/nominal.html output/m1-step4-work/browser

docker compose -p drawing-step4-qa run --rm --no-deps -T -v "${work}:/work:ro" -v './m1/cad:/job:ro' migrate python -m app.m1_delivery 2 /work /job

python m1/qa_browser.py download http://127.0.0.1:8084 output/m1-step4 output/m1-step4-evidence/db-files.json .env
```

| 实际命令或检查 | 退出码 | 实测结果 |
|---|---:|---|
| `git status --short --branch`、`Get-FileHash -Algorithm SHA256 -LiteralPath ...`、`docker image inspect drawing-m0-cad drawing-m0-harness` | 0 | 基线和分支正确，初始工作区干净。输入和锁定镜像指纹吻合 |
| `docker compose -f compose.m0.yaml config --quiet`、`select_job.py` | 0 | M0 Compose 配置有效，Harness 返回可执行的作业 ID |
| FreeCAD `job.py`、`export.py` | 0 | 四组输入分别冻结和导出，正例另外导出 HTML。预跑发现长螺旋布尔缺牙，改用分段放样并重新读回检查 |
| 独立 FreeCAD `verify.py` | 0 / 1 / 1 / 1 | 正例 15 项通过。端面对调由端面门禁识别，四针套用错长 26.70 / 26.90 mm 由总长门禁识别。早期回读曾因缺少导入与错识密封台阶退出 1。修正后重验通过 |
| `python m1/qa_browser.py offline ...` | 0 | 最终 HTML 重新导出后按实际画面复测通过，最终 STEP 也独立重验 |
| `docker compose -p drawing-step4-qa up -d mysql`、`docker compose -p drawing-step4-qa --progress plain build migrate api web`、`docker compose -p drawing-step4-qa run --rm --no-deps -T migrate alembic upgrade head` | 0 | 新卷、空库迁移、TypeScript/Vite 与后端镜像通过 |
| 经 Python 管道执行 `docker compose -p drawing-step3-qa exec -T mysql` 的 `mysqldump --single-transaction --no-tablespaces --no-create-info --skip-add-locks --skip-disable-keys --hex-blob`，由 `docker compose -p drawing-step4-qa exec -T mysql` 导入 | 0 | 原任务 #2 与原图/证据一并复用。会话表未导入 |
| `$env:WEB_PORT='8084'`，`docker compose -p drawing-step4-qa up -d --wait mysql api web`，Edge 登录检查 | 0 | 旧读图记录保留。第一次登录可下载两个冻结文件，发布前所有交付接口均为 404 |
| 通过 `docker compose -p drawing-step4-qa run --rm --no-deps -T ... migrate python -` 注入 HTML 字节改变与发布末尾异常 | 0 | 两次均拒绝，任务和文件字节行数保持原状 |
| `python -m app.m1_delivery 2 /work /job`（Compose 内执行两次） | 0 / 0 | 一条发布记录、一条发布事件，重试返回同一回执。库内原图、冻结输入、BREP 和三项成品均重算通过 |
| `Remove-Item -LiteralPath 'D:\projects\2Dto3D\output\m1-step4-work' -Recurse`，`python m1/qa_browser.py download ...` | 0 / 0 | 首次复合清理命令由工具拒绝执行，核对绝对路径和目录内容后以字面路径清理。新客户端匿名请求 401，登录后下载三项文件，与数据库逐项匹配 |
| `docker compose -p drawing-step4-qa -f compose.yaml -f compose.test.yaml run --rm -T tests`（`M12_PDF_PATH=D:/desktop/测试文件.pdf`） | 0 | 21 项现有测试通过，保留一条 Starlette 依赖弃用提示 |
| `git diff --check` | 0 | 补丁空白检查通过 |

验收范围限于这一型号和 G1。本次将螺纹牙型细节、端子和内腔过渡作为试建。未验证电气针号、O 形圈自由规格、螺母安装位置、旋合密封、量产适用性、生产部署及 M2/M3 流程。首件作业和发布由本地命令触发。
