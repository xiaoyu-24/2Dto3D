# 第 6 步 M3 故障与发布验收

验证日期：2026-11-21，北京时间。基线为本地提交 `9fdc167`，开工时工作区干净，继续使用 `codex/m0-linux-probes`。所有迁移、故障注入及测试发布均在独立 Compose 项目 `drawing-step6-qa`，网页 `http://127.0.0.1:8086`。Step 4／5 运行环境未修改。浏览器为 Microsoft Edge 157.0.4341.60，所有启动均显式 `channel="msedge"`。

## 结论与边界

首轮虽有 48 项自动测试通过，独立审核仍发现「主路由发现失败未查询备用」及「旧 fence 覆盖发布暂存」两个阻塞，第 6 步验收因此未通过。以下保留首轮证据；两个阻塞已修复并完成专项复验，详见文末「审核阻塞修复复验」。修复后完整测试 **65 passed**，退出码 0，47.35 秒。

首轮已实际覆盖真实 Worker 强杀与自然租约过期接管、数据库旧 fence 写入隔离、模型调用阶段的模拟主备故障矩阵、真实 STEP 单项故障、事务失败及同 fence 回执恢复、真实 STEP 错型号和镜像孔位拒绝、G2 全量重验，以及历史下载、包装候选、权限和 ZIP。两类错误几何发布数均为 **0**；首轮测试没有覆盖发现阶段的独立备用服务和跨 fence 的本地暂存保护，不能据 48 项通过推断这两个行为正确。

真实联调使用已获准的官方 DeepSeek `deepseek-flash` 和现有 Harness SDK／Runtime 0.2.2rc2，没有新增真实供应商或备用凭据。真实独立备用渠道未配置，因此真实主备切换不作已验证声明；429／503／凭据失效、备用能力准入、预算及待对账完整矩阵使用隔离 HTTP 模拟服务。真实联调另出现 1 笔无供应商请求 ID 的调用，保留待对账和 CNY 2.0041 预留，未重发。本轮未重复付费视觉读图，复用 Step 5 冻结依据；真实 CAD 与独立 STEP 回读均实际执行。板件是有明确基准的测试夹具，不代表通用板件自动建模能力。

交付范围为离线单文件 3D HTML、STEP、验收报告。未执行 Git 提交、推送、生产部署或正式发布；未删除文件。以下 `published` 均指独立 QA 数据库的测试发布。

## 实施内容

- `007_m3_recovery` 在原 `006_m2_report_binding` 上增加渠道状态、唯一发布回执、拒绝几何归档及模型响应完成时间。保留旧版本原字节，不重写 Step 4／5 的交付。
- `m3_models` 复用既有调用账本和项目／任务预算锁，每渠道明确失败最多两次。429 尊重秒数或 HTTP 日期 `Retry-After`；无头时使用 1–1.5 秒有限退避与抖动。等待写入队列 `ready_at`，释放 Worker；300 秒阶段窗口内无法等待时转合格备用，渠道进入冷却。401／403 停用该项目渠道。冷却后仅一个半开探针，备用必须有当天通过的能力凭证。未知发送结果保留预留，不自动重复或切换。
- 默认仍使用现有 `M1_HARNESS_URL`。仅在明确配置 `M3_BACKUP_HARNESS_URL` 时发现独立备用 Harness，路由指纹相同的不作为备用。配置属于运维信任边界，不提供向任意外部地址写入的网页接口。项目准入和日预算继续沿用原设置。
- CAD 执行器在提交冻结几何、HTML 和 STEP 前，将已验收字节写入指定 `--output` 下的 `pending/<stage>-<input_sha>/<fence>/`，通过 `fsync` 和同目录原子替换持久保存。不同 fence 写入不同目录，恢复选择不高于当前 fence 的最高有效记录，并在重送前以当前 fence 再暂存。兼容读取旧版不含 fence 子目录的暂存；目录必须位于保留的磁盘或持久卷。
- 发布事务同写版本、三项文件指纹、阶段完成、事件和 `(version_id, geometry_revision)` 唯一回执。同 fence 的重送只返回原回执；旧 fence 不可写入。只读 CLI `receipt` 以任务、阶段、输入和重算 STEP 指纹匹配数据库权威回执，返回实际发布 fence，可恢复旧暂存对应的接管后发布。
- CLI `revision` 仅允许已拒绝且等待中的几何：归档 G1 审计，创建带新依据指纹的 G2 和新阶段幂等键，清空当前修订全部产物资格，重新执行 HTML／STEP／报告门禁。已发布修订不可通过该入口改写。
- `compose.m3-qa.yaml`、`m3/qa_*.py` 仅用于独立 QA。故障注入不进入生产 API。板件冻结使用 QA 专用入口，实际发布和下载仍经过现有 M2 门禁。

