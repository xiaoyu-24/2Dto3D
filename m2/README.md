# 第 5 步 M2 批量与确认验收

2026-11-21（北京时间）。独立 Compose 项目 `drawing-step5-qa`，MySQL 8.4.10，网页 <http://127.0.0.1:8085>。基线为 `c815621` 加本地已验收的第 4 步改动。浏览器均为 Microsoft Edge 157.0.4341.60，Playwright 显式使用 `channel="msedge"`。

审核记录：首轮因 B040 包装说明可被派生为产品而未通过。该阻塞已修复并完成专项复验，见文末「包装候选阻塞修复复验」。下文保留首轮验收数据；Git 提交和第 6 步仍暂缓。

## 样本与任务

| 批次 #3 原图 | 原页 | 文件任务／共享读图阶段 | 产品任务及结果 |
|---|---|---|---|
| `B040108M0-2XX2XX01.pdf`，160,527 字节 | 2 页，0°／0° | #3／#7 | #6 模板待确认。#10 具体料号 `B040108M0-2102AA01` 已确认并续办到等待建模作业 |
| `WLT2F9-141XX0200.pdf`，295,100 字节 | 2 页，270°／270° | #4／#8 | #7 模板和 #11 具体料号 `WLT2F9-1415A0200` 已确认并续办到等待建模作业 |
| `测试文件.pdf`，104,504 字节 | 1 页，0° | #5／#9 | #8 `M12A04-03-301-M16 / A0-M2-8 / G1`，四针 24.70 mm。#9 `M12A05-03-301-M16 / A0-M2-9 / G1`，五针 26.70 mm。两个版本均在 QA 发布 |

原图 SHA-256 依次为 `a99425986b8e6e483afb18b3a63f5dde00d78f9d0912f651333732a101050173`、`15f5aef63a2e3f1b55ad6362e79bc57c73c3587d4be8b9e096f8853028bf4743`、`be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c`。原文、页图、裁图及阶段检查点均可按指纹追溯。

- Edge 一次选中并上传三份原图。按原批次再次上传返回相同文件和任务 ID。分别显示文件与产品进度。六个产品任务的创建请求均复投，第二次返回相同任务 ID。
- 三份文件各有一条成功的联合读图。双页的第 1 页为产品，第 2 页为包装。四针、五针共享读图 `6c566ab558e68db1137a0491f3eb12282db81526d1512722346fa72e6336c275`。模型将五针长度误配为 24.70 mm，原图复核识别 `5P:26.70`。产品确认引用所选料号、针位和总长依据。五针另确认配合端采用 A-code 5pin 表。
- 通过原页证据按钮核对 10 处高亮和局部图。WLT 第 1 页核对料号表及 14.00 ±0.20 mm 侧视尺寸，第 2 页核对包装说明。三个裁图像素框分别为 `[1485,630,338,167]`、`[141,226,414,438]`、`[725,1032,342,180]`。局部图 PNG 与接口和数据库 SHA-256 一致，和整页对应区域的像素差异小于 0.26%。M12 四针、五针的料号行和长度引出线各自定位，四个局部图与完整原页的对应像素完全相同。
- 确认记录保留操作者、数据库 UTC 时间、答案、原页矩形与裁图指纹。网页提交后重复提交同一答案，全部返回复用。#7–#11 各有一次 `confirmation_resumed`。四条并发相同确认的自动测试只返回一次续办，错误视向选项返回 422，试图覆盖历史答案返回 409。

## 失败隔离与重试

1. B040 首次图片能力题把 `418` 读成 `478`。文件先保存两页页图，阶段 #7 进入失败。WLT 和 M12 继续完成。Edge 点击「只重试失败项」仅重入 #3，使用同渠道后续通过的能力题和已保存页图，完成两页联合识读。阶段 #7 领取两次，其余文件读图阶段各一次。
2. 四针 #8 的首次 CAD 选择达到 128 token 上限。保留原请求与费用记录。Edge 点击「重试此项」仅重入 #8。已结束的请求允许一个有独立记录的重试。选型通过后从冻结依据完成建模，独立验收 HTML。随后 QA 注入一次 STEP 导出不可用。15:03:47 起 #8 显示「部分完成」，HTML 可下载，STEP 与报告返回 404。
3. 五针 #9 在 #8 部分完成、B040 和 WLT 产品待确认期间继续运行。15:06:46 完成同几何的离线 Edge、STEP 独立回读、报告与 QA 发布。两种针数各通过 15 项回读检查，本体分别为 7／8 个有效实体，另有独立螺母 1 个。螺母展示位移和安装变换分别记录。
4. Edge 从历史页下载 #8 的部分完成 HTML 和 #9 的全部交付，并打包。随后点击「只重试失败项」，返回 `[8]`。#8 从 MySQL 恢复几何快照和已验收 HTML，只执行 STEP 导出和回读，15:19:39 发布。#8 的几何和 HTML 哈希不变，`cad_checkpoint_reused` 只出现一次。本次 STEP 重试增加模型调用 0 次。#9 的事件、模型调用、几何和三项成品在重试前后完全一致，阶段领取仍是一次。

