# 第 7 步 M4 部署与恢复验收

日期：2026-11-21，北京时间。**结论：通过。** 干净 Linux 部署和另一独立 Linux 虚拟机恢复均已实际执行；最终判定以物理一致性快照恢复的结果为准，前两轮未满足逐值恢复的结果没有作为通过证据。

用户本轮授权实施、独立 QA、免提案；未执行 Git 提交、推送、生产部署、正式发布或文件删除。全部 `published` 为隔离 QA 数据库的测试发布。交付只有离线单文件 3D HTML、STEP、验收报告。

## 基线与实际构建快照

分支保持 `codex/m0-linux-probes`，HEAD 为 `9fdc1677e0684040840bc69ec67cc6b83d89882d`。保留开工时已验收的第 6 步工作区改动。开工清单和补丁位于 `output/m4-step7/baseline/`；该清单还包含刚创建的 M4 计划与快照脚本。计划为 `docs/superpowers/plans/2026-11-21-m4-deployment-recovery.md`。已阅读根 AGENTS、实施方案第 2、6、8 节和 M3 记录；AGENTS 引用的 `CONTEXT.md`、工程细则文件不存在，没有发现现行业务契约冲突。

实际应用镜像从封存并解包的 `release/source.tar` 构建，不从持续变化的工作区构建：

| 项目 | SHA-256 / 位置 |
|---|---|
| 六类应用镜像实际源码快照 | `8d513680bf86b647f28f4bf7a440def82ceb3d7396f17f34575fb09234aa3bb0` |
| 补充 MySQL 工具镜像构建快照 | `98b4b0dd160d6d557555b6883e2036345acba481843164ace49fd947f4c6c7f7` |
| 应用及 MySQL 镜像归档，2,104,321,024 字节 | `0d5f3439c243bd1547f2166044b5d1f4cd87499c4f79cbabb641a6b3cb50c9c4` |
| MySQL 工具镜像归档，241,331,200 字节 | `482ca8c8c3395d63a3d9dd4027d0e787e12dbc321562f6fc219f98dba59f5470` |
| 最终部署脚本/说明快照及逐文件指纹 | `output/m4-step7/bundle/source.json`、`source.tar` |
| 构建快照与最终脚本的差异清单 | `output/m4-step7/bundle/build-binding.json` |

镜像构建后修改仅涉及部署 Compose、运维/QA 脚本和文档，未再改写镜像内的业务代码或 CAD 脚本。MySQL 工具为新增镜像，单独记录其源码指纹。最终包中的两份构建快照、镜像标签、digest 和 SHA 清单可关联实际构建输入及部署输入；不把 HEAD 单独当作代码快照，也不宣称联网重建的构建时间/镜像 digest 必然相同。

## 实测环境与镜像

两台 QEMU 11.1.0/WHPX 虚拟机各从校验过的 Debian 12 官方 genericcloud x86_64 基础盘创建独立空 QCOW2 写层。各 2 vCPU、3 GiB RAM、40 GiB 虚拟磁盘，内核 `6.1.0-56-cloud-amd64`，glibc 2.36，Docker Engine 29.10.0，Compose 5.8.0。它们拥有不同 machine-id、boot-id、Docker daemon、系统盘、MySQL 卷和 server UUID；不把 Docker Desktop 上换 Compose 名称算作独立 Linux 主机。

| 环境 | machine-id | Docker daemon ID | 用途 |
|---|---|---|---|
| `m4-deploy` | `504eac0e8884458d872d249cb532b099` | `0db90ebf-975d-4057-8ecb-209046b1aa1e` | 空库部署 `drawing-m4`；另建独立数据源 `drawing-m4-history` |
| `m4-restore` | `a317bf7b82d046e5bde02c7bd1c31cc3` | `96b20582-c959-436f-9d61-ea2201093e1f` | 最终恢复 `drawing-m4-exact`，server-id 75 |

恢复后的 MySQL UUID 为 `ec0415e9-e5ad-11f1-af99-7250aae79301`，不同于源实例。MySQL 8.4.10、FreeCAD 1.1.4、OCCT 7.8.1、Harness SDK/Runtime 0.2.2rc2、Nginx 1.28.3。Windows 网页与离线验收及 Linux HTML 验收均明确启动 Microsoft Edge 157.0.4341.60，`channel="msedge"`。

以下均为 Linux amd64 内容指纹，完整标签及 RepoDigests 在 `bundle/images.json`；六个应用镜像标签为 `8d513680bf86b647`。