## Worker 中断与接管

任务 #17、读图阶段 #25。QA 让真实 Worker 在 `save_read` 写入原页检查点后等待；主线程等待期间使用真实每 20 秒续租逻辑。

| 观测（北京时间） | fence | 检查点／结果 |
|---|---:|---|
| 18:44:07.913 观察到原页检查点 | 1 | `505e61a067f9f87b14c9b5e7616ac46ca6f2a9c5b9a32187ada7d8ca229c18d7`；租约至 18:45:35.147 |
| 18:44:25.547 观察到正常续租 | 1 | 同一检查点；租约延长至 18:45:55.158 |
| 随后 `docker kill --signal KILL drawing-step6-qa-interrupted` | 1 | 容器保留，未清除；另一 Worker 进程启动等待 |
| 18:45:58.265 完成自然过期接管 | 2 | `3355191293cb231786ad628b0e237d7704e26875f53b81169e1be8a635ad3c70`，共享读图完成 |

没有通过修改数据库时钟或提前缩短租约来替代自然过期。阶段领取次数 2，`evidence_saved` 仅 1 次、`reclaimed` 仅 1 次。接管复用原页及裁图，只执行一次模拟能力题和一次模拟联合读图。旧执行器的 `save_read` 返回 False，最终检查点未变化。CAD 阶段 #21 的 fence 1 在接管至 fence 3 并完成后，再分别重送冻结几何、HTML、STEP，全部因租约失效拒绝，数据库无变化。

## 模型故障矩阵

服务为独立容器 `simulator`，真实 HTTP 协议和相同 `recorded_call`／预算／熔断实现。未把模拟调用计为真实供应商成功。`model-matrix-final.json` 保留每个请求时间、载荷、原检查点、任务、调用、用量和阶段事件。每组重新建独立项目隔离熔断状态。

| 用例 | 实际请求序列 | 判定 |
|---|---|---|
| 429 | 主能力题成功 → 主读图 429 → 至少等待 1 秒 → 主读图 429 → 备能力题成功 → 备读图成功 | 每渠道最多两次；原检查点不变；冷却后跳过主渠道 |
| 503 | 主能力题成功 → 主读图 503 → 1–1.5 秒退避 → 主读图 503 → 备能力题成功 → 备读图成功 | 有限退避、抖动和合格备用 |
| 凭据失效 | 主能力题成功 → 主读图 401 → 备能力题成功 → 备读图成功 | 主渠道停用；自动测试另覆盖 403 |
| 备用不合格 | 主读图 401 → 备能力题结果不通过 | 不发送备读图；返回无合格渠道 |
| 超长 Retry-After | 主读图 429／600 秒 → 合格备用 | 不超过 300 秒阶段窗口，不提前重发主请求 |
| 待对账 | 主读图已接收后断开连接 | 保留 NULL 结果及费用预留；重复执行不重发、不切换 |
| 预算不足 | 任务预算 CNY 1，小于本次调用预留 | HTTP 模型请求 0 次 |

自动测试另验证：HTTP 日期 Retry-After、同项目冷却期跳过、半开时两个执行器仅一个获准、主能力题错误后改用通过能力题的备用、Harness 返回 `http_status=null` 时保留待对账且只发送一次。初轮和最终完整测试均保留原权限、确认与幂等用例。

