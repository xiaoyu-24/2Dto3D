# 第 8 步 M5 负载与盲测验收

实际执行日期：2026-11-21 至 2026-11-22，北京时间。**结论：未通过第 8 步。** 50 份不同字节的 PDF 已通过网页上传并取得处理结果，全部指定故障已有逐例证据，回归与抽样下载通过；负载未完成产品交付，五类合格独立人工标注盲测样本均缺失，不能声明盲测关键错误放行数为 0。

本次用户已授权实施、独立 QA 和验收，免提案审批。分支保持 `codex/m0-linux-probes`，没有 Git 提交、推送、生产部署、正式发布或主动删除文件。`published` 均为独立 QA 数据库的测试发布。交付范围仅为离线单文件 3D HTML、STEP、验收报告。

## 基线、执行源码及镜像

已读取根 AGENTS、实施方案第 8 节、`m3/README.md` 和 `m4/ACCEPTANCE.md`。执行计划为 `docs/superpowers/plans/2026-11-21-m5-load-blind.md`。HEAD 为 `9fdc1677e0684040840bc69ec67cc6b83d89882d`，保留全部已验收 M3/M4 工作区。AGENTS 引用的 CONTEXT 与工程细则仍不存在。

| 快照 | source.tar SHA-256 | 用途 |
|---|---|---|
| `output/m5-step8/baseline/` | `0f95042d784a9a915e2ca9bf2bccbfc08cb14038bcf37a8ca3f86d55c3e604a5` | 开工前代码，与已验收 M4 最终源码相同 |
| `execution-source/` | `5e669fac46937ea0185d8688ab9233fcfa30aaad72c4ec806a3144c2b601400b` | 首轮 Linux QA 工具 |
| `harness-fix-source/` | `94d48ec8d672bc23e13444171f6ca47c5c316046141254f82e5fcb93026a70ac` | 新 Harness 镜像的实际构建输入 |
| `replay-source/` | `461a36f39320b6525383b1fbb84b4fbaadb8494208b62ac684fdc03dc9f102a8` | 重试轮 QA 代码；后续故障脚本修正以运行指纹为准 |
| `actual-execution-source.tar` | `f60222386004a83d3a3991aacec300fe09647722bbbeef2f97c7d2825bd3f6b3` | 演练结束后从 Linux 执行目录原样归档，不含凭据 |
| `delivery-source/` | 见同目录 `source.json` | 最终交付源码、Git status、逐文件指纹和工作区补丁 |

未以 HEAD 代替工作区快照。`linux/runtime-code.json` 从容器内实际计算 API/Worker 源码、Harness 源码和宿主执行脚本指纹。`m5/verify.py` 已核对镜像内全部 backend 文件与开工基线一致。M5 唯一业务运行时修复是 Harness 主 HTTP 服务从 `HTTPServer` 改为 `ThreadingHTTPServer`，避免一个模型请求阻塞另一 Worker 的 `/route` 与健康检查；每次请求的独占 Runtime、工作区和凭据边界沿用原实现。

Linux 从封存的 `harness-fix-source/source.tar` 解包后实际构建补丁镜像，未从变化中的工作区构建。`linux/harness-build.log` 保存构建输出。以下为实际 Linux Engine 镜像内容 ID；manifest list ID 与其内部 config digest 不混用。

| 组件 | 镜像 / 内容 SHA-256 |
|---|---|
| API、Worker、迁移、测试、故障模拟 | `drawing-m4-api:8d513680bf86b647` / `d3bf60e4ac291c82c65488d4314e49276ae19f2e70b55f4ccff648d3cb445c53` |
| HTTPS Web | `drawing-m4-web:8d513680bf86b647` / `fa427e1c9c440f6e3126f5950db8f9c89d7621bbc132388acd12a05ae4a6543f` |
| 首轮 Harness | `drawing-m4-harness:8d513680bf86b647` / `7be161e196a6fe85d01a634b3a59fd6274acb7af367ac3cad14685a0f5b03da1` |
| 修复后 Harness | `drawing-m5-harness:94d48ec8d672bc23` / `d259a028cdf855148a02a9d210c5a2bda4272bbb3e9b9bc1fabb77df7ee132eb` |
| CAD | `drawing-m4-cad:8d513680bf86b647` / `8309c7fda94f608ed613760f24e0fb3e72521134da2a1c87027b31feead627c4` |
| 独立 STEP 验收 | `drawing-m4-step-check:8d513680bf86b647` / `1920878387794e045744ae22e1bf9799fcc931c60c18d04ab17f0b6ab626d654` |
| 独立离线 Edge 验收 | `drawing-m4-html-check:8d513680bf86b647` / `b300e738f13e621de907463020f7966c5fd9d03b524df27516f34724e4282d50` |
| MySQL 8.4.10 | `d36d39a64cd12a5c1cc9e6aa2bfb5f8d4c81a2f6586e0a04a9ae13939db02209` |

M4 应用镜像实际构建源码为 `8d513680bf86b647f28f4bf7a440def82ceb3d7396f17f34575fb09234aa3bb0`。原镜像包 2,104,321,024 字节、SHA-256 `0d5f3439c243bd1547f2166044b5d1f4cd87499c4f79cbabb641a6b3cb50c9c4`。所有运行镜像的架构、RepoDigests、OCI 标签和容器状态见 `linux/images.json`、`linux/containers.json`。

## 独立 Linux QA 环境

实例名、Linux hostname、Compose project 均为 **drawing-step8-qa**。在新的 Debian 12 QEMU/WHPX x86_64 系统盘安装 Docker 并装载镜像，具有独立 machine-id `1742174958b24a90b3bae35c37a159f6`、Engine、系统盘、网络、MySQL 数据卷和 CAD_STATE。内核 `6.1.0-56-cloud-amd64`，glibc 2.36，2 vCPU、3 GiB RAM、40 GiB 虚拟磁盘，Docker 29.10.0、Compose 5.8.0。实际资源小于方案生产容量 8 vCPU/32 GiB，不据本次推断生产容量。

独立 Docker daemon ID 为 `058a089f-ce19-4dab-ae17-3b713632d684`，MySQL server-id 81、UUID `e344e7b7-e5c1-11f1-a136-de18c9a8a980`；最终无 ready/running 阶段，等待/失败保持现场。该汇总含旧副本和故障夹具，负载统计仍按原 50 个任务筛选。

网页 `https://127.0.0.1:8449` 经 QEMU 回环转发到 VM 443；SSH 2229 仅用于 QA 管理。应用只发布 HTTPS，数据库/API/Worker/Harness/模拟服务不映射宿主应用端口。短期自签 TLS 的浏览器证书例外已记录。凭据由受保护环境文件注入，不在源码、镜像或证据报告中打印。

数据库从 M4 保存的 M3 只读副本导入到全新独立卷，旧 ready/running 阶段在本副本暂停，旧任务、旧调用和旧发布不计入负载分母。初始化前实际检查业务库没有表，迁移重复升级至 007。CAD 及两类独立验收容器仍为 UID/GID 65534、无网络、只读根、禁全部 capability、禁止提权，无 Docker socket、数据库或模型密钥。35 个保留容器的实际隔离检查通过；失败容器保留。

## 冻结清单与真实网页负载

原始样本来自既有开发目录的 5 份 PDF 和本机 PDFAI 存储中去重后的 16 份机械 PDF。未使用简历、发票或纯文字测试 PDF。**21 份原图 + 21 份旋转派生 + 8 份双图组合 = 50 份不同 SHA-256 的负载文件**，不是 50 份独立产品图；所有派生明确标注，不计为新增独立盲测样本。

冻结清单 `load-manifest.json` SHA-256 为 `0522fb3b023d5a374a787ac59048ba700b9745b77beb08b5107c5ca35faa5b50`。输入字节保存在 `samples/L01.pdf` 至 `L50.pdf`。目标来自既有样本配置、原文件名或 PDF 文本的型号字段，仅用于负载目标匹配，未经独立人工几何标注。组合输入的目标为两份原图目标并集。O01–O05 是已参与开发的旧图；其他输入的开发历史和独立标注来源均未获证明，因此全部排除盲测。

