# 官网外部素材来源与授权登记

本文件登记 `docs/assets/` 中**非本项目手写**的每一个视觉资产。规则：先记来源，再进仓库。
最后更新：2026-10-21

## 生成类图像

| 文件 | 来源 | 授权 | 生成日期 | 说明 |
| --- | --- | --- | --- | --- |
| `hero-art.jpg` (131,477 B) | Qoder ImageGen 本地生成的概念图（原始件 1792×1024，未随仓库分发） | 本项目自建资产，未使用任何第三方受版权保护图像 | 2026-10-21 | 原图右下角带 `Qoder AI生成` 水印，已裁掉 y≥884 的整条底部区域物理移除，成品 1792×884；另加底部/左侧/右下角三层暗角压暗以承载白色文字 |
| `hero-art.webp` (64,960 B) | 同上 | 同上 | 2026-10-21 | q=80, method=6 |
| `hero-art.avif` (35,473 B) | 同上 | 同上 | 2026-10-21 | q=48 |
| `hero-blur.jpg` (16,804 B) | 由 `hero-art.jpg` 离线高斯模糊生成 | 同上 | 2026-10-21 | 给不支持 `backdrop-filter` 的浏览器当磨砂面板的静态底，零运行时开销 |
| `hero-art-mobile.jpg` / `.webp` (64,593 / 33,750 B) | 由 `hero-art.jpg` 裁 620×884 竖幅 | 同上 | 2026-10-21 | 窄屏用，取景框在笔记本屏幕内部的壁画上，指尖光点居中 |

## 字体

| 文件 | 来源 | 授权 | 抓取日期 |
| --- | --- | --- | --- |
| `fonts/michroma-latin.woff2` (17,908 B) | `https://fonts.gstatic.com/s/michroma/v21/PN_zRfy9qWD8fEagAPg9pTk.woff2`（latin 子集） | SIL Open Font License 1.1 | 2026-10-21 |
| `fonts/OFL-Michroma.txt` (4,395 B) | `https://raw.githubusercontent.com/google/fonts/main/ofl/michroma/OFL.txt` | 许可证原文 | 2026-10-21 |

授权核实方式：`https://raw.githubusercontent.com/google/fonts/main/ofl/michroma/METADATA.pb` 读回 `license: "OFL"`，设计师 Vernon Adams，版权行 `Copyright 2011 The Michroma Project Authors`。字体目录在 `google/fonts` 仓库的 `ofl/` 下而非 `apache/`。

自托管原因：受众在国内，`fonts.googleapis.com` 不可靠。全站外部字体请求数为 0。

## 明确没有采用的路

- **Wikimedia Commons 的《创造亚当》公有领域真迹**：计划用它当壁画底层，但本机实测 `commons.wikimedia.org` 连接超时、`upload.wikimedia.org` TLS 握手超时，两个域都不可达，故放弃，改为整张生成。
- **图片搜索返回的候选图**：来源为 `alifei04.cfp.cn`（视觉中国/CFP）、`hellorfimg.zcool.cn`（华盖 HelloRF）、`lgpic.3d66.com`、`pic3.zhimg.com`，均为 rights-managed 或平台水印预览图，**有侵权风险，一律未采用**。

## 仓库内既有资产（非外部，未重新授权）

`product-bag.jpg`、`product-bag-scene.jpg`、`product-jeans.jpg`、`ai-01.jpg`、`shot-*.jpg`、`demo-tour-poster.jpg`、`demo-tour.mp4` 为项目原有演示素材，本轮未改动其来源与授权状态。

## 应用截图与录屏（2026-10-22 重拍）

此前 `shot-*.jpg` 与 `demo-tour.mp4` 是 2026-10-18 的产物，界面已按 PSD 重做，故整体重拍。

| 项 | 值 |
| --- | --- |
| 捕获日期 | 2026-10-22 |
| 服务 | 隔离副本 `%TEMP%\shopmind-capture` 上运行 `app.web_main`，端口 8767；真实 workspace 未被读写 |
| 数据 | 项目 `prod_c9304218d3cb`（轻序 Daily Tote 01 云苔绿通勤托特包） |
| 视口 / DPR | 1600×1000 @ dpr 2，降采样到画廊 1600×1000、轮播 1100×688；录屏 1280×716 @ 30fps |
| 工具 | Playwright 驱动本机 Edge + `dev/design/tools/shot.py` 的防抖手法（networkidle → 图片 complete → fonts.ready → 注入 animation:none → scrollTo(0,0)） |
| 路由 | 通过 `showPage(id)` 逐页切换，并显式调用 `renderUnderstanding` / `renderDirections` / `renderContent` / `renderExportInfo` / `renderJudgmentPage` / `renderExportDonePage` / `renderBrandPage` / `refreshHistoryPage` / `refreshMaterialPage` / `refreshProductPage`。载入项目后 App 不会自动重绘这些页，必须显式调用才显示真实数据 |
| 视频 | Playwright `record_video_dir` 出 webm，ffmpeg 8.1.1 转 H.264 / 1280×716 / 30fps / CRF 25，37.4 秒；海报取第 12 秒帧 |

**关于素材路径的一处数据不一致（如实记录）**：项目 `run_meta.json` 记录的 `material_path` 指向 `workspace/uploads/20261018_185928`，但该目录**只含 prompt 与 xlsx、不含任何商品图**，导致「商品理解」页显示"暂无商品图"。同一批次的 `20261018_190121` 含 `商品资料_1.xlsx`（字节数与前者一致）以及真实商品图 `01_商品主图_云苔绿.png`、`01_牛仔裤平铺主图_1.png`。截图使用后者，页面才呈现真实商品图；已通过实拍确认（换前"暂无商品图"，换后出现 3 张真实图）。



为把首页手机档 LCP 从 5558ms 压进预算，对上述项目自有 JPEG 做了格式与尺寸派生。**原图一律保留**，`<picture>` 只把它们排在 AVIF/WebP 之后当兜底。

| 派生文件 | 源 | 处理 | 说明 |
| --- | --- | --- | --- |
| `product-*.{avif,webp}`（4 组） | 同名 `.jpg` | 缩到最长边 560px，AVIF q52 / WebP q76 | 卡片实际显示宽度 ≤300px，2x 够用。657,168 → 45,720 字节 |
| `shot-*.{avif,webp}`（11 组） | 同名 `.jpg` | **不降分辨率**，AVIF q70 / WebP q85 | 见下方清晰度判据。924,350 → 548,273 字节 |

### 截图为什么定在 AVIF q70

这些截图是产品真实性的证据，里面是小号中文 UI 文字。先按 q58 出过一版，把 `shot-step1` 的文字区域放大 2 倍和原 JPEG 并排看，出现可见的振铃与发虚（"意任务""品库""材库"边缘糊、青色小字轻微溢色）。随后做了一版 q58 / q70 / q78 / q86 的质量阶梯对照，逐档放大比对：**q70 及以上在该放大倍率下已分辨不出差异**，故定 q70（`shot-step1` 20,725 字节，对原 40,558 字节小 49%）。

复现这个判据的方法：把 `shot-step1.jpg` 分别按 q58 / q70 / q78 / q86 存成 AVIF，各取同一块含小号中文 UI 文字的区域放大 2 倍并排比对，即可看出 q58 发虚、q70 起分辨不出差异。

### 故意没做的事

`demo-tour-poster.jpg` **不换格式**。它是 `<video poster="...">` 属性，包不进 `<picture>`，也就没有回退链；一旦某个浏览器不认这个格式，用户看到的是黑帧。为省约 30KB 去冒这个风险不值得。

