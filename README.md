# 米兰读书 / Free Cross-Device Audiobook

一个在 Apple Silicon Mac 本地运行 Qwen3-TTS、通过网页在电脑和手机阅读与收听的开源项目。私密书籍、声音样本和生成中间文件不得进入 Git；未确认传播权的内容只能使用账号隔离的 Firestore 私密路径。

在线网页：<https://mkasumi1007.github.io/free-cross-device-audiobook/>

## 当前状态（0.4.0）

| 项目 | 证据状态 |
| --- | --- |
| EPUB/TXT 解析、网页、播放器、任务与删除逻辑 | 已编码；通过单元和自动化测试 |
| 跨书章节待生成列表 | Mac 与手机可选章节、排序、暂停、继续和移除；队列云端持久化 |
| 生成可见性与断点续作 | 网页显示当前书/章、阶段、小段、百分比和 ETA；约 40 字保存一个本地断点 |
| 双击安装、独立 Agent/Qwen 环境、模型、LaunchAgent | 已在真实 Apple Silicon Mac 安装和重载验证 |
| Qwen 模型加载、中文 WAV、FFmpeg M4A | 已在真实 Mac、MPS 和真实已确认声音上验证 |
| 私密 Firestore Rules | 26 个 Emulator 测试通过；必要修复已真实部署 |
| GitHub Pages 与 Google 登录/配对 | 真实 iPhone 登录、头像和账号书架同步通过 |
| 私密音频生成与 Firestore 上传 | 一个真实 Qwen chunk 已编码、分片、哈希校验并写为 READY |
| 所有者网页回读与播放 | 真实 iPhone 已回读私密正文与音频，并确认用户声音 |
| 真实 iPhone Safari、锁屏和续听 | 播放、后台、锁屏控制与位置恢复通过；中断/网络切换待测 |
| 删除/重生成闭环 | 仍需完成真实用户流程，不能标记为完成 |

完整证据、失败项和未完成项见 [验收矩阵](docs/ACCEPTANCE.md)；实时状态和小段断点设计见 [0.4.0 生成状态与断点续作](docs/2026-07-30_生成状态与断点续作.md)；此前真机过程见 [2026-07-18 实测报告](docs/2026-07-18_本轮开发与iPhone实测报告.md)。

## 普通用户安装

1. 从 [Releases](https://github.com/MKasumi1007/free-cross-device-audiobook/releases) 下载 `米兰读书安装器-0.4.0.zip`。
2. 解压后双击 `米兰读书安装器.app`。这是未购买 Apple Developer 证书的开源应用；如果 Gatekeeper 拦截，请在 Finder 中右键它、选择“打开”，再确认一次。
3. 首次安装会自动准备独立 Python、Agent、Qwen 依赖、FFmpeg、约 2.3 GiB 模型，并执行真实中文生成与 M4A 自检。需要网络、Apple Silicon Mac 和至少 6 GiB 可用磁盘；耗时取决于网速与机器。
4. 安装成功后会自动启动后台服务，并在 `~/Applications/米兰读书/` 放置网页、更新和卸载入口。
5. 打开网页的“系统状态”。所有本机项目正常后，再登录、连接 Mac、添加书和设置声音。

日常使用不需要 Node、Python、Homebrew 或终端。详细步骤见 [普通用户指南](docs/USER_GUIDE.md)。

## 安全边界

- Agent 只监听 `127.0.0.1:17832`，只接受明确允许的网页 Origin，并对写操作使用 CSRF token。
- 网页不能提交任意本机路径；书籍和声音只能通过 macOS 原生选择器选择。
- Agent 与 Qwen 使用两套锁定依赖的独立运行环境。
- Firebase 只使用 Authentication 和 Firestore Spark；代码禁止 Blaze、Billing、Storage、Functions、Hosting、Analytics 和付费 GPU。
- Qwen 只在本机有合格任务时加载；GitHub Actions 只做测试、构建和部署。
- `LOCAL_ONLY` 正文、时间线和音频只能进入所有者私密 Firestore 文档；声音样本不上传。
- 更新采用 `.next → current` 原子切换和 `.previous` 回滚；卸载保留 `books`、`voices`、`generation` 与 `models`。

正式本机目录（为保证已有书架、声音和进度不因品牌改名而丢失，继续使用原兼容目录名）：

```text
~/Library/Application Support/听见书页/
├── agent-runtime
├── qwen-runtime
├── tools
├── models
├── books
├── voices
├── generation
├── logs
└── state
```

## 开发

以下命令只面向维护者，普通用户不需要执行：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm install
npm test
npm run test:e2e
npm run build
```

运行环境依赖锁位于 `installer/requirements-agent.lock` 与 `installer/requirements-qwen.lock`。发布、维护和回滚流程见 [维护手册](docs/MAINTENANCE.md)，故障分层与日志位置见 [故障排查](docs/TROUBLESHOOTING.md)。

## 隐私与许可

不要提交真实 EPUB/TXT、解析正文、声音、生成音频、OAuth/Firebase token、Keychain 内容、模型缓存、日志或开发机绝对路径。第三方依赖和 FFmpeg 分发说明见 [LICENSES](docs/LICENSES.md)。本仓库目前未声明统一的项目许可证；这不等于自动授予再分发权。
