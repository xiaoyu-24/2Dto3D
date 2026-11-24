# M4 部署与恢复实施计划

**Goal:** 在两个独立 Linux x86_64 实例实际验证可复现部署、备份恢复、暂存续办和下载指纹。

**Architecture:** 保留 M3 数据和发布契约。增加独立部署 Compose、HTTPS 网关、受信任的宿主 CAD 调度入口，以及隔离的 CAD、STEP 和 Edge 验收镜像。备份记录一致性快照位置、封口位置和持久暂存，恢复只允许空库。

**Tech Stack:** Docker Compose、MySQL 8.4、现有 FastAPI/React/Harness、FreeCAD 1.1.4、Microsoft Edge、Python 3。

授权：用户已明确开工、免提案，覆盖独立 QA；不提交、推送、生产部署、正式发布或删除既有文件。继续使用 `codex/m0-linux-probes`，基线为 `9fdc1677e0684040840bc69ec67cc6b83d89882d` 加开工时已验收的 M3 工作区。`CONTEXT.md`、`docs/engineering-ai-guardrails.md` 缺失，采用实施方案第 2、6、8 节及 `m3/README.md` 的现行契约。

- [x] 固化开工与构建快照：`m4/snapshot.py` 记录实际文件 SHA-256、Git 基线、脏工作区和源码归档；不收集 `.env`、运行数据或凭据。
- [x] 构建部署入口：`m4/compose.yaml`、Dockerfile、TLS 网关配置、环境示例及 `m4/manage.py`；前端/API/Worker/Harness 启动顺序和健康检查，CAD 非 root、无网络、根只读。
- [x] 部署 CAD 调度和独立验收：`m4/executor.py` 复用 M3 fence 暂存与去重；STEP 和离线 Edge 在独立容器验收。只对 `m2/cad_worker.py` 提取 HTML 验收调用点。
- [x] 补齐 256 MiB 驱动/备份配置和分段下载；用接近 64 MiB 的有效交付测试文件验证入库及下载，保留原权限检查。
- [x] 备份和恢复：`m4/recovery.py` 校验快照、binlog、暂存指纹；记录明确回放位置；拒绝向非空实例恢复；重启前检查迁移及镜像指纹。实测 JSON 浮点舍入后改用正常停机的物理一致性快照，未放宽逐值核对。
- [x] 新建独立 Linux 虚拟机：记录 OS、内核、架构、资源、Docker/Compose、空库证明；从部署包启动，通过显式 `msedge` 登录、上传、后台领取及下载。
- [x] 独立恢复演练：保留首件与 M3 原字节，备份用户角色、待确认/待领取阶段、已发布版本和待发布 STEP；第二独立实例恢复后续办恰好一次，模型/建模/导出增量全部为 0。
- [x] 新 Edge 会话重验 HTML/STEP/报告/ZIP 成员字节及 SHA-256，跨项目拒绝，近 64 MiB 往返；67 项回归通过。
- [x] 交付 `m4/README.md`、`m4/ACCEPTANCE.md`、环境和镜像清单、命令退出码、备份位置、耗时、哈希及未验证项。干净 Linux 部署及独立实例恢复实际通过。

验收命令以部署包内 `python3 m4/manage.py`、`python3 m4/recovery.py` 和 `python m4/qa_browser.py` 为入口，具体参数随实际环境写入记录。QA 数据与原 Step 4/5/6 实例隔离；既有实例只读取证。