| 原图编号 | 原文件标识 | 原图 SHA-256 | 冻结目标料号 |
|---|---|---|---|
| O01 | 测试文件.pdf | `be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c` | M12A04-03-301-M16；M12A05-03-301-M16 |
| O02 | B040108M0-2XX2XX01.pdf | `a99425986b8e6e483afb18b3a63f5dde00d78f9d0912f651333732a101050173` | B040108M0-2XX2XX01 |
| O03 | B200528F0-2XX1XXX01.pdf | `da6cb2bb9c77b1b2981a1267a87ef1bc5b582a5c01d53bb8c0166b7886324987` | B200528F0-2XX1XXX01 |
| O04 | WLT2F9-141XX0200.pdf | `15f5aef63a2e3f1b55ad6362e79bc57c73c3587d4be8b9e096f8853028bf4743` | WLT2F9-141XX0200 |
| O05 | WLU3F0-181XXX190.pdf | `4be8db2ac55e9aab912eebe4c6731c5f632f26d9b91846ed1ad1762bb75a8183` | WLU3F0-181XXX190 |
| O06 | AUM12P-DMT04 DMT04-xxxTL.PDF | `c3d57eb69a1388618600cc77acabbfd71c98f0e644bd442b1ebbbf3b8552bc47` | AUM12P-DMT04/DMT04-xxxTL |
| O07 | WL-B21-541-L | `61a452e557cd492fb7812f2391f0d42fc4396a07431b958d548a7ccb0676965c` | AUM12P-DMT04/RJ45P04-xxxTL |
| O08 | WL-B21-547-L | `cebd22267662fdb2cf5fef6b0dbf2eb2714137345e97eb75fc572b0eb2ca4304` | AUM12-AM05/2AF05-xxxTL |
| O09 | AUM12-LFT05-xxxTL.PDF | `9ed845ac48000259231458e72d45129b3a062ec98e59400aea32f195001faeb2` | AUM12-LFT05-xxxTL |
| O10 | AUM12P-DMT04 RJ45P04-xxxTL.PDF | `8c80499e8e54513ca3a24468232a7edbbe0305bfd494fed123aebc3631127cc2` | AUM12P-DMT04/RJ45P04-xxxTL |
| O11 | WL-B21-543-L | `c10484630fd9534aeea49e74e7502ee81e6f83c05cff45023c4c824a3596b639` | AUM12-LFT05-xxxTL |
| O12 | AUM12P-AMT04 AFT04-xxxTL.PDF | `b080950852578c3e76742de38d7ad262f22ffe95d06188ea168682cd0cc8f2a3` | AUM12P-AMT04/AFT04-xxxTL |
| O13 | WL-B21-548-L | `5e784d49f4931a235dfbd004bcfd7913fffac2ec76ccb3dad35fb227c656dbe5` | AUM12-AF05/2AM03-010TL |
| O14 | WL-B21-542-L | `29db8f9ccd28eecf93d22c479a6495f7cd0fda5f968897c5aed84529ee926532` | AUM12P-DMT04/DMT04-xxxTL |
| O15 | WL-B21-544-L | `27fdca8afb3d0b17465426d7b19d25aa1a4ac71ad76ecedb34dd8cc855a06dcd` | AUM12-LMT05/LFT05-xxxTL |
| O16 | AUM12P-AF05 2AM03-010TL.PDF | `7075f706cde51fdba35a8d55f1c720568d10744cd9aa36f8157051ed13ba6341` | AUM12P-AF05/2AM03-010TL |
| O17 | WL-B21-546-L | `1d810e0f876755c2cc98a42d75d2706f597377e93fa3c50803a14d38db971bd9` | AUM12P-AMT04/AFT04-xxxTL |
| O18 | AUM12P-AMT05 AFT05-xxxTL.PDF | `6fd1750af32eb1287e7b286b6d15b802fc0ae9b085501107241e3db20ddfafff` | AUM12P-AMT05/AFT05-xxxTL |
| O19 | AUM12P-AM05 2AF05-NOC-xxxTL.PDF | `a5c8761ba6680c7019cca2245cd640d4236a3814ee03a27108f20243b9eebe9d` | AUM12P-AM05/2AF05-NOC-xxxTL |
| O20 | AUM12-LMT05 LFT05-xxxTL.PDF | `e5bda32480945924f2e3d14a90beb9c4baf1a329cdaed01dd478f96cfa46f638` | AUM12-LMT05/LFT05-xxxTL |
| O21 | WL-B21-545-L | `7c7d01d49f7888ecaffd8ed56f036ee2bb51bd73b66323cab2bec2f4071e8c95` | AUM12P-AMT05/AFT05-xxxTL |

以下每项目标沿用 O 编号表；组合为该 O 编号与下一 O 编号目标之和。页/旋转表示实际上传 PDF 的逐页元数据角度。R = 联合读图结果通过结构校验，可选型号，不代表正确成品；S = 模型结果 schema 拒绝；U = 请求结果未知或渠道熔断后不能取得完整结果。逐件模型原文、错误、候选及目标差异见 `load-results.json`，未用重试或重复上传替换原任务号。