| 镜像 | SHA-256 |
|---|---|
| API / Worker / migration | `d3bf60e4ac291c82c65488d4314e49276ae19f2e70b55f4ccff648d3cb445c53` |
| Web / HTTPS | `fa427e1c9c440f6e3126f5950db8f9c89d7621bbc132388acd12a05ae4a6543f` |
| Harness | `7be161e196a6fe85d01a634b3a59fd6274acb7af367ac3cad14685a0f5b03da1` |
| CAD | `8309c7fda94f608ed613760f24e0fb3e72521134da2a1c87027b31feead627c4` |
| 独立 STEP 验收 | `1920878387794e045744ae22e1bf9799fcc931c60c18d04ab17f0b6ab626d654` |
| 独立离线 Edge 验收 | `b300e738f13e621de907463020f7966c5fd9d03b524df27516f34724e4282d50` |
| MySQL | `d36d39a64cd12a5c1cc9e6aa2bfb5f8d4c81a2f6586e0a04a9ae13939db02209` |
| MySQL 8.4.10 binlog 工具 | `7f7dda19947c6d2522f258e97464e16c5ae2210cb8b518e27906fae5ade0e7bb` |

`clean-inventory.json` 和 `restored-inventory.json` 记录实际容器创建时间、进程退出码、健康检查、用户、挂载、网络和安全限制。应用只发布 HTTPS 443；Windows 回环转发分别为 8447/8448。SSH 2227/2228 仅为 QA 管理通道。数据库、API、Harness、Worker 未映射应用端口。CAD/STEP/HTML 容器均为 UID 65534、无网络、只读根，只有对应任务输入与输出；无数据库或模型密钥、无 Docker socket。

## 干净 Linux 完整链路

部署前 `docker ps -a` 和 `docker volume ls` 均为空。2026-11-21 20:36:44 等到 MySQL 健康后，从空库按顺序运行 001→007 迁移，初始化管理员，API、Harness 健康后启动 Worker 和 HTTPS。实际凭据由受保护的部署环境文件注入，没有打入源码和镜像。

20:38:23 在全新 Edge 会话登录、上传真实 M12 PDF，源文件 SHA-256 `be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c`，重复上传复用原任务。关闭该浏览器后，Worker 20:38:24 实际领取 PDF 阶段，随后后台调用真实 Harness 读图，20:38:43 完成。真实模型对四针依据提出疑问，网页按原已验收图纸依据确认「4 针 / 24.70 mm」，一次续办。

systemd 启动 Linux CAD 调度器，实际运行建模、HTML 导出、断网 Edge、STEP 导出和独立回读，20:42:03 在干净库生成版本 #1。网页下载三项交付与 ZIP 成员逐件通过。作业选择真实供应商 request ID 为 `013e5c5a753d623223f3e4fdcffc2ed6`，模型 `deepseek-flash`，312 输入 / 8 输出 token；完整审计在冻结 selection 和数据库。

| 干净库版本 #1 | 字节 | SHA-256 |
|---|---:|---|
| HTML | 1,329,314 | `a46ced60421dd03e9254df47343755d90b8a5f497c90b1fdc8cfa0072b5ff811` |
| STEP | 1,013,765 | `f7c55d430f4b56f728b5e3b5b2f0b714d359c342991b946a29f81b99239b80c3` |
| 验收报告 | 3,859 | `def2d4d9135dabff8500b98f12b76223a8a924e5cf990db81c578342ed3419dd` |

## 一致性备份和独立恢复

M3 原实例只读导出到 Linux 源机另一个独立空实例，保留首件及 M3 的原交付字节。新增独立 QA 项目 20/21/22，保留 admin/operator/reader 角色、1 条未回答确认、2 条 ready 阶段、12 条 waiting 阶段、原已发布版本。近 64 MiB 文件及待发布 STEP 均真实独立回读通过后，分别入库和持久暂存；未改写原版本。

严格恢复采用正常关闭 MySQL 后的物理 InnoDB 一致性快照，同时保留 `--single-transaction` SQL 副本。最终备份目录为源虚拟机 `/opt/m4-backup-verified`，独立转移归档为 `output/m4-step7/verified-backup.tar`，770,099,200 字节。备份不包含 CAD 任务临时工作目录，只保留 `pending`；恢复实例使用新的 CAD_STATE。

