# 0.4.0 验收矩阵

更新日期：2026-07-30。状态只描述已有证据，不用“代码存在”替代真实用户流程。

## 证据等级

- **已编码**：实现存在，尚不能推出可用。
- **单元测试**：隔离测试通过，可能使用 fake/mock。
- **自动化测试**：Emulator、Playwright 或构建流水线通过。
- **真实 Mac**：在 Apple Silicon Mac、正式 Application Support 运行目录和真实依赖上执行。
- **真实 iPhone**：用户在物理 iPhone Safari/PWA 上执行。
- **尚未完成**：没有足够证据，或仍有阻断。

## 阶段结果

| 阶段 | 修改/修复 | 测试与证据 | 当前结论 | 仍需用户 |
| --- | --- | --- | --- | --- |
| 0 真实审计 | 代码/依赖/路径/远端/Rules/文档逐项核查 | 基线 Web 20、Rules 24、Python 67；真实 Agent 日志和 GitHub 状态 | 审计完成，见 `AUDIT_REPORT.md` | 无 |
| 1 可复现环境 | 两套 uv 锁；托管 Python 3.12；正式模型目录；MPS 自检 | 真实模型加载；真实中文 WAV；真实 AAC M4A；依赖导入诊断 | 已完成真实 Mac 验收 | 无 |
| 2 正式安装器 | 双击 app；安装/更新/修复/卸载；LaunchAgent；回滚；FFmpeg fallback | 首次完整安装、覆盖更新、PID 重启、登录项、删源码重启；卸载保留自动化 | 已完成真实 Mac 验收；下载后 Gatekeeper 需右键打开一次 | 仅首次 Gatekeeper 确认 |
| 3 语音链路 | Qwen stdout 协议隔离；分层错误；长文本分片；私密规划 | 真实已确认声音生成 3.68 秒 WAV/M4A；152 字两调用合并为 33.44 秒 WAV；真实后台 chunk 完成 | 本地生成、编码和私密云端发布已真实验证 | 若要更换声音，需用户提供/选择真实录音 |
| 4 Web/Agent | Blob 试听加载；Origin 错误码；移动登录；系统状态与自动修复 | Web 单元测试；真实 loopback health/diagnostics；真实 iPhone 登录 | Web 与 Agent 主要链路已验收 | 无 |
| 5 Firebase | 按书查询；Worker 最小读权限；私密分片/哈希/删除；私密正文空闲刷新 | 25 个 Rules Emulator 测试；Rules 真实部署；真实音频与正文由 owner iPhone 回读 | 私密上传、权限和所有者回读已真实验证；删除待验收 | Google 登录若过期需用户操作 |
| 6 真实验收 | 正式 Agent 接管任务；Qwen → FFmpeg → Firestore；iPhone 播放与恢复 | 真实私密 M4A READY；iPhone 用户声音、段落高亮、后台、锁屏控制、位置恢复 | Mac 到 iPhone 私密播放闭环已完成；删除/重生成和中断场景仍未完成 | 来电/耳机、网络切换、PWA 安装需真机操作 |
| 7 发布与文档 | README、用户/排障/维护/审计/真机报告；CI 与 Pages | `ba379ed` CI/Pages 成功；后续文档提交再次执行流水线 | 发布完成并持续更新 | 无 |
| 8 生成可见性 | 约 40 字本地断点；阶段、进度、ETA；已生成音频和旧暂停整理 | 中断恢复单元测试；Rules 26；正式 Agent 0.4.0；真实任务上报 11/86 单元、3/24 段和 ETA | 小段断点与实时云端进度已真实运行；完整 10 分钟块仍继续生成 | 无 |

## 已完成的真实 Mac 证据