真实联调使用单任务 CNY 10／项目日预算 CNY 500，六次供应商调用如下。响应不明的 #15 未对同一请求重发；后续验收使用独立新任务 #16，留存所有调用记录。

| 阶段／任务 | 用途 | 请求 ID | 输入／输出 token | 毫秒 | CNY 上界 |
|---|---|---|---:|---:|---:|
| #21／#14 | 四针作业选择 | `2fe7f3de5aafbfb701c153a8195c35d6` | 312／8 | 6366 | 0.0007 |
| #22／#15 | 五针作业选择，待对账 | 未收到 | 未收到 | 23193 | 未核销，预留 2.0041 |
| #23／#16 | 重新验证渠道能力 | `f85b0cadcbc5fac0dc7c7db79da9d2c9` | 638／30 | 3338 | 0.0016 |
| #23／#16 | 五针作业选择 | `242b5902e9b056c8db66e0fdcd3eaf18` | 311／8 | 2447 | 0.0007 |
| #26／#18 G1 | 错针数负例作业选择 | `d83248de1fe7bcf8ca47683b31b6bab2` | 313／8 | 2534 | 0.0007 |
| #27／#18 G2 | 修订作业选择 | `54e3e040d08695d761255e679e0e597c` | 312／8 | 2414 | 0.0007 |

已核销五笔费用上界合计 CNY 0.0044，另有上述一笔预留。模拟服务调用有独立 `qa-simulated` 标识，不计入该表。真实备用凭据、供应商真实 429／503、真实密钥停用未直接触发，仅按隔离矩阵覆盖。

## STEP 故障、数据库故障和发布恢复

- 批次 #4，四针 #14／版本 #4 `A0-M3-partial / G1` 完成真实建模和离线 Edge HTML 验收后，在 STEP 导出前注入失败。版本为 partial，HTML 文件名和下载链接均标明部分完成，STEP 与报告均 404。
- 四针等待时，五针 #16／版本 #6 `A0-M3-lost2 / G1` 继续完成全部建模、离线 HTML 和 15 项 STEP 独立回读。发布事务已经提交后，执行器在保存回执前 `os._exit(83)`。另起进程 `recover` 从持久暂存找到原发布回执；同 STEP 再提交返回 `reused=true`，数据库前后完全一致。
- Edge 在四针 partial／五针 published 的混合状态下分别下载并解包 ZIP，4 个交付成员逐件重算大小与 SHA-256。ZIP 821,368 字节，SHA-256 `e433c64af8b196d7be08da0b11aee028ec3daffd9c042d9f74fe880d162c7021`。
- Edge 单项重试仅领取 #14，fence 1 → 2。沿用原几何和 HTML，只执行 STEP 导出与独立回读。临近发布事务的唯一回执 INSERT 注入写入异常，整个版本／文件引用／发布事件事务回滚；仍为 partial。已验收 STEP 和验收凭证已在磁盘 `pending/21-…/step.json`，没有丢失。
- Edge 再单项重试 #14，fence 2 → 3。新执行器仅恢复持久暂存的 STEP 并提交，输出 `resumed persisted STEP: published`。此次模型、建模、HTML 导出、STEP 导出和独立回读新增次数均 **0**。与故障前相比，几何和 HTML 指纹完全相同。
- #14／#16／#18／#19 各仅一条当前修订发布回执；其 HTML／STEP／报告固定列各只有一个槽位。#14、#16 各只有一次 `published` 事件。重复发布不增加事件、调用或任何业务记录。完整重启 API／Worker 后再次下载验证通过。

## 真实 STEP 几何负例与修订

FreeCAD 1.1.4／OCCT 7.8.1，在既有锁定 CAD 镜像运行，UID 65534、根只读、无网络，仅 `lo`。每个 STEP 验收都由另一个 CAD 进程读取实际 STEP 字节。

