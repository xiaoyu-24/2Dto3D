# M4 部署与恢复

范围为离线单文件 3D HTML、STEP、验收报告。FCStd 不属于交付。下面的应用部署与数据库恢复只在隔离 QA 实例执行；正式部署仍需单独授权。

## 部署包及信任边界

`python m4/manage.py build --output output/m4-step7/release` 从 Git 已跟踪和未忽略文件封存 `source.tar`、逐文件 SHA-256 和工作区补丁，再只从这个快照构建。`images.json` 记录实际源码归档指纹、镜像 ID、仓库 digest、架构和 OCI revision 标签。`python m4/manage.py pack --output output/m4-step7/release` 保存 `images.tar` 及 SHA-256。部署相同归档得到相同镜像，不依赖部署时远端标签漂移。源码重建锁定基础镜像 digest、Debian 快照、Python/npm 版本及官方 FreeCAD/Edge 下载校验；本轮不声明两次联网重建会得到相同的构建时间和镜像 digest。

本次可直接部署的包为 `output/m4-step7/m4-linux-amd64.tar`，解包得到 `bundle/`。先在该目录运行 `sha256sum -c SHA256SUMS`，再将 `source.tar` 解到 `/opt/drawing-platform`。本次 `images.tar` 与补充 `mysql-tools.tar` 均需装载；今后由最终脚本完整重建时，工具镜像会与其他镜像一起进入 `images.tar`。`build-binding.json` 分别记录实际镜像构建源码和最终运维脚本源码。

组件分别是 `drawing-m4-api`（API、Worker、迁移使用相同镜像）、`drawing-m4-web`（React 静态资源和 HTTPS 网关）、`drawing-m4-harness`、`drawing-m4-cad`、`drawing-m4-step-check`、`drawing-m4-html-check`、`drawing-m4-mysql-tools`，另附固定内容指纹的 MySQL 8.4.10。迁移仍为 `001` 至 `007`，无 M4 业务表变更。

CAD 和独立 STEP/HTML 验收容器 UID/GID 为 65534，`network_mode: none`，根文件系统只读，禁用全部 capability 和权限提升，并限制 CPU、内存、进程数及时间。宿主调度器是受信任运维进程；Docker socket、数据库和模型凭据均不挂进 CAD 或验收容器。任务输入只读，单独输出目录可写。Harness 仅通过专用出口网络访问现有批准模型；数据库和模型服务不映射宿主端口。只有 HTTPS 网关发布 443。仅供运维备份的 `mysql-tools` 以 root 和 CHOWN/DAC_OVERRIDE/FOWNER 访问停机数据卷，保持无网络、只读根和禁止权限提升；它不执行建模代码。

宿主调度器需要 Linux、Python 3.11+、Docker Engine 和 Compose v2+。`m4/install-docker.sh` 提供 Debian 的安装入口；实际版本须保存在验收记录。生产容量规划仍按实施方案的 8 vCPU／32 GiB／200 GiB；本轮功能演练资源另行如实记录。

## 空库启动

在新 Linux x86_64 主机将代码放到 `/opt/drawing-platform`，部署包放到 `/opt/m4-bundle`。TLS 证书和私钥置于 `/etc/drawing-platform/tls/cert.pem`、`key.pem`。从 `m4/env.example` 准备 `/etc/drawing-platform/deploy.env`，设为 0600，填入本包 release、独立 MySQL server-id、数据库及初始化账号凭据、CAD_STATE 和已获准模型凭据。仓库与包内不包含实际凭据。生产 TLS 使用受信任证书，QA 使用有明确记录的短期自签证书。

```sh
cd /opt/m4-bundle
sha256sum -c SHA256SUMS
sudo docker load -i images.tar
if test -f mysql-tools.tar; then sudo docker load -i mysql-tools.tar; fi
cd /opt/drawing-platform
sudo python3 m4/manage.py verify --output /opt/m4-bundle
sudo python3 m4/manage.py init --output /opt/m4-bundle --env /etc/drawing-platform/deploy.env
sudo python3 m4/manage.py up --output /opt/m4-bundle --env /etc/drawing-platform/deploy.env
sudo install -m 644 m4/drawing-cad.service /etc/systemd/system/drawing-cad.service
sudo systemctl daemon-reload
sudo systemctl enable --now drawing-cad.service
```