| 文件 | 原图/操作 | 页数/旋转° | 字节 | 上传 SHA-256 | 原任务/结果 |
|---|---|---|---:|---|---|
| L01 | O01/原 | 1/0 | 104,504 | `be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c` | #39/R |
| L02 | O02/原 | 2/0,0 | 160,527 | `a99425986b8e6e483afb18b3a63f5dde00d78f9d0912f651333732a101050173` | #40/R |
| L03 | O03/原 | 1/0 | 243,696 | `da6cb2bb9c77b1b2981a1267a87ef1bc5b582a5c01d53bb8c0166b7886324987` | #41/R |
| L04 | O04/原 | 2/270,270 | 295,100 | `15f5aef63a2e3f1b55ad6362e79bc57c73c3587d4be8b9e096f8853028bf4743` | #42/R |
| L05 | O05/原 | 1/0 | 134,557 | `4be8db2ac55e9aab912eebe4c6731c5f632f26d9b91846ed1ad1762bb75a8183` | #43/R |
| L06 | O06/原 | 1/0 | 2,131,826 | `c3d57eb69a1388618600cc77acabbfd71c98f0e644bd442b1ebbbf3b8552bc47` | #44/S |
| L07 | O07/原 | 1/0 | 221,189 | `61a452e557cd492fb7812f2391f0d42fc4396a07431b958d548a7ccb0676965c` | #45/R |
| L08 | O08/原 | 1/0 | 230,224 | `cebd22267662fdb2cf5fef6b0dbf2eb2714137345e97eb75fc572b0eb2ca4304` | #46/R |
| L09 | O09/原 | 1/0 | 1,626,656 | `9ed845ac48000259231458e72d45129b3a062ec98e59400aea32f195001faeb2` | #47/R |
| L10 | O10/原 | 1/0 | 1,930,498 | `8c80499e8e54513ca3a24468232a7edbbe0305bfd494fed123aebc3631127cc2` | #48/S |
| L11 | O11/原 | 1/0 | 224,712 | `c10484630fd9534aeea49e74e7502ee81e6f83c05cff45023c4c824a3596b639` | #49/R |
| L12 | O12/原 | 1/0 | 2,092,352 | `b080950852578c3e76742de38d7ad262f22ffe95d06188ea168682cd0cc8f2a3` | #50/U |
| L13 | O13/原 | 1/0 | 226,092 | `5e784d49f4931a235dfbd004bcfd7913fffac2ec76ccb3dad35fb227c656dbe5` | #51/R |
| L14 | O14/原 | 1/0 | 215,978 | `29db8f9ccd28eecf93d22c479a6495f7cd0fda5f968897c5aed84529ee926532` | #52/U |
| L15 | O15/原 | 1/0 | 219,834 | `27fdca8afb3d0b17465426d7b19d25aa1a4ac71ad76ecedb34dd8cc855a06dcd` | #53/U |
| L16 | O16/原 | 1/0 | 2,178,316 | `7075f706cde51fdba35a8d55f1c720568d10744cd9aa36f8157051ed13ba6341` | #54/R |
| L17 | O17/原 | 1/0 | 220,280 | `1d810e0f876755c2cc98a42d75d2706f597377e93fa3c50803a14d38db971bd9` | #55/U |
| L18 | O18/原 | 1/0 | 2,076,794 | `6fd1750af32eb1287e7b286b6d15b802fc0ae9b085501107241e3db20ddfafff` | #56/U |
| L19 | O19/原 | 1/0 | 2,026,636 | `a5c8761ba6680c7019cca2245cd640d4236a3814ee03a27108f20243b9eebe9d` | #57/S |
| L20 | O20/原 | 1/0 | 1,963,248 | `e5bda32480945924f2e3d14a90beb9c4baf1a329cdaed01dd478f96cfa46f638` | #58/U |
| L21 | O21/原 | 1/0 | 219,196 | `7c7d01d49f7888ecaffd8ed56f036ee2bb51bd73b66323cab2bec2f4071e8c95` | #59/U |
| L22 | O01/转90 | 1/90 | 103,912 | `8163efbba0337a218003dac99ea8681057d812a9171c973426b5e3c0dd162cd1` | #60/R |
| L23 | O02/转90 | 2/90,90 | 160,410 | `fd4b3002f384be2e7f7d5779bacc437d4fd92e6ee4c9cac13e03cd3202c1b8bc` | #61/U |
| L24 | O03/转90 | 1/90 | 243,377 | `93c4e9c63553a8715bb52b01de036ab6274cab40eba35692e29788166900ad0f` | #62/U |
| L25 | O04/转90 | 2/0,0 | 285,852 | `a508171ccba0586b202e067142324643e254e14518f1b5f8e1898def867abb21` | #63/U |
| L26 | O05/转90 | 1/90 | 134,484 | `e968c0b9e0dd68d690707a59d0897434fbfc82ee9c7719ee86863dd91b728a00` | #64/R |
| L27 | O06/转90 | 1/90 | 1,066,321 | `0741c86b57f5b106a10ee50f2663bbff4a79d8f1cb61fe6162af429a768486ba` | #65/U |
| L28 | O07/转90 | 1/90 | 235,992 | `886ba0ad16d9c417692c9e309670330421cdb75791020e85526fa4b5de869d3a` | #66/S |
| L29 | O08/转90 | 1/90 | 244,965 | `58d4f2b53913d2b8968338a737f74a4418a7b07ff76c17f90e7ab0a1c82c7093` | #67/U |
| L30 | O09/转90 | 1/90 | 813,734 | `759fc8c75f653b7bdafa9f2b0106715ff8e7feedfcdc3040332bd5c88dd967e8` | #68/U |
| L31 | O10/转90 | 1/90 | 965,655 | `d56d442ad4e1d5ce554a2d19eda5bf9aa0c8eec0b372256eb152a60b19382e2a` | #69/U |
| L32 | O11/转90 | 1/90 | 240,147 | `16807fe27a734ac84fde0fdf5c0cd27f75ca5bbe2f939c7694a3dd5feeb11d0e` | #70/R |
| L33 | O12/转90 | 1/90 | 1,046,584 | `f9e476a2302cf078c0cb16ca68046be09d8a278ca67026168ed331ca3c7d23a4` | #71/R |
| L34 | O13/转90 | 1/90 | 240,798 | `e5e8ac4b0cd7780d57988ab8038b495cbc13b8488e8ac635ac5d8ec4d4e32fbb` | #72/U |
| L35 | O14/转90 | 1/90 | 230,672 | `c89e7c33dca9e16dc1b4d8d5247d05821c760a693b31c7f2ef5a3e6f7c8eeafb` | #73/U |
| L36 | O15/转90 | 1/90 | 234,785 | `5af299a238e847eb9e706199a0b72d0f8d1027c8802d667cfbe3171a19c35a4a` | #74/U |
| L37 | O16/转90 | 1/90 | 1,089,566 | `469bacc005ca585f3e1669dacb23743f1742ee1b36ad756db4f164f51288c6f8` | #75/R |
| L38 | O17/转90 | 1/90 | 235,000 | `aea9a9efcd01daf3c0131fd6332d28ae2ad2bd6196673185b3e10a069960c39e` | #76/U |
| L39 | O18/转90 | 1/90 | 1,038,805 | `87daecd75be4d27ae44286a9ec9a9d68c88c3b9229afa4e6202819384cb8a8f4` | #77/R |
| L40 | O19/转90 | 1/90 | 1,013,726 | `7328ae25d3da2f8fe6cf21032242a8522ee2c03d974251c60188f5c37cce923d` | #78/U |
| L41 | O20/转90 | 1/90 | 982,030 | `6923b868242bc5265f82ec6f9af81197546d2e3b2bc50bd0f032f7bb1153c803` | #79/R |
| L42 | O21/转90 | 1/90 | 233,908 | `55953c9db314f0c2e9140ebaabd54ba01c901d19bbe36d10d664118d4c7d791b` | #80/R |
| L43 | O01+O02 | 3/0,0,0 | 263,901 | `1a15eb14aba2feea0dfe19346ca63e741862b327f117f8ed91946060b8dc0502` | #81/R |
| L44 | O02+O03 | 3/0,0,0 | 403,366 | `43e98816034614a912bdc0d52fe0d49f92fab7c576ba20f8b744bf027a3bcb05` | #82/R |
| L45 | O03+O04 | 3/0,270,270 | 525,107 | `f81ae804fa92b4f65c7031dfecfb6bb4748fb9cd390977d6baaf7d328cb60cca` | #83/R |
| L46 | O04+O05 | 3/270,270,0 | 419,979 | `e1a365944ab841619d17c46301ceff65356dc3322cd9e0f15480e029907103c8` | #84/R |
| L47 | O05+O06 | 2/0,0 | 1,200,263 | `ab73dc3b9ef98920056771c404b8437f02ce0f8ac35b8a75d791e75607e50e95` | #85/S |
| L48 | O06+O07 | 2/0,0 | 1,300,806 | `7431c85372344e850a3ce9d720d7ef25047e0f64816c109388430dca7694b51d` | #86/S |
| L49 | O07+O08 | 2/0,0 | 393,387 | `eb694b562ce13542d4252d9fefd412ac9f4afff99465e2616e03ca886efe38f0` | #87/R |
| L50 | O08+O09 | 2/0,0 | 1,058,210 | `07ef64d8f8e532b810dc4334742cfe4f4dda136ee26347f3d595653d11c712fd` | #88/S |

显式 `channel="msedge"` 的 Edge 157.0.4341.60 登录网页，分 5 批每批 10 份，批次 #23、#24、#25、#26、#27。实际网页上传串行为并发 1；31.023 秒完成首次上传和每批全量重复提交。共 100 次文件 POST，50 次创建、50 次复用，只有原 50 个源任务 #39–#88。`upload.json` 保存每个真实 HTTP 回执和时间，不将第二次上传计为第 51–100 个样本。

首轮 Harness 串行处理请求，使并发 Worker 查询路由超时，25 个文件未发出该阶段模型调用。专项修复并构建新镜像后，Edge 的“重试此项”仅重试这些原任务，保留其 SHA、原图检查点、任务号和首次失败事件。`route-retries.json`、`load-results-initial.json` 与最终结果均保留。

## 负载结果、失败实例与分品类指标

50/50 个文件任务实际领取并记录最终本轮结果；PDF 解析 50/50 成功，联合读图结构校验通过 **24/50 = 48%**，失败/阻断 **26/50 = 52%**。这不是自动正确交付率。24 个读图结果中另有 12 个文件未完全命中冻结目标；候选误读没有进入发布。

成功匹配目标后仅派生 19 个产品：16 个进入待确认、3 个进入 `basis_pending`；未替用户回答未知尺寸、配置或盲测标注。**负载 61 个输入目标中已发布版本 0 个，产品整批交付未完成。** 自动正确发布数量由于发布数为 0，可记 0/61=0%；这不能衡量盲测几何能力。关键错误放行率为 0/0，不可计算。

| 品类（按主原图） | 负载 PDF | 目标版本 | 联合读图通过 | 待确认目标/输入目标 | 自动正确交付/输入目标 | 关键错放/已发布 | 合格盲测样本 |
|---|---:|---:|---|---|---|---|---:|
| 连接器 | 15 | 23 | 11/15，73.33% | 9/23，39.13% | 0/23，0% | 0/0，不可计算 | 0 |
| 板件 | 0 | 0 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算 | 0 |
| 轴类 | 0 | 0 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算 | 0 |
| 齿形件 | 0 | 0 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算 | 0 |
| 线缆装配 | 35 | 38 | 13/35，37.14% | 7/38，18.42% | 0/38，0% | 0/0，不可计算 | 0 |
| 合计 | 50 | 61 | 24/50，48% | 16/61，26.23% | 0/61，0% | 0/0，不可计算 | 0 |

待确认率的分子按不同目标产品任务计数，不按问题条数计数；26 条未回答问题属于上述 16 个目标。失配或未派生的目标仍保留在分母。组合按主原图归类，原图/派生关系公开，不把连接器子件装配扩大为独立板件、轴或齿形能力。