1. **任务 #18／版本 #7／G1，四针任务误用五针几何。** 采用 Step 5 五针真实 BREP 重新导出 STEP，并将元数据设为当前四针任务，避免仅靠文件名或元数据拦截。独立验收实测 5 针、26.70 mm，对照冻结四针清单 4 针、24.70 ±0.25 mm；针数、长度、部件数及四针中心检查失败。`verify.py` 退出 **1**，版本 rejected。先前通过的 HTML 资格同时撤回；Edge 验证三种下载均 404，ZIP 复选框禁用。G2 改回真实四针几何，再从同一 G2 冻结几何导出、验收 HTML 和 STEP，生成报告后发布；STEP 验收退出 **0**。
2. **任务 #19／版本 #8／G1，非对称板件镜像。** 夹具 40×30×3 mm，板中心原点，正面观察 +X 向右、+Y 向上、+Z 朝正面。三孔按不同半径识别：r1 在 (-12,-6)、r1.5 在 (7,4)、r2 在 (3,-9) mm。镜像 STEP 独立读取为 (12,-6)、(-7,4)、(-3,-9)，外形／有效实体／身份均正确，但有符号孔位不符；退出 **1** 并 rejected，Edge 验证全撤回。G2 恢复正确孔位，独立回读退出 **0**，离线 Edge 可见、旋转、缩放、视角和显隐通过后，同修订三项成品发布。

G1 被归档在 `m3_revision.evidence`，保留其指纹、错误 STEP 与实测结果，不改写为通过。创建 G2 时全部当前下载资格清空，冻结依据和新阶段幂等键均变化。G1 发布回执数 **0**；G2 每项 **1**。板件通过 QA 夹具专用冻结入口，未扩展生产建模家族。

## 下载、权限和哈希

最终 Edge 从正常网页历史入口下载四个新版本共 12 个文件，再从网页下载 ZIP 并逐件解包比较原字节、大小和 SHA-256。最终 ZIP 1,636,166 字节，SHA-256 `c0a76f608a9faaaf91aed0debe9cb3af476506950fee81ebb963d4b2dc5c5b4a`。

| 任务／版本／修订 | 文件 | 字节 | SHA-256 |
|---|---|---:|---|
| #14／#4／G1 | HTML | 1,329,320 | `3f83900471fc0116df489ea8532a1fb55fab6f3ad11e3f05c4b8642db2a32263` |
| 同上 | STEP | 1,013,771 | `c153fed85e9605c594457f637f159703d819bbf8c8e259f326ab6fdb537a6a0c` |
| 同上 | 报告 | 3,865 | `2f9515bf847ca1aee88220552ec27fa0bebe6d52fa6dc639c6500c706b18e784` |
| #16／#6／G1 | HTML | 1,448,235 | `4ac72f962bcfccfa9880e2af6af3c0ef115726cb1a27ef9fe45baa678ceef9e1` |
| 同上 | STEP | 1,034,830 | `7760ec04455dd3a79d0fd665cff08d60705e74f2ae96df46840061358b1f2469` |
| 同上 | 报告 | 3,911 | `a5d601f42395b72d748131321bd285e7ea7328e4f62b5ecc41ef8fe0f7601f07` |
| #18／#7／G2 | HTML | 1,329,323 | `04af5d8b85212dbfb5e5700f16b3655d7bec43d84a90b999cee4ded3de8af2b6` |
| 同上 | STEP | 1,013,774 | `8ca8fb4f104f61ba41d015da5f37ad2fa367712b4911710205cb5b513c6c8cba` |
| 同上 | 报告 | 3,868 | `c9eebb6df0bf70fd5015723413e5017ae91c14504040f6308f72e9fc2f528860` |
| #19／#8／G2 | HTML | 44,447 | `197844614cd79aac43b49257fd5baea163f92bf89628cfcbefd6d6a6c0bfdd12` |
| 同上 | STEP | 11,560 | `d121b4a266717f8929f092d554f544f10d3cf196985f8df7c02dd2425b82da12` |
| 同上 | 报告 | 1,253 | `c631e5b402244c74222698e466554321a28c8d2efde519937daa5fb148c88da2` |