| 备份成员 | 字节 | SHA-256 |
|---|---:|---|
| `database.sql`，辅助逻辑副本 | 198,564,021 | `9d6ebc3db97e2bfbd9bf512a3a7a751aa6bf7b77562717dbab629ae18f8ad522` |
| `mysql-data.tar`，权威物理快照 | 412,364,800 | `29e2360e6eec2448b5cca7489c10f89c2975a4f6fcde9c720416e57dc8dd0da8` |
| `cad-pending.tar` | 90,859,520 | `abee3f2f0b157ef7f12c727d7524365263a595401eb36fbea9e29cb5a9067b59` |
| `binlog.000007` | 181 | `3b611d917fd38dfa8103fd19161edd805ff93871cd1c66f95b980f1a84680ed4` |
| `binlog.000008` | 546 | `187a0962a2f3d1482cf60b87233d314f41ff7c607adfb8224ec630a2ac47fc82` |

快照位置 `binlog.000007:158`，恢复目标 `binlog.000008:502`。快照后提交一条 `M4 binlog replay proof` 项目记录，然后封口；该记录通过 binlog 恢复，未把「导入快照即可」当作回放通过。

2026-11-21 **21:17:48** 恢复完成，恢复工具耗时 **24.036 秒**，计入全部备份哈希校验、空卷装载、数据库启动、两段 binlog 回放、CAD 暂存解包和迁移，未计首次安装 Docker、传输镜像和装载镜像。随后才启动应用和 Worker。

`sealed-final.json` 与 `restored-exact-before-start.json` **完全相等**：19 张业务表、6 个用户、24 条成员角色、10 条版本记录、69 条原模型调用、162 个文件正文的大小与数据库重算 SHA-256，以及每个 JSON 原值。会话表未作为业务一致性断言；恢复验证使用全新浏览器登录。

### 未入库 STEP 续办只发布一次

独立项目 20，任务 #36、阶段 #46、版本 #9。在原 M3 #4 的已验收 STEP 上增加可追踪注释，不改变几何，重新独立回读，形成此前 `file_bytes` 不存在的新哈希。1,013,820 字节，`05390e126d69f931df26a242ccc935696210ff6ef00ea28d6d0f866b87ed87fb`。备份时数据库无该 STEP 槽位、无报告、无发布回执，实际字节与验收凭证保存在 fence=1 的 pending。

恢复后新执行器领取 fence=2，仅执行 `inputs` 和暂存 STEP 重送，21:20:14 输出 `resumed persisted STEP: published`。唯一发布回执 1 条，published 事件 1 次；原模型调用 **69→69**。新增模型调用、建模、HTML 导出、STEP 导出和验收进程次数均 **0**。另起进程再次运行后 19 张业务表保持完全一致，只恢复既有回执，没有重复发布。恢复执行器的 9 条命令全部为数据库 CLI `exec`，没有 CAD/验收容器启动。

另一 ready PDF 阶段 #48 由真实 Worker 21:19:46 领取并完成，证明恢复的待领取队列可继续工作；未回答确认仍保持未回答，没有替用户自动确认旧待办。

## 新 Edge 会话：下载、离线和权限

每次验收启动新的 Edge 进程，不复用前会话登录状态。HTTPS 测试使用短期自签证书并显式记录 QA 证书例外；cookie 均为 Secure。恢复后首件和 M3 文件及 ZIP 全部成员与备份前逐字节一致：

| 版本 | 文件 | 字节 | SHA-256 |
|---|---|---:|---|
| 首件 #1 `A0-M1.3` | HTML | 1,329,332 | `69440e5f1d28958c37938958de8bc78a24e140dee96ae68fc368ab04490b2740` |
| 首件 #1 | STEP | 1,013,783 | `5ade87ca1537c1eea21eda2c89c48710a0ef57d63886b0bb1ff6d04bec696403` |
| 首件 #1 | 报告 | 7,236 | `ed07e47bc0cc2111ea2588bfc951605f11c1fa0f1e9afb36c92a029ffc1861c3` |
| M3 #4 `A0-M3-partial` | HTML | 1,329,320 | `3f83900471fc0116df489ea8532a1fb55fab6f3ad11e3f05c4b8642db2a32263` |
| M3 #4 | STEP | 1,013,771 | `c153fed85e9605c594457f637f159703d819bbf8c8e259f326ab6fdb537a6a0c` |
| M3 #4 | 报告 | 3,865 | `2f9515bf847ca1aee88220552ec27fa0bebe6d52fa6dc639c6500c706b18e784` |
| 大文件 #10 | STEP | **67,104,768** | `58d74e60cf720bbf156ccff060db69e4126a268db052bbf23a4b1830f2a9a81a` |
| 大文件 #10 | 报告 | 3,865 | `ef6eaba1b8170b1dc1a59af1606aa1cb375bfcc943c93411037a02e07fea15f8` |
| 续办 #9 | 报告 | 3,865 | `cf44b20c5fd05c44919822be870a54bd821e398e8f54c41b8776f1247214afa8` |

