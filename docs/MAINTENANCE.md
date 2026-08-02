# 开发与发布维护手册

## 本地开发

普通用户不需要这些命令。维护者使用 Python 3.12 和 Node 22+：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm install
npm test
npm run test:e2e
npm run build
```

额外门禁：

```bash
.venv/bin/ruff check .
.venv/bin/mypy services/audiobook-core/src apps/mac-agent/src
git diff --check
bash installer/build-installer.sh
shasum -a 256 -c dist/installer/*.sha256
```

## 依赖更新

- Agent 与 Qwen 依赖必须分开锁定。
- 修改 `.in` 后使用固定 uv 重新 compile，提交完整 hash lock。
- Qwen/torch 更新必须在 Apple Silicon MPS 上真实加载、生成 WAV、编码 M4A；不能只看 import 或 mock。
- 修改 uv 或 FFmpeg 下载版本时必须更新 wheel/二进制 SHA-256、编译参数许可审计和安装器测试。
- 禁止把本机环境、模型、wheel cache 或 `.venv` 复制进仓库。

## 安装器不变量

- 正式路径只在 `~/Library/Application Support/听见书页/`。
- `agent-runtime` 不包含 torch/Qwen；`qwen-runtime` 不承载 Web Agent。
- 新环境先建在 `.next`，验证后切换；旧环境暂存在 `.previous`。
- LaunchAgent 必须报告目标版本健康后才能删除 `.previous`。
- 卸载不得递归删除整个数据根，只能删除白名单运行组件。
- 更新后要做一次“临时移走源码并 kickstart”的独立性测试。

## Firebase

- `.firebaserc` 指向 Spark 项目；部署前再次确认没有 Billing/Blaze。
- Rules 先过 Emulator，再最小范围部署：`firebase deploy --only firestore:rules`。
- 每个新允许路径必须同时测试匿名、其他 owner、未配对 Worker 和已撤销 Worker。
- 不要为了修复查询 403 使用宽泛 collection-group read；优先把查询约束到已知 owner/book 路径。
- `audioChunks` 规则复杂度接近表达式上限，是已知维护风险；重构必须保留租约、删除代次和私密资产 READY 屏障。

## 发布新版本

1. 确认工作树只含目标修改，安全扫描没有本机路径或凭据。
2. 完成全套测试和真实 Mac 验收，更新 `ACCEPTANCE.md`。
3. 提交并推送分支，创建 PR，让 CI 全绿。
4. 合并到 `main` 后确认 Pages workflow 成功，并核对线上 `version.json` 的 build ID。
5. 创建与项目版本一致的 `vX.Y.Z` tag；installer workflow 产生 zip/checksum artifact。
6. 创建 GitHub Release，附上 zip、checksum、Gatekeeper 说明、支持硬件和未完成真机项。
7. 从 Release 下载一次，校验 checksum，并在不依赖仓库的目录执行安装。

## 回滚

- 安装器健康失败会自动恢复 `.previous`。
- Pages 可回滚到上一个已知提交，但不要用 destructive reset 覆盖用户工作。
- Rules 回滚必须使用已审核版本并重新跑 Emulator；Rules 与客户端查询必须同步。
- 不要删除 Firestore 私密资产、书架、声音或检查点来“让测试变绿”。

## 隐私检查

发布前必须确认 Git 里没有书籍、声音、M4A/WAV、模型、日志、token、cookie、Keychain 输出、开发机绝对路径或用户邮箱。测试 fixture 只能使用项目自制文本和合成音频，并记录来源/许可。
