# M0 Linux 环境验收

在仓库根目录运行以下命令。首次构建需要联网下载官方 wheel、FreeCAD AppImage 和 Debian 软件包。两个探针运行时只使用容器回环网络，无需图纸或供应商密钥。

```sh
docker compose -f compose.m0.yaml config --quiet
docker compose -f compose.m0.yaml build
docker compose -f compose.m0.yaml run --rm harness
docker compose -f compose.m0.yaml run --rm cad
docker image inspect drawing-m0-harness drawing-m0-cad --format '{{json .RepoDigests}}'
```

## 验证记录

2026-11-20 在本机 Docker Desktop Linux Engine 29.5.3、Compose 5.1.4、`linux/amd64` 上验证。

| 检查 | 退出码 | 实测结果 |
|---|---|---|
| Compose 配置与镜像构建 | 0 | 构建两个探针镜像，wheel 和 AppImage 通过 SHA-256 校验 |
| Harness | 0 | 官方 Python SDK 启动配套 `dsh`。回环模拟端点收到内嵌 PNG 和工具清单 `read_image`、`skill`、`working_directory`。通过真实 `skill` 调用取得只读技能 `m0-check` 的正文标记 |
| CAD | 0 | 无桌面导出带孔板，`MeshPart` 生成非空网格。独立 Python 进程从 STEP 回读 1 个有效实体，外形 32 × 20 × 5 mm，通孔中心 (7, 9) mm，半径 3 mm，体积 3058.628331 mm³ |
| 镜像孔负例 | 1（预期） | 只把孔中心的 X 从 7 改为 25 mm。外形、实体数、体积仍正确，回读进程报 `STEP measurement mismatch` |

复现镜像孔负例只变更送入容器标准输入的脚本。

```sh
docker compose -f compose.m0.yaml run --rm cad sh -ec "sed 's/Vector(7, 9, 0)/Vector(25, 9, 0)/' export_step.py | /opt/freecad/usr/bin/python - /tmp/mirror.step && /opt/freecad/usr/bin/python verify_step.py /tmp/mirror.step"
```

本次联调解决了两个会影响正常运行的问题。Docker 的 tmpfs 默认 `noexec`，`dsh` 原生模块和图像解码库需要可执行缓存，因此给 Harness 单设进程私有的 `/run/dsh-native`，作业目录仍使用 `/tmp`。模拟端点对 `/v1/files` 返回 404，使 SDK 实际走内嵌 PNG 路径，随后两次 `/v1/messages` 分别完成技能调用和结果回传。

真实模型识图能力待使用项目获准的端点、凭据和模型 ID 实测。读图路由启用前需通过随机图形、数字和方向题。当前记录覆盖环境与协议，M12 尺寸和针数的实测归入 M1。

## 版本与许可

| 组件 | 已验证版本与锁定方式 | 许可证位置 |
|---|---|---|
| Harness SDK / Runtime | 均为 `0.2.2rc2`，Python 3.11.18。七个 Linux x86-64 / Python 3.11 适用的 wheel 及哈希见 [requirements.lock](harness/requirements.lock) | 两个 wheel 的 `dist-info/licenses/LICENSE` 均为 MIT。Runtime 的同目录保留 `THIRD_PARTY_NOTICES.md` |
| FreeCAD / OpenCascade | 官方 `1.1.4` x86-64 AppImage，内含 OCCT 7.8.1 和 Python 3.11.14。AppImage SHA-256 为 `f6dc6ba676e5ac96a565ebc8d657232f94c6158e85b4352141bd1a46f6b43434` | 发行包在 `/opt/freecad/usr/share/doc/FreeCAD/` 保存 LGPL2+ 说明和第三方声明，在相邻 `opencascade/` 保存 LGPL 2.1 正文和 OCCT 例外条款 |
| Linux 运行库 | 两个镜像共用 Python Bookworm amd64 基础清单 `sha256:e8c69d86735f6df3efa06b324694fe51f0422785f83a3bd463052636c2421944`。CAD 系统软件包取自 `20261119T000000Z` 的 Debian 签名快照 | Debian 软件包的 `/usr/share/doc/*/copyright` |

本次验收使用的本地镜像摘要和 STEP 样本指纹如下。STEP 文件头含导出时间，复跑时以回读的几何测量值判定通过。

| 对象 | SHA-256 |
|---|---|
| `drawing-m0-harness` 镜像 | `30a8961856bf9de972f8c3db489ef0e4aeb2d5290942210cfaf77939f31708be` |
| `drawing-m0-cad` 镜像 | `3f38a3ed15f289551177a9a5540853932d6cedf631fd71e3ce690ead80429373` |
| CAD 镜像的系统包清单（`dpkg-query -W`） | `068b3776348f2b86368a965dcfcfc7f803c9579f503146d42bb831cefdeb947c` |
| 本次正例的 STEP 文件 | `a9b94c9663068a549d9a3092f417d89141c9f01fe029b728c2ba2f3f64d849c5` |