近 64 MiB 测试 STEP 距 64 MiB 上限 4,096 字节，用合法 STEP 空白和注释扩充已验收几何，真实重新导入并测量通过。覆盖原始文件入库、SQL/物理备份、独立恢复、API 流式下载和 ZIP 成员下载。它验证容量和字节往返，不代表同等大小复杂几何的建模性能。大文件 HTML 仍为上述 M3 HTML；对应报告引用新 STEP 哈希。

首件/M3 ZIP 恢复后 1,068,019 字节，SHA-256 `50d9628512618c33576ca604751fe21f9ed4ba78a691465e63a2eba133144e09`。其 `manifest.json` 为 2,194 字节、`3ff0b010f25ff5a53d15155f9304e93add788de317fc0c76f455b5ef620478ff`，六个交付成员均对应表中原字节。大文件 ZIP 598,345 字节、`c33bd405d5882680e604780c21431ce6d914a5eaad89d3dcfb6787af20d754b4`，成员 manifest 1,157 字节、`404123f4b89dcfa2477790cc62f2092ed421e2691f704ac1039891156b24c375`。ZIP 容器时间戳使整体哈希不同，所有成员（含 manifest）恢复前后相同。全部 ZIP、成员及其他下载的大小/哈希记录在 `final-verification.json`。

两个 HTML 从下载目录以 `file://`、断网模式重新打开，实际像素非背景，旋转、缩放、两端视角、部件开关、恢复显示均通过，远端请求与页面错误均为 0。跨项目账号仅属于项目 22：项目 1 列表、版本、HTML 下载、旧首件 STEP、证据页，以及直接/混入外项目版本的 ZIP 均 **404**。

## 实际命令、退出码与证据

Windows 工作目录 `D:/projects/2Dto3D`。Linux 工作目录 `/opt/drawing-platform`；以下 `$E` 为相应 `/etc/drawing-platform/*.env`，`$P` 为实际项目名。展开后的命令、UTC 时间、秒数和退出码见 `release/commands.jsonl`、`clean-runtime-commands.jsonl`、`final-evidence/recovery-commands.jsonl`、`final-evidence/exact-cad/commands.jsonl`、`vm/*/commands.jsonl`。浏览器记录和截图分别放在对应目录。未将凭据写入这些命令记录。