`step-check.json` 记录实际回读版本为 FreeCAD 1.1.4／OCCT 7.8.1。CAD 使用锁定镜像 `sha256:3f38a3ed15f289551177a9a5540853932d6cedf631fd71e3ce690ead80429373`，UID 65534，根文件系统只读，网络接口仅 `lo`。HTML 以 `file://` 断网打开，读取实际 WebGL 像素，操作旋转、缩放、两端视角、螺母与触点显隐。每次验收的 HTTP(S) 请求与脚本异常均为 0。

## 真实模型调用

使用第 4 步获准的 DeepSeek 官方渠道、`deepseek-flash`、Harness SDK／Runtime `0.2.2rc2`，沿用单任务 CNY 10 和项目日预算 CNY 500。表中费用是项目既有计费上界，输入栏格式为普通输入加缓存读取，时间为毫秒。

| 任务／阶段 | 请求 | 供应商请求 ID | 输入／输出 | 耗时 | CNY 上界 |
|---|---|---|---|---:|---:|
| #3／#7 | 能力题未通过 | `95f691737e2a4be3c5e68cc32c22b824` | 633／30 | 7,697 | 0.0016 |
| #3／#7 | 两页联合读图 | `a109c15a7052d0136aa5ce37276b7e50` | 2749+128／1473 | 13,616 | 0.0176 |
| #4／#8 | 能力题通过 | `48ffa116d2cf15a6448d5163cf68bf1f` | 632／30 | 4,157 | 0.0016 |
| #4／#8 | 两页联合读图 | `9af3790eea80d18e485a48d4033e2fcb` | 2868／1077 | 13,003 | 0.0144 |
| #5／#9 | 能力题通过 | `eb882c3cedf44e2d3d867a4ad2603113` | 631／21 | 5,526 | 0.0015 |
| #5／#9 | 多型号读图 | `2a4046c906b985d7d9403d6f847d5399` | 1583+128／1207 | 10,709 | 0.0131 |
| #8／#14 | 作业选择截断 | `3021138cab8dc4df8eba5bfe8c58a51c` | 386／128 | 4,383 | 0.0018 |
| #8／#14 | 作业选择重试通过 | `f0e3dc6efdc46ea28237b080b560fa19` | 313／8 | 4,000 | 0.0007 |
| #9／#15 | 作业选择通过 | `a033f86730eb167695d5f789c62dde8a` | 312／8 | 3,676 | 0.0007 |

本步 9 次供应商调用，HTTP 均为 200，费用上界合计 CNY 0.0530。原首件 2 笔读图模型记录、1 笔作业选择凭证随旧数据保留。B040 复用已通过能力记录 1 次和页图检查点 1 次。三份共享读图被六个产品任务引用，每个产品选择的重复提交、确认的续办和 STEP 局部重试均未新增视觉调用。四针最后一次重试额外复用几何与 HTML 各一次。

## 下载与历史指纹

以第 4 步 QA 库的 `003_m1_delivery` 为源。使用 `mysqldump` 只读导出库结构与数据，单独导出会话表结构。导入 `drawing-step5-qa` 后逐步升级至 `006_m2_report_binding`，重复执行 `alembic upgrade head` 成功。首件仍为任务 #2、批次 #2、`A0-M1.3 / G1`，历史版本 ID #1。Edge 分别从旧任务页和新版历史查询下载三项文件，大小及 SHA-256 与第 4 步基线完全一致。