几何指纹：#14 `dcac617231cff3fe7dd15f0471e95484fe1ef30bb6206d9b10ae9f8ce7a0103c`；#16 `b3f4c8890e26cc736e8115c4a43fdeb4c0a484dc7aafcc5e866c15d7567be386`；#18 G2 `8e03f92ee52abc4a7b1ab4a05a448ecf9d2b0c18fd07765187bc26c77944a020`；#19 G2 `9c823b1dcfb791dab906c8746196f07e41090dcec91891fc8b7a758a67641545`。

历史回归：从 Step 5 库只读导出 31,934,139 字节 SQL，导入独立空库，从 006 升级到 007，重复 `upgrade head` 成功。Step 4 任务 #2 `A0-M1.3 / G1` 从旧下载接口和新版历史入口分别重下，HTML 1,329,332 字节／`69440e5f1d28958c37938958de8bc78a24e140dee96ae68fc368ab04490b2740`，STEP 1,013,783／`5ade87ca1537c1eea21eda2c89c48710a0ef57d63886b0bb1ff6d04bec696403`，报告 7,236／`ed07e47bc0cc2111ea2588bfc951605f11c1fa0f1e9afb36c92a029ffc1861c3`，与原始基线一致。Step 5 批次 #3 两个 M12 版本六个文件也重新下载并核对原指纹，ZIP 逐件通过。

Edge 候选回归拒绝 18 次非法请求、复用 8 个合法产品回执；B040 包装说明无复选框，包装页和 WLT 270° 原页高亮仍正确。另一账号只加入独立项目，查询项目 1 的版本、文件、页图、旧下载返回 404，直接和混入外项目版本的 ZIP 请求均 404；未登录和 reader 写入拒绝另由完整测试覆盖。

## 实际命令与退出码

以下在仓库根目录执行；Compose 网页设 `$env:WEB_PORT='8086'`，测试设 `$env:M12_PDF_PATH='D:/desktop/测试文件.pdf'`。