| 实际命令 / 入口 | 退出码 | 结果 |
|---|---:|---|
| `git status --short --branch`、`git rev-parse HEAD` | 0 | 核对并保留原工作区 |
| `python m4/manage.py build --output output/m4-step7/release` | 0 | 从封存快照构建六类应用；各 Docker build 均 0 |
| `docker build --platform linux/amd64 -t drawing-m4-mysql-tools:8d513680bf86b647 -f m4/Dockerfile.mysql-tools --build-arg SOURCE_SHA=98b4b0dd160d6d557555b6883e2036345acba481843164ace49fd947f4c6c7f7 .` | 0 | 补齐官方同版 binlog 工具；RPM SHA 锁定 |
| `python m4/manage.py pack --output output/m4-step7/release`、工具镜像 `docker save` | 0 | 实际离线镜像包 |
| 两台 VM `sudo sh install-docker.sh`、`sha256sum -c`、`docker load` | 0 | 独立引擎，归档校验与装载 |
| `sudo python3 m4/manage.py init --output /opt/m4-bundle --env $E` | 0 | 空库 001→007、管理员初始化 |
| `sudo python3 m4/manage.py up --output /opt/m4-bundle --env $E --project $P` | 0 | 依赖顺序及健康检查 |
| `systemctl enable --now drawing-cad.service` | 0 | 实际 Linux 宿主调度、隔离 CAD 与独立验收 |
| `python m4/qa_browser.py upload/derive/confirm/downloads --url https://127.0.0.1:8447 ...` | 最终均 0 | 登录、上传、后台读图、确认、建模和下载 |
| `python m4/qa_recovery.py history/fixtures --env .../history.env --folder /opt/m4-qa ...` | 最终均 0 | 原 M3 只读导入，新增两份真实验收 STEP |
| `python3 m4/qa_recovery.py snapshot/seal --env .../history.env --folder /opt/m4-qa --backup /opt/m4-backup-verified` | 0 | 冷一致性快照、两段 binlog、pending |
| `python3 m4/recovery.py restore --env .../exact.env --project drawing-m4-exact --backup /opt/m4-backup-verified` | **0** | **24.036 秒**，19 表完全一致 |
| `python3 m4/executor.py --env .../exact.env --project drawing-m4-exact --once`，两次新进程 | 0 | 一次发布，重复无副作用，零重算 |
| `python m4/qa_browser.py downloads --url https://127.0.0.1:8448 --env output/m4-step7/exact.env --output output/m4-step7/after-history --versions 1 4` | 0 | 六项交付及 ZIP |
| 同入口 `--output .../after-large --project 21 --versions 10`、`.../after-resumed --project 20 --versions 9` | 0 | 大文件和续办交付 |
| `python m4/qa_browser.py permissions --url https://127.0.0.1:8448 --env .../outsider.env --output .../after-permissions --project 22` | 0 | 跨项目拒绝 |
| `python m1/qa_browser.py offline <首件/M3 HTML> output/m4-step7/offline-first/offline-m3` | 均 0 | 新 Edge 断网交互 |
| `python m4/qa_verify.py` | **0** | 汇总 19 表、零重算、唯一发布、权限、所有文件/ZIP 哈希 |
| M3 隔离测试入口 `pytest ... tests/test_m4.py tests/test_m2.py tests/test_m3_review.py` | 0 | **35 passed**，25.03 秒 |
| `docker compose -p drawing-step6-qa -f compose.yaml -f compose.m3-qa.yaml -f compose.test.yaml run --no-deps -T -e M3_SIM_URL=http://simulator:8011 tests` | 0 | **67 passed**，48.08 秒；原 65 项加 2 项流式下载回归 |
| 活跃实例重复 `restore`、非空库 `init` | 预期 1，外层断言 0 | 拒绝覆盖，随后 19 表未变 |

完整回归只保留原 Starlette httpx 弃用提示。测试使用随机隔离数据库；未改写 Step 4/5/6 业务数据。

## 已修复问题与未验证项

实际失败保留在日志，没有隐藏为通过：

- QEMU 首次 cloud-init 介质接口及 `operator` 用户创建失败：改为 virtio seed 和新 `m4qa` 用户，在新系统盘重建；失败盘保留。
- 离线 `docker load` 不保留 MySQL 的原 tag@digest 引用：Compose 使用已校验内容 ID、禁止自动拉取。
- 纯 internal 网关网络没有发布端口：只给网关增加 ingress，内部服务仍保持不映射端口。
- 现成 MySQL 精简镜像无 `mysqlbinlog`，初轮回放退出 127：增加同版官方工具镜像；原不完整实例保留，新实例重跑。
- 原逻辑恢复把一个验收 double 从 `-1.2363270502050926e-7` 变为 `-1.2363270502050928e-7`：不放宽比较；改用冷物理快照，最终 19 表原值完全相等。
- 物理备份首次使用 Compose 不支持的 `--volumes-from`、缺少读取数据卷 capability，退出 1/2：改为检查卷名后显式挂载和仅运维工具具备必需权限。每次失败均重新启动源 MySQL，原文件不删除。
- STEP 夹具首次缺少独立验收所需 `panel.brep`，退出 1：补齐只读几何输入后两个 STEP 实测退出 0。
- QA 浏览器项目选择器两次等待超时：使用页面实际项目选择控件定位后复验通过。曾以普通用户读取 root 0600 TLS 目录使 curl 退出 77，改用 sudo 校验后成功。

本轮没有未完成的 M4 必需门槛。未验证：生产部署、公共 CA/DNS 接入、跨物理机故障域、零停机热备、持续异地 binlog 自动归档、跨 MySQL 大版本恢复、M5 50 文件负载与盲测。两台 VM 位于同一 Windows 宿主，但有独立系统与引擎；资源低于方案生产容量。本轮备份是维护窗口内的实际一致性备份，必须由运维另行设定备份频率和异地保存周期。