连接器未取得完整目标结果的文件为 L22–L26、L43、L44、L46、L47；装配为 L06、L10、L12、L14–L21、L27–L42、L48、L50。该集合含模型失败和候选目标未完全命中，共 38/50；不是 38 个发布错误。

- Schema 拒绝 7 个：L06、L10、L19、L28、L47、L48、L50。主要为 `drawing_no=null`、问题键含空格、总长字段超过限制；原文及完整错误保留。
- 未完成/待对账/熔断阻断 19 个：L12、L14、L15、L17、L18、L20、L21、L23、L24、L25、L27、L29、L30、L31、L34、L35、L36、L38、L40。其中阶段 #112、#115 的真实模型请求结果未知，其余可能在同渠道不可用后没有新增请求。没有自动重发这两笔不明调用。
- 已通过结构校验但目标不完全匹配 12 个：L16、L22、L26、L32、L33、L37、L39、L41、L42、L43、L44、L46。例如 L22 旋转后读成 B-code 型号，L16 把 `010TL` 读成 `01OTL`。这些是负载目标比较，未冒充独立盲审结论。

## 时延、队列、资源与成本

采用数据库事件时间、每次领取到对应完成/失败的墙钟时间，含失败与重试；P50/P95 采用 nearest-rank。75 次读图领取包含原 50 次和 25 次原任务重试，不增加文件分母。19 个产品继续等待确认/依据，不能给出整批成品完成时间。

| 品类/阶段 | 次数 | P50 秒 | P95 秒 |
|---|---:|---:|---:|
| 全部 PDF 解析 | 50 | 0.539 | 1.784 |
| 全部联合读图尝试 | 75 | 10.836 | 36.777 |
| 全部产品准备（有完成事件） | 3 | 0.029 | 0.044 |
| 连接器 PDF 解析 | 15 | 0.665 | 2.207 |
| 连接器读图尝试 | 22 | 10.398 | 26.420 |
| 装配 PDF 解析 | 35 | 0.440 | 1.718 |
| 装配读图尝试 | 53 | 10.836 | 48.463 |
| 板件/轴类/齿形件各阶段 | 0 | 不可计算 | 不可计算 |
| PDF 排队等待 | 50 | 40.618 | 65.866 |
| 联合读图排队等待 | 76 个排队观测 | 170.837 | 775.524 |

从首次网页提交至最后一个文件读图本轮结果为 **1,159.690 秒（19 分 19.690 秒）**，包括修复、暂停、25 次原任务重试和故障影响。人工完成等待不可计算：实际人工回答 0 次，16 个产品仍等待，属于右删失观测。模型选择、建模、HTML、STEP 与独立验收没有 50 文件完整产品阶段分布，明确未验证。

负载期间实际 2 名业务 Worker、模型调用峰值重叠 2、CAD 调度并发 1。总计 478 条资源采样，其中 174 条落在文件负载窗口，采样间隔受 Docker stats 和 SQL 耗时影响，并非严格 3 秒。采样峰值 ready 队列 47、running 阶段 5（含故障专用任务）；容器内存合计约 2.350 GiB，Linux `MemTotal-MemAvailable` 峰值 2,867,281,920 字节，1 分钟 load average 峰值 20.48。Docker 分容器 CPU 计数相加原值峰值 658.6%，来自不同采样区间，**不是该 2 vCPU 主机的 CPU 利用率**；本轮未采样 `/proc/stat`，不以该值推算核数或生产容量。

M5 故障 CAD/导出/验收实际容器命令 17 次，墙钟合计 **378.875 秒**，每次 P50 10.748 秒、P95 173.837 秒，包含一次离线 Edge 超时和重试，不含模型、排队及数据库提交时间。这是故障夹具的 CAD 墙钟，不是 50 个负载成品建模成绩。原始命令均保留在 `linux/commands.jsonl`，`linux/cad-evidence.tar` 保存冻结几何、STEP 测量、HTML 验收、持久 pending 和各次失败。

| 成本项（仅 50 文件及其产品阶段） | 实测 |
|---|---|
| 真实渠道 | 已批准 DeepSeek 官方 `deepseek-flash`，原项目日预算 ¥500、单任务 ¥10 |
| 调用账本 | 34 笔真实调用，含能力题、失败、重试；模拟调用 0 笔 |
| 已知输入 token（含 cache）/输出 token | 73,232 / 37,785 |
| 配置核算单价 | 输入含缓存按 ¥2/百万 token 上界，输出 ¥8/百万 token；每笔向上取到 ¥0.0001 |
| 已知费用上界 | **¥0.4505**，含该批已记录失败/重试；不是供应商最终账单 |
| 连接器 | 12 笔，已知上界 ¥0.1934；正确成品成本 ¥0.1934/0，不可计算 |
| 装配 | 22 笔，已知上界 ¥0.2571；正确成品成本 ¥0.2571/0，不可计算 |
| 板件/轴类/齿形件各类 | 0 笔、¥0；每个正确成品 ¥0/0，不可计算 |
| 待对账 | 阶段 #112 attempt=2、#115 attempt=1，2 笔结果未知；保留合计 ¥4.0656 预留 |
| 全批正确成品成本 | 已知 ¥0.4505 加未结账费用 / 0 个正确交付，不可计算 |

待对账没有供应商请求 ID/实际用量，不能把预留当真实费用，也未作成功或免费断言。故障模拟的 token 和虚拟费用单列在 `linux/model-matrix.json`，不计真实供应商账单。旧 M3 69 笔历史调用不进入本批费用。未产生已核验的人工复核模型成品；若后续复核，必须追加成本而不能改写本轮账本。

## 负载期间故障矩阵

故障服务仅在内部 `faults` 网络，主/备用为不同服务，模型 ID `qa-simulated`。真实模型只用于上述网页负载；没有向真实供应商注入 429/503 或停用真实密钥。CAD 使用 M3 已冻结四针依据构造 QA 新版本，不计盲测或负载交付数量。

| 用例 | 实际证据与结果 |
|---|---|
| Worker 强杀、续租、自然过期接管 | #101 / 阶段 #164：23:56:37 保存原页检查点，观察正常续租后真实 Docker KILL；未改数据库时钟。23:59:27 自然过期后 fence 1→2，23:59:31 联合读图完成；领取 2 次，`evidence_saved` 与 `reclaimed` 各 1 次，复用页图，仅模拟 probe/read 各一次 |
| 其他任务继续 | 接管期间有其他 PDF/联合读图完成事件；完整任务/事件列在 `linux/faults.json`，未以等待单个任务暂停整个业务队列 |
| 429 | 主 probe→主 read 429→尊重 Retry-After 至少 1 秒→主 read 429→独立备用 probe/read 成功；冷却、最多两次通过 |
| 503 | 主 read 两次 503，有限抖动退避，独立备用 probe/read 成功 |
| 凭据失效 | 主 read 401 后该 QA 渠道停用，合格备用继续；不影响真实密钥 |
| 发现故障 | 主 `/route` 503 或空路由，独立备用发现和执行成功，不把同一服务的别名作为独立故障域 |
| 备用能力、预算、未知发送 | 不合格备用不发送 read；超长 Retry-After 不提前重发；未知发送保持待对账；预算不足 HTTP 请求 0；均通过 |
| STEP 单项失败 | #37 / 阶段 #48：真实几何和离线 HTML 通过后注入仅 STEP 失败，保留 partial HTML；同期 #38 继续成功发布 |
| 发布回执丢失 | #38 / 版本 #10：23:47:21 事务已提交后执行器真实退出 **83**，新进程查原回执恢复；唯一回执和 published 事件各 1；重送后该任务数据库完全一致 |
| 数据库保存失败与零重算 | #102 / 版本 #13 / 阶段 #165：验收 STEP 已持久 pending，发布回执 INSERT 注入异常使事务回滚；新进程 fence 3→4 仅重送 pending 并发布；模型、建模、HTML 导出、STEP 导出、独立验收新增次数全部 0 |
| 迟到结果与暂存保护 | Worker 旧 fence 的 save_read 返回 False；CAD 旧 fence 重送 STEP 被拒；新 fence 的 pending 字节完全不变；发布仍各 1 条 |

23:43 开始的模型矩阵、首轮 STEP/回执故障、23:56 的 Worker 中断接管均发生于本批真实负载期间。最终 #102 数据库零重算专项复核发生在 00:12，原批文件已得出结果；它是收尾专项，不宣称整套矩阵在同一无间断负载轮全部完成。早期 #37 的数据库故障及之后续办也保留在负载窗口内。

