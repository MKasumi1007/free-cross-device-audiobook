# 故障排查

优先打开网页“系统状态”。它区分正常、警告和失败，并显示可执行建议；可修复项会出现“自动修复”。

## 日志

私有结构化日志：

```text
~/Library/Application Support/听见书页/logs/diagnostics.jsonl
```

其他日志：

- `agent.log` / `agent-error.log`：Web 服务与 LaunchAgent；
- `qwen-stderr.log`：模型横幅、下载进度和第三方 stderr；
- `install-*.log`：每次安装/更新；
- `repair.log`：网页触发的自动修复。

这些文件可能含本机路径、任务 ID 和异常堆栈，不要直接提交到公开 issue。网页只显示脱敏摘要。

## 常见错误

| 错误/现象 | 含义 | 处理 |
| --- | --- | --- |
| `AGENT_UNREACHABLE` | 17832 端口不可达 | 双击安装器或运行 LaunchAgent 自动修复 |
| `ORIGIN_MISMATCH` | 当前网页不在允许列表 | 只使用正式 Pages 或本地开发地址，不关闭 Origin 保护 |
| Agent 版本不同 | 网页与本机更新不同步 | 双击更新 app；完成后刷新 PWA |
| `FIREBASE_CONFIG_MISSING` | 公开客户端配置缺失 | 修复 Agent 或重新部署 Web 配置 |
| `FIREBASE_LOGIN_FAILED` | 登录/刷新失败 | 重新完成 Google 登录；不要粘贴 token 到日志 |
| `FIRESTORE_PERMISSION_DENIED` | Rules、配对或任务状态不允许 | 检查 Mac 绑定、撤销状态和私有日志中的 operation/status |
| `QWEN_ENV_MISSING` / `QWEN_DEPENDENCY_MISSING` | 独立 Qwen 环境损坏 | 系统状态 → 自动修复 Qwen |
| `MODEL_NOT_FOUND` / `MODEL_LOAD_FAILED` | 模型缺失或加载失败 | 自动修复模型；确认网络、磁盘和 MPS |
| `MPS_UNAVAILABLE` | Apple GPU 不可用 | 确认 Apple Silicon 和锁定 torch；CPU 降级很慢 |
| `MEMORY_PRESSURE` | 可用内存低于保护值 | 关闭大型应用；任务保留检查点后再继续 |
| `FFMPEG_MISSING` | 音频工具缺失 | 重新运行安装器；无需手工安装 Homebrew |
| `FFMPEG_ENCODING_FAILED` | M4A 编码失败 | 查看私有日志中的退出码/stderr；WAV 检查点保留 |
| `REFERENCE_TEXT_REQUIRED` / `REFERENCE_AUDIO_MISSING` | 声音配置不完整 | 重新选择录音并填写实际参考文字 |
| `TTS_WORKER_EXITED` | 模型子进程退出或协议失败 | 更新到 0.2.0；检查 qwen-stderr、内存和最近 traceback |
| `STALE_LEASE` | 任务已暂停、撤销、过期或被新尝试接管 | 旧结果不会发布；等待当前任务重新领取 |
| 免费额度保护暂停 | 应用接近保守阈值 | 等下一个 UTC 日；不要启用 Blaze |

## 安装器没有完成

1. 打开最新的 `install-*.log`。
2. 检查是否为非 Apple Silicon、磁盘小于 6 GiB、下载校验失败或网络中断。
3. 保留现有 `books`、`voices`、`generation`、`models`；不要为了重装删除整个数据目录。
4. 网络恢复后再次双击安装器。运行时使用 `.next/.previous`，失败不会把半套环境当成成功。

## 网页缓存不是最新

“系统状态”比较当前脚本 build ID 和 Pages 的 `version.json`。不一致时关闭已安装 PWA 后重开，或在 Safari/Chrome 刷新。发布者应先确认 Pages workflow 已完成再让用户清缓存。

## 报告问题时

提供版本、Mac 型号/内存、macOS、错误代码、操作步骤和日志中的脱敏 operation/HTTP status。不要提供书名/正文、声音、完整本机路径、refresh token、Authorization header、Cookie 或 Keychain 内容。