| 命令／实际调用 | 退出码 | 结果 |
|---|---:|---|
| `git status --short --branch`、`git log -4 --oneline` | 0 | 基线 9fdc167，开工前无改动 |
| `python m3/setup_qa.py` | 0 | 独立卷建库；Step 5 只读 dump；空库检查后导入 |
| `docker compose -p drawing-step6-qa -f compose.yaml -f compose.m3-qa.yaml --progress plain build api worker migrate web vision` | 0 | 后端、Harness、TS／Vite 构建通过；后端最终重建也退出 0 |
| `docker compose -p drawing-step6-qa -f compose.yaml -f compose.m3-qa.yaml up -d --wait api worker web simulator` | 0 | 迁移启动 007；服务可用 |
| `docker compose -p drawing-step6-qa -f compose.yaml -f compose.m3-qa.yaml run --rm --no-deps -T migrate alembic upgrade head` | 0 | 实际升级后重复迁移通过 |
| `docker compose -p drawing-step6-qa -f compose.yaml -f compose.m3-qa.yaml -f compose.test.yaml run --rm -T -e M3_SIM_URL=http://simulator:8011 tests` | 0 | 最终 48 passed；前面定向 24 passed、全量 46／47 passed 均通过 |
| `docker compose -p drawing-step6-qa exec -T api python /qa/qa_models.py` | 0 | 完整 HTTP 矩阵，最终输出 `model-matrix-final.json` |
| `python -u m3/qa_interruption.py` | 0 | 检查点、续租、真实 KILL、自然过期接管、旧 fence 拒绝；子命令均 0 |
| `python m3/qa_cad.py seed --label partial --origin 8`、`seed --label lost --origin 9`、`seed --label lost2 --origin 9` | 均 0 | #14、#15、#16 独立验收任务 |
| `python -u m3/qa_cad.py run --fault step-export` | 0 | 执行器成功记录业务失败；以数据库 partial 为判定 |
| `python -u m3/qa_cad.py run --fault lost-receipt` | 首轮 0，第二轮 83 | 首轮 #15 供应商回执不明，未重发；#16 成功提交后按用例强制退出 83 |
| `python m3/qa_cad.py recover` | 0 | 新进程恢复 #16 已提交的唯一回执 |
| `python m2/qa_browser.py retry --url http://127.0.0.1:8086 --batch 4 --part M12A04-03-301-M16 --output output/m3-step6/retry-before-commit` | 0 | Edge 仅重试 #14；第二轮保存到 `retry-persisted` 同样 0 |
| `python -u m3/qa_cad.py run --fault before-commit` | 0 | 记录事务写入异常，整个发布回滚；STEP 与验收凭证已暂存 |
| `python -u m3/qa_cad.py run` | 0 | 新进程仅重送暂存 STEP，发布成功；没有 CAD／模型命令 |
| `python -u m3/qa_geometry.py m12`、`plate` | 0 | 验收脚本预期拒绝；内部独立 STEP 验收退出 **1** |
| `python -u m3/qa_geometry.py m12 --correct`、`plate --correct` | 0 | G2 重新导出和验收；内部 STEP 验收退出 **0** |
| `python m3/qa_browser.py partial --output output/m3-step6/partial`、`rejected --versions 7 --output output/m3-step6/rejected-pins`、`rejected --versions 8 --output output/m3-step6/rejected-plate` | 均 0 | partial 和 rejected 网页／下载资格验证 |
| `python m3/qa_browser.py published --versions 4 6 7 8 --output output/m3-step6/final-downloads` | 0 | 最终 12 件网页下载和 ZIP 逐件指纹验证 |
| `python m3/qa_browser.py permissions --output output/m3-step6/permissions` | 0 | Edge 跨项目读取、下载、页图和两类 ZIP 拒绝 |
| `python m2/qa_browser.py candidates --url http://127.0.0.1:8086 --batch 3 --output output/m3-step6/history-candidates` | 0 | 包装候选、非法派生原子拒绝、合法派生复用 |
| `python m2/qa_browser.py legacy --url http://127.0.0.1:8086 --output output/m3-step6/history-legacy`、`deliveries --url http://127.0.0.1:8086 --batch 3 --delivery-state published --output output/m3-step6/history-m2` | 均 0 | Step 4／5 历史和 ZIP 回归 |
| `python m1/qa_browser.py download http://127.0.0.1:8086 output/m3-step6/legacy-old-interface output/m1-step4-evidence/db-files.json .env` | 初跑 1，修正后 0 | 旧网页脚本正则同时命中历史表；限定实际交付区域后原三项下载通过 |
| `python m3/qa_audit.py` | 0 | 旧 fence 三类写拒绝、发布重送无副作用、唯一发布、几何／HTML 不变、数据库恢复模型差值 0 |
| `git diff --check`、`python -m compileall -q backend/app backend/migrations backend/tests m1 m2 m3`、Compose `config --quiet`、API `python -m pip check`、Web `nginx -t` | 均 0 | 空白、语法、配置和依赖通过 |

较早试跑复用了 Step 5 `deliveries --delivery-state partial` 的固定样本断言，因 #15 当时还未完成退出 1；改用 M3 按版本和状态验证的脚本后通过。不是下载接口失败。测试保留 Starlette 关于 httpx 的已有弃用提示；Compose 提示两个故障测试容器已停止但保留，没有执行移除。`--once`／QA 执行器的 0 表示该次领取已记录，故障是否按预期发生以版本状态、事件、回执和实测记录判断。

## 证据与复验方法

`output/m3-step6/` 保留：`baseline.sql`、`worker-interruption.json`、两轮模型矩阵、完整测试日志、`before-db-recovery.json`／`after-db-recovery.json`、`final-audit.json`；`cad/` 含持久 pending、容器命令和退出码、冻结几何、离线截图、独立 STEP 检查；`geometry/` 含 M12／板件 G1、G2 的真实字节、检查和截图；`partial/`、`rejected-*`、`final-downloads/`、`history-*`、`permissions/` 含网页证据与下载。