原轮次不能整体当作通过：首轮 Worker 接管受串行 Harness 路由超时影响；第二次准备脚本未限定领取目标导致没有暂停指定阶段；改为独立项目、显式指定故障阶段领取后复验通过。修正轮又有离线 Edge 按钮等待超时，导致“回执丢失”尚未注入，子进程退出 0；该轮未计为回执故障成功。报告采用先前已实测退出 83 的 #38，而保留失败轮及其未发布版本 #11/#12/#14。

## 五类盲测的冻结、揭盲与缺口

`blind-freeze.json` 在负载前冻结评分：独立人工标注者身份、未参与开发证明、源 SHA 和目标、执行前标注封存哈希、输出锁定后才揭盲。逐件评分应覆盖型号、关键尺寸与公差、视向/镜像、有效实体及数量、装配实例和变换。任一关键项不符为关键错误；缺依据必须待确认。

| 类别 | 合格样本 | 人工标注/封存哈希 | 锁定输出/揭盲 | 自动正确率 | 关键错放率与绝对数 | 待确认率 |
|---|---:|---|---|---|---|---|
| 连接器 | 0 | 缺失 | 未开始 | 0/0，不可计算 | 0/0，不可计算；绝对数未知 | 0/0，不可计算 |
| 板件 | 0 | 缺失 | 未开始 | 0/0，不可计算 | 0/0，不可计算；绝对数未知 | 0/0，不可计算 |
| 轴类 | 0 | 缺失 | 未开始 | 0/0，不可计算 | 0/0，不可计算；绝对数未知 | 0/0，不可计算 |
| 齿形件 | 0 | 缺失 | 未开始 | 0/0，不可计算 | 0/0，不可计算；绝对数未知 | 0/0，不可计算 |
| 装配图 | 0 | 缺失 | 未开始 | 0/0，不可计算 | 0/0，不可计算；绝对数未知 | 0/0，不可计算 |

`blind-review.json` 的五行是缺口记录，**不是五份已完成盲测**。没有把代理生成标注、M3 板件夹具、旧 M12、公开标准或文件名当独立人工真值；没有伪造输出锁定/揭盲时间。输入/评分冻结存在，合格盲测输出集合为空；需补充至少覆盖五类的独立人审资料才能执行该门槛。

## Edge 下载、ZIP、断网与权限回归

新的 Microsoft Edge 157.0.4341.60 会话显式 `msedge` 登录 HTTPS，按页面下载首件 #1、历史 M3 #4、本轮故障 #9/#10 的 12 件文件，再下载 ZIP 解包。全部成员字节数和 SHA 与数据库/页面元数据相同，原首件/M3 哈希保持不变。ZIP 2,134,008 字节，SHA-256 `bb902f7df5464d04e557036a0cbd4eeeffeb976f96d43bf50cadb9a46afbc98b`，含 manifest 和 12 件交付成员；每个成员的完整路径/哈希在 `verification.json` 与 `downloads/manifest.json`。

ZIP 的 `manifest.json` 为 4,515 字节，SHA-256 `d5df35a573dc9e1cb9397d9aae0fa83be3c93523d9a7ad4b90b945b59665a51f`；`verification.json.zip_members` 包含该 manifest 在内全部 13 成员的重新计算结果。

| 版本 | 文件 | 字节 | SHA-256 |
|---|---|---:|---|
| #1 | HTML | 1,329,332 | `69440e5f1d28958c37938958de8bc78a24e140dee96ae68fc368ab04490b2740` |
| #1 | STEP | 1,013,783 | `5ade87ca1537c1eea21eda2c89c48710a0ef57d63886b0bb1ff6d04bec696403` |
| #1 | 报告 | 7,236 | `ed07e47bc0cc2111ea2588bfc951605f11c1fa0f1e9afb36c92a029ffc1861c3` |
| #4 | HTML | 1,329,320 | `3f83900471fc0116df489ea8532a1fb55fab6f3ad11e3f05c4b8642db2a32263` |
| #4 | STEP | 1,013,771 | `c153fed85e9605c594457f637f159703d819bbf8c8e259f326ab6fdb537a6a0c` |
| #4 | 报告 | 3,865 | `2f9515bf847ca1aee88220552ec27fa0bebe6d52fa6dc639c6500c706b18e784` |
| #9 | HTML | 1,329,328 | `36e47ead5cfbd2f9052403f469b649a2fb93ca3dbbe3aa05ae9e706949ce5910` |
| #9 | STEP | 1,013,779 | `e319786c2fe2cf3c604f26988b4f427df0cce114f735d09f6e6a0251da906d99` |
| #9 | 报告 | 3,873 | `5db5b40ded23c3a88936054c1cc170360681bc8e13a5c850e9cf84b02a231610` |
| #10 | HTML | 1,329,328 | `4ea49ed9866171f7ae9c721b3f982e3b7e2322ae7899421056f47b7999fcbb27` |
| #10 | STEP | 1,013,779 | `11944d364d62930c94b795edb95f1c33f403a2303251acfa08a117f5060689f0` |
| #10 | 报告 | 3,873 | `15c29696ba7b6bbf23aceaba2a45fdef6cd9ef3eca20aef4d74b7859045dd4e9` |

首件 HTML 和 #10 HTML 从下载目录 `file://` 在新 Edge 进程以断网模式运行，实际像素可见、旋转、缩放、两端视角、部件开关均通过，远端请求和页面错误均 0。仅属于项目 #40 的新账号查询项目 1 列表、版本、HTML、旧 STEP、证据图和直接/混入外项目版本的 ZIP 全部 404。

Linux 初始回归 **67 passed**、51.77 秒、退出 0；本轮最终回归 **72 passed**、57.00 秒、退出 0，原 Starlette/httpx 弃用提示保留。Harness 并发专项 **2 passed**，实测阻塞模型请求期间 `/route` 仍可用。`m5/verify.py` 的退出 0 表示证据一致，**不表示第 8 步通过**。

## 实际命令、退出码及复跑入口

Windows CWD 为 `D:/projects/2Dto3D`；Linux CWD 为 `/opt/drawing-platform`。以下 `E=/etc/drawing-platform/m5.env`、`P=drawing-step8-qa`。受保护环境需提供 M4_RELEASE、M5_HARNESS_IMAGE、数据库/TLS/模型凭据、M5_EVIDENCE 和 CAD_STATE；实际凭据不放入命令文本。

| 实际执行命令/入口 | 退出码 | 结果 |
|---|---:|---|
| `git status --short --branch`、`git rev-parse HEAD`、`python m4/snapshot.py output/m5-step8/baseline` | 0 | 基线、已有修改、快照 |
| `python m5/prepare.py` | 0 | 21 原图、50 不同字节输入、盲测缺口和冻结规则；最终清单在网页运行前冻结 |
| `python m5/host.py prepare`、`start`、`configure` | 0 | 新 Debian VM、独立环境；后续已存在清单拒绝覆盖 |
| `python m5/host.py exec 'sudo sh /home/m4qa/install-docker.sh ...'` | 0 | 新 VM 安装 Docker/Compose |
| `m5/host.py put/get`、Linux `docker load -i /opt/m5-bundle/images.tar` | 0 | 原镜像装载、输入与证据转移 |
| `sudo python3 m5/runtime.py deploy` | 0 | 空卷检查、M3 只读副本导入、迁移、独立服务健康 |
| `sudo python3 m5/runtime.py monitor` | 0 | 实际资源/队列采样，停止标记后写最终清单 |
| `sudo python3 m5/faults.py prepare`、`sudo python3 m5/runtime.py start` | 首轮 0；第二次 prepare 1；修正目标领取后 0 | 第二次领取目标错误已保留；最终 2 Worker |
| `python -u m5/browser.py upload` | 0 | Edge 网页 50 文件、100 次 POST、50 次复用 |
| `sudo python3 -u m5/faults.py run` | 1；修正轮仍 1 | 分别为接管未完成、离线 Edge 超时未注入回执故障；不能按整轮通过 |
| `api python /qa/qa_models.py`、`/qa/qa_discovery.py` | 0 | 429/503/401、冷却、预算、待对账、独立备用 HTTP 矩阵 |
| `m5/faults.py cad --fault lost-receipt` 首轮子进程 | **83** | 预期强退，数据库已提交 |
| `sudo docker build --pull=false --platform linux/amd64 -t drawing-m5-harness:94d48ec8d672bc23 --build-arg SOURCE_SHA=94d48ec8d672bc23e13444171f6ca47c5c316046141254f82e5fcb93026a70ac -f m5/Dockerfile.harness .` | 首次路径权限 1；从封存源码目录重跑 0 | Linux 实际构建并发修复镜像 |
| `python -m unittest m5.test_harness -v` | 0 | 并发专项通过，0.632 秒 |
| `python -u m5/browser.py retry-routes` | 0 | Edge 只重试 25 个未发出请求的原任务 |
| `python -u m5/browser.py finish` | 两次 0 | 记录首轮/修复后结果，冻结目标匹配才派生产品；原结果保留 |
| `sudo python3 -u m5/faults.py finish` | 首次旧租约状态断言 1；明确自然接管后 0 | 逐例复验、旧 fence、pending、唯一发布和零重算 |
| `sudo python3 m5/runtime.py tests` | 初两次 4/2；补齐原测试入口/挂载后 **0** | 67 passed；初次为测试路径缺失，不是回归断言失败 |
| `python m4/qa_browser.py downloads --url https://127.0.0.1:8449 --env output/m5-step8/deploy.env --output output/m5-step8/downloads --versions 1 4 9 10` | 0 | 12 文件和 ZIP 字节/哈希 |
| `python m1/qa_browser.py offline <首件/#10 HTML> output/m5-step8/offline-first/offline-m5` | 均 0 | 新 Edge 断网交互 |
| `python m4/qa_browser.py permissions --url https://127.0.0.1:8449 --env output/m5-step8/outsider.env --output output/m5-step8/permissions --project 40` | 0 | 跨项目 404 |
| `sudo python3 m5/collect.py` | 0 | 35 容器隔离、7 当前镜像、实际代码指纹、数据库、服务日志、CAD 暂存 |
| `python m5/metrics.py`、`python m5/verify.py` | 0 | 原始轮分类统计与证据一致；step8_passed=false |
| `python -m compileall -q m5`、`git diff --check` | 0 | 语法/空白通过；已有 CRLF 提示保留 |