- macOS Apple Silicon，正式目录使用独立 `agent-runtime` 与 `qwen-runtime`。
- Qwen3-TTS 0.6B Base 从正式模型缓存加载，MPS 可用。
- 安装器自检生成有效 24 kHz 单声道 WAV，并由 FFmpeg 编码、完整解码和校验 AAC M4A。
- 在刻意移除 Homebrew 路径后，安装器真实下载并双重校验 `imageio-ffmpeg` 0.6.0 / FFmpeg 7.1 fallback；该正式二进制随后完成 MPS 模型自检，生成 3.072 秒、26905 字节 AAC M4A。
- 已确认声音通过相同 Qwen IPC 生成 3.68 秒 WAV，随后得到有效 3.68 秒 AAC M4A。
- 152 字输入被拆成 2 次真实模型调用，输出单个 33.44 秒 WAV。
- 一个真实后台任务完成 Qwen → WAV → FFmpeg → 私密 Firestore：15.44 秒 AAC M4A、135064 字节，音频和时间线各 1 个分片，分片索引与 SHA-256 校验通过，任务与音频元数据均为 READY。
- 覆盖更新成功替换服务；LaunchAgent PID 改变，健康接口返回 0.2.0。
- 临时移走整个 Git 仓库后强制重启服务，健康接口仍返回 0.2.0；安装包代码来自 Application Support，不来自源码。
- 诊断确认 Agent/Qwen 路径、torch、qwen-tts、soundfile、FFmpeg、模型、MPS、内存、磁盘、Firebase 和配对状态。
- 0.4.0 正式安装后，独立 Qwen 进程真实启动；任务从 MODEL_LOADING 进入 GENERATING，云端上报 11/86 个小单元、3/24 个文字段、当前 1/6 小段、约 65 秒已生成声音和动态 ETA。
- 本机已经产生新的 `.parts/checkpoint.json` 和小段 WAV；进度字段不包含书籍正文或声音样本。

真实测试音频、声音、书籍、token、日志和本机路径均未加入 Git。

## 已完成的真实 iPhone 证据

- iPhone Safari 正常打开 GitHub Pages，书架、阅读器和底部播放器适配移动屏幕。
- 未登录时“登录同步”按钮可见；Google 登录成功后显示头像与账号书架。
- owner-only 私密书籍正文和 READY 音频可回读，用户确认播放为自己的声音。
- 播放时正文当前段落高亮；暂停和恢复正常。
- 锁屏显示书名、作者、封面与进度，锁屏播放、暂停和跳转按钮正常。
- Safari 进入后台后继续播放，返回 Safari 后状态正常。
- 离开后返回可恢复保存位置；因 5 秒自动保存允许少量回退。
- 截图发现的 `Section0001` 标题和“首段无音频但本章后段可播”问题已修复并部署；后者仍需刷新网页后的真机复验。

## 自动化证据

2026-07-30 的 0.4.0 记录：

```text
免费能力审计：通过
秘密扫描：通过
TypeScript typecheck：通过
Web 单元测试：25/25
Firestore Rules Emulator：26/26
Python：100/100（另有 1 个上游弃用警告）
Playwright desktop/mobile：8 个通过，2 个可选公开资产测试跳过
Vite/PWA 生产构建：通过
Ruff：通过；mypy strict：39 个源文件通过
GitHub CI：成功
GitHub Pages：0.4.0 / 806ec32 已上线
```

2026-07-18 发布候选代码的最终记录：

```text
免费能力审计：通过
秘密扫描：通过
TypeScript typecheck：通过
Web 单元测试：22/22
Firestore Rules Emulator：25/25
Python：87/87（另有 1 个上游弃用警告）
Playwright desktop/mobile：8 个通过，2 个在可选公开 Release 资产不存在时跳过
Vite/PWA 生产构建：通过
Ruff：通过；mypy strict：37 个源文件通过
Shell 语法、git diff --check：通过
安装器 zip、SHA-256 与包内必需文件：通过
```

当前新增回归覆盖包括：安装/卸载保留、LaunchAgent 重试、诊断与 Origin、Qwen stderr/IPC、长文本分片、私密规划、Firestore 脱敏错误、Worker 最小读取和失败状态租约围栏。

## 尚未完成，不能宣称已验证

- 真实新用户从 GitHub Release 下载后完整走过 Gatekeeper 与首次安装。
- 真实删除后从保留 cursor 重新生成。
- iPhone 刷新最新 Pages 后复验章节首个可播放段修复。
- 真实 iPhone 添加主屏幕 PWA、倍速、定时、书签、来电/耳机中断和网络切换。
- Mac 物理重启；当前只完成 LaunchAgent bootout/bootstrap/kickstart 和源码移除后的进程重启。

这些项目没有被 Pixel 尺寸模拟、mock audio 或单元测试替代。