持久暂存恢复应使用原 `--output` 目录。手工复验从 `m3/setup_qa.py` 开始只允许空的独立库；若发现库非空会停止，避免覆盖已有验收。真实待对账账目不可通过删除行或换幂等键重发。灾备异机恢复、生产部署、长期负载、多供应商真实主备、通用几何自动修复和量产适用性未验证。

## 审核阻塞修复复验

2026-11-21。继续使用 `drawing-step6-qa`，保留本地未提交改动及审核原证据 `output/m3-receipt-review-f6ed050e/`。不新增数据库迁移，不运行真实模型，不重建 CAD 几何。

### 主路由发现失败

原 `harness` 只有在主 `/route` 成功且路由非空时才请求备用；503 提前返回，空路由则跳过备用。现在 `discover_routes` 分别请求已配置的主、备服务，主失败不阻断备用发现。所有候选都绑定实际 Harness URL，避免选出备用后请求仍发向主地址；健康主服务保持优先，备用发现失败不会妨碍主服务，无可用服务则返回空路由。备用接手仍经过原能力准入、预算和账本幂等。

以两个独立容器 `simulator`、`simulator-backup` 进行 HTTP 实测，备用容器仅接受自己的路由指纹：

| 场景 | QA 任务／阶段／fence | 主服务请求 | 备用服务请求 | 结果 |
|---|---|---|---|---|
| 主 `/route` 503 | #34／#44／1 | `/route` 1 次，模型调用 0 | `/route`、`/probe`、`/read` 各 1 次 | 成功，切换原因 `http_503` |
| 主 `/route` 返回 `route:null` | #35／#45／1 | `/route` 1 次，模型调用 0 | `/route`、`/probe`、`/read` 各 1 次 | 成功，切换原因 `empty_route` |

两例都复用原共享检查点 `6c566ab558e68db1137a0491f3eb12282db81526d1512722346fa72e6336c275`，重复执行新增模型调用 0。`model_route_result` 记录 `alternate=true` 和发现切换原因。四次模型调用全部来自模拟器，不含真实供应商调用。自动测试另覆盖主网络不可达、主正常、备不可用、未配置备用、两侧不可用，以及备用能力不通过／预算不足时不发送读图。

### 迟到执行器覆盖暂存

原路径只有阶段及输入哈希，旧 fence 的 `os.replace` 会先覆盖新 fence 文件，再被数据库拒绝；此外接管后重送只调用 MySQL，没有为新 fence 写入暂存。现在每个 fence 使用独立目录，按数字顺序恢复；重送冻结几何、HTML、STEP 均先写当前 fence 的暂存。发布写入仍严格匹配 fence。回执恢复属于只读查询，用阶段、输入及重算 STEP 哈希核对数据库，不再把旧暂存中的 fence 当成实际发布 fence。

对审核样本 #14／阶段 #21 实测：

- 当前数据库发布 fence=3，版本 #4 `A0-M3-partial / G1`。先保存 fence=3 暂存，再调用旧 fence=2 的完整 `Executor.submit`，后端拒绝旧租约，新暂存的 SHA-256 始终为 `cb5027757c53f104350a6dc66e6132e3ad25ee36be3a494d80d269a3630043ec`。
- 另起 Python 进程执行 `recover`，在 fence=3 目录恢复唯一回执，指向报告 `2f9515bf847ca1aee88220552ec27fa0bebe6d52fa6dc639c6500c706b18e784`。
- 把审核原文件只读复制到另一个旧格式目录，另起进程同样恢复出上述 fence=3 回执。审核原文件及复制件字节不变，SHA-256 均为 `29c6bd669dfde9aea0c5f011104feefa2a80163ef5591963be8ee3c738574049`。
- 回执复验前后的任务、阶段、事件、模型调用、版本、修订及发布表完全一致；模型调用、发布及事件差值均为 **0**。
- 自动回归覆盖冻结几何／HTML／STEP 三种逆序提交、fence 1／2／9／10 并发写、最高有效 fence 恢复、不同输入隔离、错误 STEP 哈希拒绝、旧格式恢复，以及「提交前失败 → 接管重暂存 → 提交后退出 83 → 旧执行器迟到 → 新执行器恢复」完整链路。该恢复链只调用 `inputs` 和 `step`，不调用 CAD、导出或模型。