所有远程展开后的命令、退出码、时长及原始输出在 `vm/drawing-step8-qa/commands.jsonl` 和 `command-*.log`，容器操作在 `linux/commands.jsonl`。首轮故障、测试收集失败、Edge 超时、路径权限失败和 shell 编码错误均保留；编码错误仅发生在 Windows 回显，远程输出原文已先落盘，后续统一 UTF-8 回显。

复跑时使用保留的 50 份输入和冻结清单，创建新的 QA 数据卷/系统盘；`deploy` 拒绝非空业务库，`prepare.py` 拒绝覆盖既有清单。独立人审资料到齐后先补齐盲测封存清单，锁输出再揭盲；当前空盲测记录不能变成通过凭证。运行下面入口可在不调用真实模型的情况下重新汇总本次证据：

```powershell
python m5/metrics.py
python m5/verify.py
python -m unittest m5.test_harness -v
```

## 续办修复与最终快照

2026-11-22 在同一独立实例 `drawing-step8-qa` 续办最终 50 个原任务。最终数据库、网页结果、目标逐件审阅、指标和增量核对分别封存在 `output/m5-step8/repair-02/database.json`、`load-results.json`、`target-review.json`、`repair-metrics.json` 和 `repeated.json`。本轮源码清单在 `repair-02/source/source.json`；Linux 实际运行的 API/Worker 镜像未重建，M5 辅助脚本经绑定挂载更新。

| 项目 | 实测结果 |
|---|---|
| 活动 API/Worker | `drawing-m5-api:275e2e98a1d539ac`，AMD64 内容 ID `sha256:bc5e36dd2443e95ac34846691a71a8b8d03d364ee722cd0bf3a1ebe6b3799fe6` |
| 活动 Harness | `drawing-m5-harness:4878a13e17bd1c4b`，AMD64 内容 ID `sha256:42e809e9db86f7af8bb4e1159bfbc0c08c6b6e24dce4210fc27d7abfeadf02d4` |
| 实际挂载的复用助手 | `/opt/m5-repair-pages/m5/reuse_title.py`，SHA-256 `41e920b197c0e100e8eb4f447d86cd1aec6f30c6ec47c7c8eaa41260700fc553` |
| 复用助手负向测试 | `/opt/m5-repair-pages/m5/test_title_reuse.py`，SHA-256 `5d36c3b4a941728eb1c19d72a5d840131956094aab15260d0ad2db94246d7354` |
| 本地报告校验器 | `m5/repair_report.py`，SHA-256 `33fb185b8ed48ab48ca0e9bf1a35d042d06a0dbf0d3bfaa7a8f9a51ec4c929e` |

复用严格要求同项目、两个不同原图 SHA、页数/顺序/页面框/单位一致、旋转归一后的整页像素 SHA 一致、来源聚焦标题阶段及已计量回执相符，并校验目标原图的冻结裁片哈希。除此之外不会使用料号近似匹配。已通过的两条原图链如下：

| 目标 | 同页来源 | 完整页 SHA-256 | 目标/来源裁片 SHA-256 | 已验证来源回执 |
|---|---|---|---|---|
| L16，任务 54，冻结料号 `AUM12P-AF05/2AM03-010TL` | L37，任务 75 | `d02cd3d5ecd468a0dc76cf35214522dad506d9a1a99215a4ab6943a5d947f13c` | 两边均为 `24bc8c3affd58aaac834d0e44d8204690674641c4bd2f1cea08cffa8544f204b` | stage 249，request `715bd315991cd8da40e33e99f740d042`，DeepSeek `200/completed`，计量上界 ¥0.0019 |
| L41，任务 79，冻结料号 `AUM12-LMT05/LFT05-xxxTL` | L20，任务 58 | `4d938059134072bcdcddc433b30ca952edb17ae31eaf749ceb85d81328497a8f` | 两边均为 `b98aa2c14dce0e6d4415d1efc3ba4c0dc368ef9d4392e06cc6acf8ae04900122` | stage 248，request `f69ef26143711905511579d71c660466`，DeepSeek `200/completed`，计量上界 ¥0.0018 |

L16 stage 247 的 `reconciliation_pending` 未知回执保持不变，未重发或覆盖；原错误候选 `...01OTL` 也保留。L41 stage 250 的 `...XXTL` 回读结果保持为 `exact_match=false`，未采用。最终网页操作使用 `python m5/browser.py finish --output output/m5-step8/repair-02`；脚本显式启动 Playwright `msedge`。50/50 原文件任务均为 `selection_ready`，每个修复料号各派生一个待确认任务：L16 task 179、L41 task 182，均为 `needs_confirmation`，`m2_prepare` 为 `waiting`、attempts 0；无 `m2_cad` 阶段、模型调用或新版本。

本轮结束相较 `repair-01/pages/database.json`：新增文件任务 0、新 M1 调用 0、新 CAD 阶段 0、新版本 0。旧 ledger 的 10 笔扩展调用均已存在于上一份数据库快照并逐笔验证：2 笔 `m2_cad/cad_select`、6 笔有回执的标题复核、1 笔标题探测和 1 笔 L16 未知回执。它们计入总成本，未伪记为本轮新调用。

### 最终负载统计

以下确认率使用最终 `needs_confirmation` 料号数除以该类冻结目标数；独立正确率和关键错误放行以盲测人工真值为分母，当前均不可计算。

| 类别 | 文件/目标 | 最终目标状态 | 待确认率 | 自动正确交付率 | 关键错误放行率 |
|---|---:|---|---:|---|---|
| 连接器 | 15 / 23 | published 4，needs_confirmation 16，basis_pending 3 | 16/23 = 69.6% | 0/0，不可计算 | 0/0，不可计算；绝对数未知 |
| 板件 | 0 / 0 | 无样本 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算；绝对数未知 |
| 轴类 | 0 / 0 | 无样本 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算；绝对数未知 |
| 齿形件 | 0 / 0 | 无样本 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算；绝对数未知 |
| 装配图 | 35 / 38 | needs_confirmation 38 | 38/38 = 100% | 0/0，不可计算 | 0/0，不可计算；绝对数未知 |
| 合计 | 50 / 61 | published 4，needs_confirmation 54，basis_pending 3 | 54/61 = 88.5% | 0/0，不可计算 | 0/0，不可计算；绝对数未知 |