`init` 拒绝非空业务库。先等 MySQL 健康，再迁移、初始化管理员与项目。`up` 按迁移完成、Harness 健康、API 健康的依赖顺序启动 Worker 和网关。Worker 健康检查在闲时和阶段续租时更新心跳。CAD 每次启动先找回原发布回执，再领取任务；`CAD_STATE/pending` 为必须保留的持久暂存。一次诊断执行：

```sh
sudo python3 m4/executor.py --env /etc/drawing-platform/deploy.env --once
sudo python3 m4/executor.py --env /etc/drawing-platform/deploy.env --recover
sudo python3 m4/manage.py status --output /opt/m4-bundle --env /etc/drawing-platform/deploy.env
```

## 一致性备份与恢复

本轮采用有维护窗口的协调冷备份。停止写入入口、Worker 和 CAD 调度；等待已运行 CAD 调度正常退出。备份工具持有与调度器相同的文件锁，拒绝应用仍运行的实例。`snapshot` 先保留 InnoDB `--single-transaction --source-data=2 --hex-blob` 逻辑副本和 binlog 位置，再正常关闭 MySQL，将数据卷保存为 `mysql-data.tar`，成功或失败均重新启动数据库。物理副本不包含 `auto.cnf`，目标实例生成新的 server UUID。实测发现逻辑 SQL 导入会使一个验收 JSON double 末位舍入，因此严格恢复使用物理副本；`backup_json.py export` 仅保留为只读重现诊断，不用于恢复。

`seal` 保存截止位置、轮转并复制对应的完整 binlog、持久 `pending`；记录每项字节数及 SHA-256。封口包必须复制到另一实例和独立存储，尚未封口的包不可恢复。本实现不等同于持续异地 binlog 归档服务；两次备份之间的 RPO 由运维备份频率决定。

```sh
sudo systemctl stop drawing-cad.service
sudo docker compose --env-file /etc/drawing-platform/deploy.env -f m4/compose.yaml stop web api worker
sudo python3 m4/recovery.py snapshot --env /etc/drawing-platform/deploy.env --backup /backup/m4-001
sudo python3 m4/recovery.py seal --env /etc/drawing-platform/deploy.env --backup /backup/m4-001
```

在另一个全新实例加载并验证相同镜像，注入备份时的数据库凭据、目标唯一 MySQL server-id 和 TLS。物理备份包含数据库用户的认证状态，因此注入的数据库密码须与备份时一致。不要先启动 MySQL、初始化或迁移。恢复先校验完整包，创建尚未启动的 MySQL 容器和空卷，拒绝卷中已有任何文件，解包物理快照后启动数据库。随后使用锁定的 MySQL 8.4.10 `mysqlbinlog --start-position/--stop-position` 回放到封口位置，恢复持久暂存，最后运行迁移。已有 pending 目录同样会被拒绝。恢复不带回 CAD 任务临时目录，也不重置已发布版本或调用账本。物理恢复必须使用本包完全相同的 MySQL 镜像；跨大版本升级不属于该恢复入口。

```sh
sudo python3 m4/recovery.py restore --env /etc/drawing-platform/deploy.env --backup /backup/m4-001
sudo python3 m4/manage.py up --output /opt/m4-bundle --env /etc/drawing-platform/deploy.env
sudo systemctl start drawing-cad.service
```

默认项目名为 `drawing-m4`；自定义项目须对所有入口一致传 `--project`，Compose 直接调用则传 `-p`。停止、备份后需恢复原实例服务时使用相同 `up` 和 systemd 启动命令。备份失败保留现场，不自动删除文件或覆盖目标。

MySQL 服务端、PyMySQL 和 dump/import 客户端均配置 256 MiB packet。M1/M2 单件下载先鉴权，再按 1 MiB SQL 分段读取，响应带字节数和 SHA-256；ZIP 按成员分段读写并使用临时磁盘缓冲，下载原字节不改变。需要为并发 ZIP 配足 `/tmp` 和磁盘容量。

## 验收状态

第 7 步通过。两台独立 Linux 虚拟机分别完成空库部署和恢复，19 张业务表原值一致，恢复 24.036 秒，待入库 STEP 只发布一次且零重算；新 Edge 验证历史交付、离线操作、权限和 67,104,768 字节 STEP 往返。完整证据和边界见 [验收记录](ACCEPTANCE.md)。