| 任务／版本 ID | 文件 | 字节 | SHA-256 |
|---|---|---:|---|
| #2／#1 | HTML | 1,329,332 | `69440e5f1d28958c37938958de8bc78a24e140dee96ae68fc368ab04490b2740` |
| #2／#1 | STEP | 1,013,783 | `5ade87ca1537c1eea21eda2c89c48710a0ef57d63886b0bb1ff6d04bec696403` |
| #2／#1 | 验收报告 | 7,236 | `ed07e47bc0cc2111ea2588bfc951605f11c1fa0f1e9afb36c92a029ffc1861c3` |
| #8／#2 | HTML | 1,329,314 | `c6758b101cd9900edf5ed64f83a37207207d3f80f415831c84f5901f99c1ed84` |
| #8／#2 | STEP | 1,013,765 | `cabbfb027916740da61da4820f9b71a59b4ef0abd7da63631bb3b325f8bcda0f` |
| #8／#2 | 验收报告 | 3,859 | `3cb2099e16ade51040a2e7ca97fdcd20538b75e152d81fde46b1be0f1e66ef86` |
| #9／#3 | HTML | 1,448,231 | `661487ca6011a51f3ec25c6766e32f91f5dd15869943472ad6d39729e47afc3e` |
| #9／#3 | STEP | 1,034,826 | `203c1bdfcc13ff8cf11d370ea590b16820d6703f9632e56db868215d42d5a623` |
| #9／#3 | 验收报告 | 3,907 | `5cf8ae4faabeb262dec9ea605db2fffe9eed7eafcec3798ff7506126e86dc998` |

#8、#9 的冻结几何分别为 `0495e7509659d61a5879469d7281af5b12fea1aa461fd3332efbe31feb8608bd`、`d710f6a861c3a70589b7f8cb957360706d2c789bb5eed2df317f251280987f92`。原首件为 `cee5658086f47e6a0de23a6cf98cfbb91201bdb4910de2e051f264b82ab38421`。MySQL 独立执行 `SHA2(body, 256)` 和 `OCTET_LENGTH(body)`，结果与网页重下载一致。

按批次、料号和版本查到对应历史版本。部分完成 ZIP 含 4 项，821,320 字节，SHA-256 `4e0de4c66775cc2aa59359a2cf22496548b3b5d3946ee06e57375d84f3095aaa`。最终网页下载 ZIP 含 6 项，1,088,552 字节，SHA-256 `e2c9c7ce0e734fa220ff9ff8d3bd35f2310a0ca0d59e155d5cd811c53180b12c`。两次均打开 ZIP，读取清单，逐件核对路径、字节和 SHA-256。ZIP 生成时间参与容器字节，下载校验使用此次留存的 ZIP 与每个成员指纹。

独立账号仅加入项目 #2。登录 Edge 后访问项目 #1 的版本列表、版本 #3、交付、原页图及旧任务下载均返回 404。向项目 #1 打包和把项目 #1 版本混入项目 #2 的 ZIP 均返回 404。未登录的自动测试返回 401。

## 命令与退出码

以下命令使用仓库根目录。`$env:WEB_PORT='8085'`，测试设置 `$env:M12_PDF_PATH='D:/desktop/测试文件.pdf'`。浏览器脚本用 `.env` 登录，实际目录和细分输入记录在 `output/m2-step5/`。