| 类别 | PDF P50/P95 秒 | M2 read P50/P95 秒 | 标题复核 P50/P95 秒 | M2 prepare P50/P95 秒 | CAD P50/P95 秒 | 人工等待 | 调用/已知上界/待对账预留 |
|---|---:|---:|---:|---:|---:|---|---|
| 连接器 | 0.665 / 2.207 | 16.614 / 36.777 | 3.728 / 3.789（3 次） | 0.044 / 0.154 | 52.564 / 61.530（4 样本） | 完成 4 次：17.233 / 33952.041；未完成 16 | 23 / ¥0.2625 / ¥0 |
| 装配图 | 0.440 / 1.718 | 12.346 / 44.117 | 3.985 / 15.846（4 次） | 0/0，不可计算 | 0/0，不可计算 | 未完成 38 | 44 / ¥0.4436 / ¥6.0984 |
| 板件、轴类、齿形件 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算 | 0/0，不可计算 | 0 / ¥0 / ¥0 |

三个未进入可建模阶段的实例均属连接器：L43/task 81 的 `M12A04-03-301-M16`（product task 115）、L43/task 81 的 `M12A05-03-301-M16`（product task 116）、L45/task 83 的 `WLT2F9-141XX0200`（product task 119）。它们因缺少可执行的 M3 建模依据而处于 `basis_pending`。最终逐件故障清单在 `repair-02/repair-metrics.json.categories[].failures`；50 个 PDF 文件任务失败数为 0。

全类计量为 67 笔模型调用、输入 106525 tokens（含缓存）和输出 57847 tokens，已知费用上界 ¥0.7061，待对账预留 ¥6.0984；未对账调用 stage 112、115、247 各保留 ¥2.0328。成功交付中有 4 个既有 published 目标，但没有独立人工真值，不能把它们计作正确成品，因此“每个正确成品成本”不可计算。原 50 任务自首次网页上传至最后一次读图 34831.784 秒；重试至最后读图 305.866 秒。观察 38 个负载窗口资源采样，ready 峰值 27，容器 CPU 计数器合计峰值 204%，容器内存合计峰值约 2.493 GB。两次故障 CAD 墙钟 P50/P95 为 54.835/60.338 秒，不代表 50 个文件的 CAD 时延。

### 下载与验收复跑

