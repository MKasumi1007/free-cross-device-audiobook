# 0.2.0 真实审计报告

审计日期：2026-07-18
审计基线：`main` 的 `609a80d` 系列部署；修复分支以实际代码、运行时和远端状态重新核查。

## 结论

修复前的仓库不是普通用户可直接使用的产品。解析、网页、Rules、播放和任务代码较多，自动化测试也能通过，但正式安装器、可复现 Qwen 环境、模型落位、错误诊断和源码独立性都缺失。文档把“代码存在/测试通过”写成了“可运行、已部署、已完成”，证据等级不准确。

审计后直接进入修复，没有把 mock 测试当成真实验收。

## 修复前实际能运行到哪里

- Web、Rules、Python 基线分别通过 20、24、67 个测试；生产 Web 可以构建。
- GitHub Actions 的 CI 与 Pages 最近一次基线运行成功，Pages 地址可访问。
- 本机已有一套手工建立的旧 Qwen 环境，因此开发者机器可以运行短 TTS；仓库本身不能在新 Mac 上复现它。
- Agent 能从开发环境启动，但旧正式服务曾出现包缺失；语音试听的 `<audio src>` 请求不带允许的 Origin，真实 Agent 返回 403。
- Firebase 项目公开客户端配置存在，Rules 有隔离测试；真实 Worker 链路随后暴露了 Rules 与客户端查询不一致的问题。

## 一定会失败或高概率失败的环节

1. 没有真正安装器：README 要求手工建 `.venv`、装依赖并执行命令。
2. 主依赖没有 torch、soundfile、qwen-tts；`qwen_worker.py` 会直接导入它们。
3. 正式 Agent/Qwen 运行目录不是由仓库可复现地创建；删掉源码后无法保证继续运行。
4. 试听音频使用直接 URL，浏览器媒体请求缺少 Origin，真实 loopback Agent 拒绝。
5. Qwen 子进程 stderr 被丢弃；第三方 stdout 横幅会破坏 JSON-lines IPC。
6. 通用生成规划错误地拒绝 `LOCAL_ONLY` 私密书，导致私密任务在进入私密发布器前失败。
7. 五天清理使用 collection-group 查询，线上 Rules 返回 403。
8. 已配对 Worker 可以更新书籍正文指针，却不能读取同一书籍元数据，真实任务返回 403。
9. `launchd.py` 没有模块入口，安装脚本调用 `python -m` 时实际上不注册服务；健康检查可能误把旧服务当成新服务。
10. LaunchAgent 退出与重新 bootstrap 有竞态，真实覆盖更新出现过 launchctl I/O error 5。
11. 无 Homebrew 的全新 Mac 没有 FFmpeg 自动补齐路径。

## 硬编码与开发目录残留

修复前 `apps/mac-agent/src/mac_agent/preview.py` 包含一个指向开发机历史工作区 `venv-qwen3tts/bin/python` 的绝对路径。它已删除。正式运行现在只接受：

- `~/Library/Application Support/听见书页/agent-runtime/bin/python`
- `~/Library/Application Support/听见书页/qwen-runtime/bin/python`
- 安装器写入的 `AUDIOBOOK_DATA_ROOT`、`AUDIOBOOK_QWEN_PYTHON`、`HF_HOME`

仓库扫描保留的 `.venv` 文本只出现在开发命令和忽略规则中；正式代码、LaunchAgent 和安装包不依赖 `.venv`、Codex 工作区或仓库原位置。审计文档不记录开发机完整绝对路径，避免把本机信息重新写回 Git。

## 缺失依赖与安装断点

- Agent 与 Qwen 没有分离的锁文件。
- 没有 Python 版本管理、uv 校验、模型缓存目录和真实自检。
- 没有下载模型失败、MPS、内存、磁盘、FFmpeg 的可执行诊断。
- 没有可双击的安装/更新/卸载入口。
- 更新没有 `.next/current/.previous` 原子交换和失败回滚。
- 卸载边界未证明会保留书架、声音和模型。

## 被吞掉或失真的错误

- Qwen stderr 被送到 `/dev/null`。
- Worker、试听和私密发布多处宽泛捕获后只留下“稍后重试”。
- 子进程退出码、stderr、Python、模型、FFmpeg、内存和磁盘没有统一记录。
- Firebase REST 只报告 HTTP 数字，丢弃脱敏错误响应。
- 浏览器无法区分 Agent 未安装、端口不可达、Origin 拒绝和具体 Agent 错误码。

修复后仍允许在边界层捕获 `Exception`，但必须写入私有结构化日志并保留 traceback；网页只获得脱敏中文摘要。

## 文档与代码不一致

- “普通用户无需命令”与手工 `.venv` 安装相矛盾。
- “本地语音生成已完成”没有可复现 Qwen 依赖和安装后模型自检。
- “私密书籍已完成”与规划层拒绝 `LOCAL_ONLY` 相矛盾。
- “五天自动删除已完成”与线上 collection-group 403 相矛盾。
- “手机播放已完成”只有 Pixel 尺寸模拟和合成公共资产网络测试，没有真实 iPhone 证据。
- “可运行、已部署”混合了代码、自动化、旧 Pages 部署和真实设备验收。

这些表述已改成统一证据等级：已编码、已通过单元测试、已通过自动化测试、已完成真实 Mac 验收、已完成真实 iPhone 验收、尚未完成。

## GitHub 与 Pages 基线

- 仓库为公开仓库，Pages 基线地址存在。
- 基线 CI/Pages 最近运行成功；这只能证明当时提交的自动化和静态部署，不证明本机 TTS 或手机闭环。
- 新增安装包 workflow 只在 tag 或手工触发时打包，不把 Actions 当 TTS 后台。
- 0.2.0 必须在合并后重新确认 CI、Pages、`version.json` 和安装器 artifact。

## Firebase 配置与 Rules 风险

- 公共 Web 配置不是密钥；真正权限由 Authentication 和 Rules 决定。
- 项目保持 Spark，只使用 Authentication 与 Firestore；没有启用 Billing、Blaze、Storage、Functions 或 Hosting。
- Owner 隔离、匿名拒绝、Worker revocation、租约和私密资产分片有 Emulator 覆盖。
- 审计发现两项真实 403：collection-group 音频扫描、Worker 读取书籍元数据。前者改为按本机保留书籍逐本查询；后者用 `activeWorkerFor(ownerUid)` 最小授权并已部署。
- Rules 的复杂 `audioChunks` 更新分支在部分“预期拒绝”测试中触及 Emulator 1000 表达式上限。当前允许路径测试通过，但这是维护风险；后续应拆小状态转换规则，不能把现有测试数量等同形式化证明。
- Firestore 私密二进制方案受 Spark 读写次数和文档限制约束，700 MiB 是应用硬上限而非服务承诺；接近应用阈值必须暂停。

## 修复顺序

1. 建立 Agent/Qwen 锁文件、正式路径和真实模型自检。
2. 建立双击安装、LaunchAgent、更新回滚、卸载保留与源码独立性测试。
3. 建立结构化错误和环境诊断，先让真实失败可见。
4. 修复 Qwen IPC、私密规划、长段落、试听 Origin 和任务围栏。
5. 修复并部署最小 Rules 变更，完成私密上传/回读/删除真实链路。
6. 运行 Web/Rules/Python/Playwright/构建/安全全套测试。
7. 更新诚实文档，发布 0.2.0，确认 Actions/Pages；最后由用户完成真实 iPhone 和必要授权。