| 实际命令或调用 | 退出码 | 结果 |
|---|---:|---|
| `git status --short --branch` | 0 | `codex/m0-linux-probes`，保留已有第 4 步工作区改动 |
| `docker compose -p drawing-step5-qa up -d --wait mysql` | 0 | 独立 MySQL 与独立命名卷 |
| `docker compose -p drawing-step4-qa exec -T mysql sh -ec …` 执行 `mysqldump --single-transaction --no-tablespaces --skip-add-drop-table --hex-blob --ignore-table=drawings.user_session drawings`，另导出 `--no-data drawings user_session`。通过 Python 字节管道导入 `drawing-step5-qa` 的 `mysql -uroot drawings` | 0、0／0、0 | 数据 9,502,921 字节，会话表结构 1,817 字节。导入后确认版本 `003_m1_delivery` 与首件几何指纹 |
| `docker compose -p drawing-step5-qa --progress plain build api worker vision migrate`，以及最终的 `build api worker migrate web` | 0 | API、Worker、视觉服务及 TypeScript／Vite 构建通过。两次预跑因 Docker Hub EOF 退出 1，重试成功 |
| `docker compose -p drawing-step5-qa run --rm --no-deps -T migrate alembic upgrade head` | 0 | 覆盖实际升级与相同迁移重复运行 |
| `docker compose -p drawing-step5-qa up -d --wait --no-deps api worker web` | 0 | 最终服务可用 |
| `python m2/qa_browser.py upload` → `python m2/qa_browser.py upload --batch 3` | 1 → 0 | 首次浏览器上传成功，脚本在回读相对 URL 时退出。修正后复用三项回执 |
| `python m2/qa_browser.py derive --batch 3`，`variant --batch 3 --template B040108M0-2XX2XX01 --part B040108M0-2102AA01` 和 `variant --batch 3 --template WLT2F9-141XX0200 --part WLT2F9-1415A0200` | 0、0、0 | 网页选择、派生、复投和续办 |
| `python m2/qa_browser.py confirm --batch 3 --part <料号> --answer <原图核对结论>` | 0 | 四针、五针和 WLT 模板分别提交确认。复投同一答案返回复用，原页局部可见 |
| `python m2/qa_browser.py retry --batch 3 --part M12A04-03-301-M16`，之后 `python m2/qa_browser.py retry --batch 3` | 0、0 | 单项和批量操作均仅重入 #8 |
| `python -u m2/cad_worker.py --project drawing-step5-qa --output output/m2-step5/cad --once`，注入轮附 `--qa-fail-step M12A04-03-301-M16 --qa-fail-attempts 2` | 0 | 首次调用将选择失败持久化，故障轮持久化部分完成。成功轮的容器建模／HTML 导出／STEP 导出／回读命令均退出 0。`--once` 的 0 表示已处理该次领取，业务状态以数据库回执为准 |
| `python m2/qa_browser.py evidence --batch 3`、`deliveries --batch 3 --delivery-state partial`、`deliveries --batch 3 --delivery-state published --output output/m2-step5/final-head` | 0、0、0 | 十处证据、四件部分完成 ZIP、六件最终 ZIP。早期脚本的定位和过严像素断言退出 1，修正后通过 |
| `python m1/qa_browser.py download http://127.0.0.1:8085 output/m2-step5/final/legacy-downloads output/m1-step4-evidence/db-files.json .env`，`python m2/qa_browser.py legacy --output output/m2-step5/final/legacy-history` | 0、0 | 旧接口、历史接口、原字节三项回归。早期脚本两次遇到重复定位，后来限定范围 |
| `docker compose -p drawing-step5-qa -f compose.yaml -f compose.test.yaml run --rm -T tests` | 0 | 最终 31 项通过，包括 21 项原回归和 10 项 M2 回归。保留一条 Starlette `httpx` 弃用提示。定向预跑的三条非零裁剪框用例预期算错，修正后通过 |
| `git diff --check`，`python -m compileall -q backend/app backend/migrations backend/tests m1 m2`，`docker compose -p drawing-step5-qa -f compose.m0.yaml config --quiet`，API 容器 `python -m pip check`，Web 容器 `nginx -t` | 均 0 | 空白、语法、配置和依赖检查通过 |

证据目录为 `output/m2-step5/`。`mysql-audit.json` 包含数据库哈希、事件和供应商请求。`final-db-state.json` 包含最终产品与确认记录。`page-evidence.json` 和 `final-head/page-evidence.json` 含坐标与裁图对照。`partial/` 与 `final-head/published/` 保存网页下载、ZIP 和清单。`permission-audit.json` 保存跨项目拒绝。`cad/` 含容器命令及退出码、独立实测和网页截图。初次旧下载复跑更新了 `output/m1-step4-evidence/edge-published.png`，后续截图写入本步输出目录。

## 验收范围

首轮验证覆盖指定三份原图的批量工作流、跨页共享读图、原页定位、型号确认、失败隔离、局部重试、历史权限、部分完成及 ZIP，但遗漏了包装说明误入产品候选的负向用例；审核因此未通过。本次补充修复与专项复验记录如下。CAD 成品验收覆盖 M12 四针和五针。B040 和 WLT 的产品选型已保存，在各自作业与独立清单就绪前停在「等待建模作业」。通用自动建模、几何修订自动修复、M3 供应商故障矩阵、批量负载与盲测、密封及量产适用性未验证。所列发布状态发生在独立 QA 库。

## 包装候选阻塞修复复验

2026-11-21，仍在独立 Compose 项目 `drawing-step5-qa` 执行。Edge 修复前实测可勾选 `包装（同料号，卷盘/载带）`，且「派生选中型号」按钮可用；未在业务 QA 库创建错误任务。新增测试在随机临时 MySQL 库复现后端返回 200 并写入任务的问题。

根因是 `sample_evidence` 保留所有模型候选，派生接口直接信任历史候选。现在 `m2_sample.eligible_products` 对这两份已核定原图仅保留完整模板或符合原图配置表的具体料号。新增候选和 `m2_routes.evidence_for` 的历史读取共用此校验，派生入口在任何写入前验证完整请求；包装、带解释的料号标签、错误配置和其他图纸家族的料号均返回 422。`m2_work` 的读图提示同时明确包装说明应作为相应料号的 findings 和页区证据。其他原图家族沿用既有行为。

历史检查点无需迁移或重读。模型原始 `reading`、包装页区域及裁图仍保留；历史产品候选在 API 读取时筛选，不改写数据库里的证据字节或原哈希。三份共享读图仍为：

