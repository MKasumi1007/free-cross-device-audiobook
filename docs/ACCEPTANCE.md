# 0.2.0 验收矩阵

更新日期：2026-07-18。状态只描述已有证据，不用“代码存在”替代真实用户流程。

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
| 4 Web/Agent | Blob 试听加载；Origin 错误码；系统状态与自动修复 | Web 单元测试、真实 loopback health/diagnostics | 已编码并完成部分真实 Mac 验收 | 无 |
| 5 Firebase | 按书查询；Worker 书籍元数据最小读权限；私密分片/哈希/删除 | 25 个 Rules Emulator 测试；Rules 真实部署；真实音频 1 分片、时间线 1 分片、SHA-256 与 READY 元数据 | 权限修复与私密 M4A 上传已真实验证；所有者网页回读/删除待验收 | Google 登录若过期需用户操作 |
| 6 真实验收 | 正式 Agent 接管现有任务，真实 Qwen → FFmpeg → Firestore | 15.44 秒、135064 字节 AAC M4A 成为 `PRIVATE_FIRESTORE/READY`；Mac 更新后任务安全重试 | Mac 到私密云端闭环已完成；网页播放、删除/重生成和真机仍未完成 | Google 登录、真实 iPhone，必要时麦克风/文件授权 |
| 7 发布与文档 | README、用户/排障/维护/审计文档；0.2.0 workflow | 发布前全套测试、Actions、Pages 待最终记录 | 进行中 | 无 |

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

真实测试音频、声音、书籍、token、日志和本机路径均未加入 Git。

## 自动化证据

2026-07-18 发布候选代码的最终记录：

```text
免费能力审计：通过
秘密扫描：278 个文本文件，通过
TypeScript typecheck：通过
Web 单元测试：20/20
Firestore Rules Emulator：25/25
Python：81/81（另有 1 个上游弃用警告）
Playwright desktop/mobile：10/10，包括真实 GitHub Release 文本、时间线与远程音频播放
Vite/PWA 生产构建：通过
Ruff：通过；mypy strict：37 个源文件通过
Shell 语法、git diff --check：通过
安装器 zip、SHA-256 与包内必需文件：通过
```

当前新增回归覆盖包括：安装/卸载保留、LaunchAgent 重试、诊断与 Origin、Qwen stderr/IPC、长文本分片、私密规划、Firestore 脱敏错误、Worker 最小读取和失败状态租约围栏。

## 尚未完成，不能宣称已验证

- 真实新用户从 GitHub Release 下载后完整走过 Gatekeeper 与首次安装。
- 用户在当前版本网页上重新完成 Google 登录、配对、真实 TXT/EPUB 的 GUI 导入。
- 当前版本网页以所有者账号回读并实际播放上述 READY 私密音频。
- 真实删除后从保留 cursor 重新生成。
- 真实 iPhone Safari/PWA 播放、锁屏、来电/耳机中断、跨设备续听。
- Mac 物理重启；当前只完成 LaunchAgent bootout/bootstrap/kickstart 和源码移除后的进程重启。

这些项目没有被 Pixel 尺寸模拟、mock audio 或单元测试替代。