### 命令、证据与验证边界

新增 6 项核心复现用例在修改前全部失败；扩展边界后新增 **17 项**全部通过，原 48 项加新回归共 **65 passed**，没有跳过测试。仅保留原 Starlette `httpx` 弃用提示。

以下命令使用仓库根目录，测试设置 `$env:M12_PDF_PATH='D:/desktop/测试文件.pdf'`，网页使用 `$env:WEB_PORT='8086'`：

| 实际命令 | 退出码 | 结果 |
|---|---:|---|
| `git status --short --branch`、`git log -3 --oneline` | 0 | 保留 9fdc167 之后全部 M3 工作区改动 |
| `docker compose -p drawing-step6-qa -f compose.yaml -f compose.m3-qa.yaml -f compose.test.yaml run --rm -T -e M3_SIM_URL=http://simulator:8011 tests python -m pytest -q -p no:cacheprovider tests/test_m3_review.py` | 修复前 1，修复后 0 | 核心 6 failed；最终新增 17 passed，13.21 秒 |
| `docker compose -p drawing-step6-qa -f compose.yaml -f compose.m3-qa.yaml --progress plain build api worker` | 0 | 仅重建独立 QA 后端 |
| `docker compose -p drawing-step6-qa -f compose.yaml -f compose.m3-qa.yaml up -d --wait --no-deps api worker simulator-backup`，随后同项目 `restart simulator` | 均 0 | 更新 QA 后端及两个隔离模拟器 |
| `docker compose -p drawing-step6-qa exec -T api python /qa/qa_discovery.py` | 0 | 独立服务主 503／空路由接手、调用账本及检查点复用 |
| `python m3/qa_receipt_review.py` | 0 | #14 逆序提交、新进程恢复、新旧目录兼容、数据库零差值；两个恢复子进程均退出 0 |
| `docker compose -p drawing-step6-qa -f compose.yaml -f compose.m3-qa.yaml -f compose.test.yaml run --rm -T -e M3_SIM_URL=http://simulator:8011 tests` | 0 | **65 passed**，47.35 秒 |
| `python m3/qa_browser.py published --versions 4 6 7 8 --output output/m3-review-fix/downloads` | 0 | Edge 12 件交付重下载、ZIP 逐件字节和 SHA-256 校验通过 |
| `python m2/qa_browser.py legacy --url http://127.0.0.1:8086 --output output/m3-review-fix/legacy` | 0 | 首件三项历史下载与原始哈希一致 |
| `python -m compileall -q backend/app backend/tests m2 m3`、`git diff --check`、同项目 Compose `config --quiet` | 均 0 | 语法、空白和配置通过 |

将这次 12 个交付成员与首轮 `output/m3-step6/final-downloads/manifest.json` 再次比较，全部字节数、文件哈希及四个几何哈希一致。本次 ZIP 1,636,166 字节，SHA-256 `85dae0f4a333cdf108989b3d7809b83cc9ab838c8b6d9cbe09996809db297e7d`；ZIP 容器时间变化不影响成员原字节。

证据目录 `output/m3-review-fix/`：`tests-before.log`／`tests-targeted-final.log`／`tests-full.log` 记录先失败后通过；`discovery.json` 保留两个独立 HTTP 服务与数据库调用证据；`receipts/audit.json`、`receipts/isolated/restart.json`、`receipts/legacy/restart.json` 保留当前及旧格式回执恢复；`downloads/` 与 `legacy/` 保存 Edge 截图、下载和清单。审核原目录未改写。

本轮两个阻塞专项复验通过。真实备用供应商联调仍未配置，未重新运行真实 CAD 负例或付费模型；此前两个 G2 及首件的下载字节已重新核对。未执行 Git 提交、推送、生产部署、正式发布或文件删除。