| 文件任务 | 共享读图 SHA-256 |
|---|---|
| B040 #3 | `059f2658b46dabc90868f8e7f5678a1ea7f5e49451d26c6b041b41d987632077` |
| WLT #4 | `4b418c6074f748cc4f88aa85db94adea53511d73d00a13beb32a7a0716b614ff` |
| M12 #5 | `6c566ab558e68db1137a0491f3eb12282db81526d1512722346fa72e6336c275` |

复验结果：

- 新增 6 项回归覆盖 B040／WLT 的新候选筛选、已持久化误报的读取、直接及混合非法派生无副作用、合法模板和具体料号创建与幂等复用。修复前 6 项均失败，修复后均通过；完整测试 37 项通过。
- Edge 从正常登录、批次 #3、文件详情进入，B040 仅显示 `B040108M0-2XX2XX01`，包装和两种附带解释的标签均无复选框。WLT 保留本图模板，M12 保留四针及五针。
- 经 Edge 登录会话提交 18 次单独或混合非法选择，全部返回 422。混合请求首项使用尚未派生的合法料号，整次拒绝后没有新增该产品。直接请求无需依赖前端筛选也被拦截。
- 通过网页选择 B040 `B040108M0-2102AA01`、WLT `WLT2F9-1415A0200`、M12 四针及五针，分别重复派生两次。8 个回执全部复用任务 #10、#11、#8、#9。
- B040 第 2 页包装表和 WLT 第 2 页 270° 包装区域仍可点击，整页高亮、局部图、像素坐标及图片 SHA-256 校验通过。
- 前后 MySQL 审计完全一致：源文件 4、任务 10、产品 6、阶段 16、事件 78、确认 10、版本 3、模型调用记录 11、文件字节记录 118。阶段状态、领取次数、输入输出指纹、产品及三份历史版本的几何和产物指纹均未改变。本轮新增真实模型调用 **0**，没有重新执行 CAD。
- Edge 从历史查询重新下载第 4 步首件 `A0-M1.3 / G1` 的 HTML、STEP、报告，字节数及 SHA-256 与上表基线完全一致。

以下命令均在仓库根目录执行。测试先设置 `$env:M12_PDF_PATH='D:/desktop/测试文件.pdf'`，QA 服务使用 `$env:WEB_PORT='8085'`。

| 实际命令或检查 | 退出码 | 结果 |
|---|---:|---|
| `git status --short --branch` | 0 | 沿用 `codex/m0-linux-probes`，保留既有工作区改动 |
| `docker compose -p drawing-step5-qa -f compose.yaml -f compose.test.yaml run --rm -T tests python -m pytest -q -p no:cacheprovider tests/test_m2.py -k 'new_candidates or legacy_candidates or derive_rejects'` | 修复前 1，修复后 0 | 6 failed → 6 passed |
| `docker compose -p drawing-step5-qa -f compose.yaml -f compose.test.yaml run --rm -T tests` | 0 | 37 passed，22.10 秒；保留既有 Starlette `httpx` 弃用提示 |
| `docker compose -p drawing-step5-qa --progress plain build api worker` | 0 | QA 后端镜像构建通过 |
| `docker compose -p drawing-step5-qa up -d --wait --no-deps api worker` | 0 | 仅更新独立 QA 的 API／Worker |
| `python m2/qa_browser.py candidates --batch 3 --output output/m2-step5-review-fix` | 0 | Edge 157.0.4341.60；18 次拒绝、8 次复用，任务与证据不变 |
| `python m2/qa_browser.py legacy --output output/m2-step5-review-fix/legacy` | 0 | 首件三项原始下载回归通过 |
| `docker compose -p drawing-step5-qa exec -T api python -` 只读 SQL 审计及本地 JSON 比较 | 0 | 前后计数、阶段、版本和产品一致；审计初跑因 `RowMapping` 未转字典退出 1，修正序列化后通过，全程只读 |
| `git diff --check`；`python -m compileall -q backend/app backend/tests m2` | 均 0 | 空白及 Python 语法检查通过 |

证据目录 `output/m2-step5-review-fix/`：`before.json` 和 `edge-before-packaging.png` 保存修复前复现；`tests-before.log`、`tests-targeted.log`、`tests-full.log` 保存先失败后通过；`candidate-selection.json`、`edge-candidates-3.png`、`edge-candidates-4.png`、`edge-candidates-5.png` 保存浏览器复验；`db-before.json`、`db-after.json` 和 `db-compare.log` 保存无副作用对照；`legacy/` 保存首件原字节下载。此次仅完成阻塞修复及相关回归，未重新运行付费读图或完整 CAD 建模，未执行 Git 提交、推送或正式发布。