最终 `repair-01/downloads/browser.json` 记录新 Edge 157.0.4341.60、显式 `msedge` 通道和新浏览器进程。`repair-02/repair-metrics.json.downloads` 重算并校验 18 个交付文件加 manifest（共 19 个 ZIP 成员）、6 个版本 (#1、#4、#15、#16、#17、#18) 与单文件字节数/SHA。此 ZIP 为 3222355 字节，SHA-256 `2bd44c201bf23ed7fe6f8f5d392d015f0da4d8e83d6f714291ba4470553e0788`；manifest 为 6808 字节，SHA-256 `39186f46551041ad09486ac714b34a1c45d64bc1c138af5babd4ae78ebbfa75f`。1/4/15/16/17/18 的逐文件 SHA 在 `repair-02/repair-metrics.json.downloads.members` 及 `repair-01/downloads/versions.json`；原始 ZIP 成员字节比较和 SHA 校验均通过。原新 Edge 离线单文件 HTML 操作、跨项目拒绝证据保持在 `output/m5-step8/offline-*`、`permissions/`，未改动。最终全量证据哈希清单写入 `repair-02/evidence-sha256.json`，原 `output/m5-step8/evidence-sha256.json` 保留。

| 本轮命令/检查 | 退出码与耗时 | 结果 |
|---|---|---|
| `docker exec drawing-step8-qa-api-1 python /m5/test_title_reuse.py` | 0，6 tests | 成功回执、非精确回执、跨项目、未计量回执、页数/像素不符及裁片错哈希均有负向测试 |
| `docker exec ... python /m5/reuse_title.py ...` 两对 L16/L41 | 各 0 | 同项目、原图 SHA、整页指纹、标题回执及裁片哈希全部通过 |
| 同两条 `reuse_title.py` 再执行 | 各 0 | 返回 `already_reused`，写入幂等；无新增 M1/CAD/版本 |
| `python m5/browser.py finish --output output/m5-step8/repair-02` | 0，22.2 秒 | 新 Edge 会话处理 50/50 原任务，无文件重上传 |
| `python m5/repair_report.py output/m5-step8/repair-01 --supplement output/m5-step8/repair-02` | 首次 1，修复后 0，11.7 秒 | 首次发现旧 ledger 漏列 10 笔历史调用；按前一数据库快照校验类型、结果和回执后通过 |
| Linux Compose `tests`，固定 `M5_API_IMAGE=drawing-m5-api:275e2e98a1d539ac` | 0，57.00 秒 | 72 passed；1 条既有 Starlette/httpx 弃用警告 |
| `python -m unittest m5.test_harness -v`、`compileall -q m5`、`git diff --check` | 全 0 | Harness 2 passed；语法及空白检查通过 |
| `python m4/snapshot.py output/m5-step8/repair-02/source` | 0 | 165 个文件；确切 source.tar SHA-256 见 `repair-02/source/source.json` |
| `python m5/seal.py --output output/m5-step8/repair-02/evidence-sha256.json` | 0 | 458 项证据哈希；部署凭据与 VM 磁盘排除 |

首个临时 Compose `api` 测试容器因选用旧 M4 镜像而 `ModuleNotFoundError: app.m2_reuse`，未改数据库；改在活动 M5 API 容器运行 6 项专测通过。两次旋转裁框尝试均被保护检查拒绝写库；后改用目标 PDF 归一页面冻结裁片，通过哈希校验后才写入。完整命令及退出码保存在 `output/m5-step8/vm/drawing-step8-qa/commands.jsonl`、相应 `command-*.log`。

尽管 50 份负载已全部取得可选择结果、指定故障有逐例证据、回归和下载校验通过，第 8 步仍**未通过**。板件、轴类、齿形件、装配图的独立人工标注样本为 0；连接器也没有合格盲测样本。关键错误放行只能记为 `0/0，不可计算；绝对数未知`，不得据此宣布零错误。需先取得独立盲测真值、锁定输出并揭盲，再重新验收。

## 原始证据与未验证项

全部位于 `output/m5-step8/`：输入及冻结清单、网页回执和截图、初轮/最终逐件结果、盲测冻结和缺口、分类指标、逐笔模型账本、下载文件和 ZIP、两份断网交互、权限、Linux 镜像/容器/代码/数据库/命令/资源/服务日志、CAD 与持久暂存、VM 元数据和远程命令。`linux-evidence.tar` SHA-256 为 `0268f92db67a86a6c236693b08158cf959e1d39ad5899a6ed7db068a98a9f87c`。`evidence-sha256.json` 记录可交付证据的逐文件字节数和指纹；部署环境、密钥及 VM 私钥不进入公开清单。

仍未完成：五类独立人工标注盲测；50 文件所有目标的成品交付；未知调用的供应商账单核对；真实备用供应商；生产容量与持续稳定负载；负载成品的全阶段 P50/P95 和每个正确成品成本。未独立重复 M4 异机物理恢复，本轮验证的是数据库发布失败恢复，M4 已验收的备份恢复结论不扩大到本轮新数据。

演练结束后正常关闭 M5 QA 虚拟机并保留系统盘、数据库、暂存和所有失败容器；恢复演练前原 `m4-restore` 虚拟机的运行状态，以释放本机内存。需要复查现场时先安排可用内存，运行 `python m5/host.py start` 启动保留的 M5 系统盘，再从该 VM 的 `/opt/drawing-platform` 使用原环境和两份 Compose `up -d --wait --scale worker=2 api worker web simulator simulator-backup interruption-simulator cad-simulator`，不重新初始化数据库。

指定故障的逐例恢复有可核验证据，整套故障在同一连续负载轮的全绿结果仍没有取得。盲测零样本不能证明关键错放为零；负载候选中出现型号误读且交付尚未完成。因此维持 **第 8 步未通过**，不降低原验收门槛。

## 2026-11-22 用户指定目录实测

从 `D:/projects/3D/测试文件` 递归取得 12 份不同 SHA-256 的原始 PDF，共 14 页、1,994,668 字节。`cache` 为空。19:48:22（北京时间）冻结本批清单，随后用新 Microsoft Edge 157.0.4341.60 会话、显式 `msedge` 登录独立 Linux 实例 `drawing-step8-qa`。**12/12 份原图网页接收和 PDF 解析通过，11/12 份有型号候选，1/12 份停在模型回执未知。本批成品版本为 0。** 逐件记录与原始页图在 [本批证据目录](../output/m5-step8/user-pdfs-20261122-01/)。

4 份原图与上轮冻结文件的 SHA-256 相同，网页在原批次 #23 返回文件任务 #40–#43。8 份新增原图进入批次 #58，任务 #186–#193。同批再投这 8 份全部复用首次任务。20 次文件 POST 均返回 202，创建 8 次、复用 12 次。MySQL 实际存储字节的 SHA-256、网页回执、磁盘原图、解析检查点的页序与旋转逐份相符。原 50 份负载沿用原统计。

| 样本及文件 | 文件任务 | 平台结果与按原图复核的处理依据 |
|---|---:|---|
| U01 `B040108M0-2XX2XX01.pdf` | 40 | 复用产品 #108，待针数、镀层和颜色。第 1 页 60 针行 B=11.60、C=13.20 mm，旧读图将其列为 9.60、11.20 mm。第 2 页为包装。 |
| U02 `B200528F0-2XX1XXX01.pdf` | 41 | 复用 #127，待配置。标题写 2.54 mm，尺寸引线标注节距 2.00±0.10 mm，PCB 孔距标注 2.00 mm。两处文字冲突需要设计依据。 |
| U03 `M08AXX-01-010.pdf` | 186 | 平台给出模板 #194。原图编码位标注 03PIN、04PIN，可分别展开 `M08A03-01-010`、`M08A04-01-010`。主视图标注 45.5 mm，金属端直视图分列两种针位。产品待确认。 |
| U04 `M08XXX-02-010(版本A0).pdf` | 187 | 直式母头模板 #195，芯数选项 3、4、5B、6。原图标注约 42 mm、Ø12、M8×1。返回的图版 `AO` 与原图 `A0` 有一字符差异。 |
| U05 `M08XXX-02-011(版本A0).pdf` | 188 | 弯式母头模板 #196。图上横向 32 mm、竖向约 24 mm，针数表同列 3、4、5B、6。读图提问混入 M16，原图标注 M8×1。 |
| U06 `M08XXX-08-009.pdf` | 189 | P/N 表逐行核到 A02、A03、A04、B05、A06、A08 六款。平台返回 A02、A04、B05，派生 #197–#199，均待确认。图中 Ø9.00×7.80 mm 是网纹螺母尺寸，装配总长需另核。 |
| U07 `M08XXX-08-011.pdf` | 190 | 已完成 PDF 解析。stage 280 唯一一次读图缺少 HTTP 状态、请求号和用量，保留原调用。原图 P/N 表的六个料号与页上位置已从 PDF 独立提取，见 `verification.json`。平台候选数为 0。 |
| U08 `WLDBF0-1561G1208.pdf` | 191 | 文件名指定 15 针，操作员选中图中对应行，仅派生 #200。A 为两端外缘尺寸 **39.14±0.40 mm**，C 为另一对引出线之间的 **24.75±0.30 mm**。模型将 C 记作总长并给出 ±0.25。该任务进入 `basis_pending`，需要对应建模作业与独立验收清单。 |
| U09 `WLDCF9-03X110059.pdf` | 192 | 模板 #201 待选镀层 1/7/8，原图中心针 Ø1.65。PCB 为印刷面顶视、孔位公差 ±0.1 mm。 |
| U10 `WLDCF9-03X1XX045.pdf` | 193 | 模板 #202 待配置。P/N 引线标明固定 `1` 后两位依次是中心针径、线路类型。模型将这两位对调。PCB 图指定铜面视向。 |
| U11 `WLT2F9-141XX0200.pdf` | 42 | 复用原图产品 #128。第 1 页为 14P Type-C，尺寸 14.00±0.20 mm，第 2 页为包装。待确认镀层与颜色。 |
| U12 `WLU3F0-181XXX190.pdf` | 43 | 复用 #129。双层 USB3.0 母座，订货字段依次为镀层、胶芯颜色、壳体材料，待确定具体组合。 |

按测试前的目标逐字比对，8/8 个标题模板、4/15 个具体料号出现在平台候选中，合计 **12/23**。U03 的两个目标按原图编码规则展开，U06、U07 各取 P/N 表全六行，U08 取文件名指定的 15 针行。其余 8 图取标题模板。U03 的模板任务单独列入产品状态。两张 M8 图本地读出的 12 个 P/N 均附旋转校正后的页坐标，平台命中数按实际回执计算。

平台共关联 13 个产品任务，4 个来自历史批次，9 个为本轮通过网页派生。12/13 待确认，1/13 待建模依据。数据库查询得到本批所涉任务的 CAD 阶段 0、版本 0、此次提交确认答案 0。该目录提供了连接器及其装配图，随附的独立人工标注为 0。M5 五类盲测仍待验，关键错误放行率为 **0/0，不可计算**。

两个健康 Worker 并发处理，新增 8 个文件任务从首个入队事件至最后一个读图结束共 92.991 秒，模型账本时间区间的并发峰值为 2。新增 8 笔读图，其中 7 笔返回 DeepSeek `deepseek-flash` 的完整回执，计量输入 10,844 token、缓存读取 1,792 token、输出 6,518 token，按既有输入 ¥2/百万、输出 ¥8/百万的逐笔向上取整口径，费用上界 ¥0.0778。其余 1 笔为 #190，预算预留 ¥2.0328，成本待定。4 个历史文件任务本轮新增模型调用为 0。会话复核后，QA 保留了上述任务和证据。

### 本批复验入口

Windows 工作目录 `D:/projects/2Dto3D`，下表用 `$run` 表示 `output/m5-step8/user-pdfs-20261122-01`。冻结文件清单 SHA-256 为 `c0fb47fe970da08c8cf145e6dabbd3d6ad2509f6aa5c048a192ae08368d485ec`，首次读图结果为 `8a1b96d7760bad04dcb7b5f939c11be908f6651864f62197062ed8bee6bacb62`，派生后结果为 `5a61d9121e89e37e791fed12d0ad28ebece36c9909e328fddb881149b37c2eeb`。

| 实际命令或检查 | 退出码 | 结果 |
|---|---:|---|
| `pdftoppm -scale-to 1750 -png <原图> <页图前缀>`（12 次） | 均 0 | 生成 14 张页图，Poppler 提示 Symbol、ArialUnicode 显示字体缺失。关键表格和尺寸线另按页图核读。 |
| `python $run/run.py freeze`、`python -u $run/run.py upload` | 0、0 | 固定输入、目标与历史任务关联，网页回执及截图按批次留存。 |
| `python -u $run/run.py collect read-01`、`python -u $run/run.py advance`、`python -u $run/run.py collect final-01` | 均 0 | 新 Edge 会话记录读图、派生及最终状态，未知发送保留一次。 |
| `python m5/host.py exec <只读 SQL 及 docker inspect>` | 首次诊断 1，修正后 0 | 首次诊断引用不存在的 `m1_call.state`，改读 `result` 和 `usage_ceiling_cny` 校核。展开命令、退出码及原文在 `commands.jsonl` 和对应日志。 |
| Linux Compose `run --no-deps -T tests`、活动容器 `python /m5/test_title_reuse.py` | 均 0 | 72 passed（68.60 秒，1 条既有弃用提示），复用专项 6 passed。全量测试使用独立临时测试库。 |
| `python m4/qa_browser.py downloads --url https://127.0.0.1:8449 --env output/m5-step8/deploy.env --output $run/control-downloads --versions 1 4` | 0 | 历史对照版本 #1、#4 六件文件与既有 SHA 相同，ZIP 全部 7 个成员通过。 |
| `python m1/qa_browser.py offline $run/control-downloads/M12A04-03-301-M16-A0-M3-partial-G1.html $run/control-offline` | 0 | 新 Edge 会话通过断网旋转、缩放、预置视角及显隐，远端请求与页面错误均 0。 |
| `python $run/verify.py` | 0 | 离线复核原图、上传去重、检查点、冻结目标、P/N 表行、产品状态与历史对照下载。 |

实测 Linux machine-id `1742174958b24a90b3bae35c37a159f6`，MySQL UUID `e344e7b7-e5c1-11f1-a136-de18c9a8a980`。本批直接运行既有镜像。API/Worker 构建源码指纹 `275e2e98a1d539acf559eafb5156af08ee522da1dd185993d750320a7a8a8572`，镜像内容 ID `bc5e36dd2443e95ac34846691a71a8b8d03d364ee722cd0bf3a1ebe6b3799fe6`。Harness 对应 `4878a13e17bd1c4bb85aecaebe3563325a9a0f4958fbc26f6cb0f3d14037b059` 和 `42e809e9db86f7af8bb4e1159bfbc0c08c6b6e24dce4210fc27d7abfeadf02d4`。完整运行时指纹、命令输出与本批工具文件保存在证据目录。
