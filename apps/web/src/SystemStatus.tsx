import type { User } from "firebase/auth";
import { useCallback, useEffect, useState } from "react";

import {
  getAgentDiagnostics,
  startAgentRepair,
  type AgentDiagnosticItem,
  type AgentDiagnostics,
} from "./agent";
import { checkFirestoreConnection, firebaseIsConfigured } from "./firebase";
import { workerIsOnline, type WorkerLink } from "./pairing";

type Status = "ok" | "warning" | "failed";

interface StatusItem extends AgentDiagnosticItem {}

interface Props {
  user: User | null;
  activeMac?: WorkerLink;
  canInspectLocalMac: boolean;
  onClose: () => void;
  onNotice: (message: string) => void;
}

interface DeployedVersion {
  version?: string;
  build_id?: string;
  built_at?: string;
}

function item(
  key: string,
  label: string,
  status: Status,
  detail: string,
  suggestion = "",
): StatusItem {
  return { key, label, status, detail, suggestion, repair_action: "" };
}

function statusName(status: Status): string {
  return status === "ok" ? "正常" : status === "warning" ? "警告" : "失败";
}

function workerStateStatus(state: string): Status {
  if (state === "ERROR") return "failed";
  if (state.startsWith("WAITING_") || state.startsWith("FAILED_") || state.endsWith("_RETRY")) {
    return "warning";
  }
  return "ok";
}

export function SystemStatus({ user, activeMac, canInspectLocalMac, onClose, onNotice }: Props) {
  const [items, setItems] = useState<StatusItem[]>([]);
  const [agent, setAgent] = useState<AgentDiagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [repairing, setRepairing] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    const next: StatusItem[] = [
      item("web_version", "当前网页版本", "ok", `${__APP_VERSION__} · ${__BUILD_ID__.slice(0, 12)}`),
    ];
    try {
      const versionUrl = new URL("version.json", document.baseURI);
      versionUrl.searchParams.set("t", String(Date.now()));
      const response = await fetch(versionUrl, { cache: "no-store" });
      const deployed = await response.json() as DeployedVersion;
      const current = deployed.build_id === __BUILD_ID__;
      next.push(item(
        "pages_deploy",
        "GitHub Pages 部署",
        current ? "ok" : "warning",
        current ? `当前已加载最新构建（${deployed.built_at || "时间未知"}）。` : "网页缓存版本与最新部署不一致。",
        current ? "" : "关闭已安装的 PWA 后重新打开，或刷新页面。",
      ));
    } catch {
      next.push(item("pages_deploy", "GitHub Pages 部署", "warning", "无法读取部署版本文件。", "检查网络后重试。"));
    }

    const configured = firebaseIsConfigured();
    next.push(item(
      "firebase_config_web",
      "Firebase 网页配置",
      configured ? "ok" : "failed",
      configured ? "公开客户端配置已载入。" : "Firebase 配置缺失。",
      configured ? "" : "重新部署网页公开配置。",
    ));
    next.push(item(
      "google_login",
      "Google 登录",
      user ? "ok" : "warning",
      user ? "已经登录。" : "尚未登录。",
      user ? "" : "点击页面右上角“登录同步”。",
    ));
    if (user) {
      try {
        await checkFirestoreConnection(user.uid);
        next.push(item("firestore", "Firestore 连接", "ok", "已通过账号权限读取 Firestore。"));
      } catch (error) {
        next.push(item(
          "firestore",
          "Firestore 连接",
          "failed",
          error instanceof Error ? error.message : "连接失败。",
          "检查网络和登录状态；不会自动升级到付费方案。",
        ));
      }
    } else {
      next.push(item("firestore", "Firestore 连接", "warning", "登录后才能执行账号权限检查。", "先登录 Google。"));
    }
    next.push(item(
      "paired_mac_web",
      "网页中的 Mac 配对",
      activeMac ? "ok" : "warning",
      activeMac ? "账号中存在有效的 Mac 绑定。" : "账号中没有有效的 Mac 绑定。",
      activeMac ? "" : "在 Mac 网页点击“连接这台 Mac”。",
    ));
    next.push(item(
      "mac_presence",
      "Mac Agent 在线",
      workerIsOnline(activeMac) ? "ok" : "warning",
      workerIsOnline(activeMac) ? "Mac 最近十分钟内报告在线。" : "没有收到最近的 Mac 在线心跳。",
      workerIsOnline(activeMac) ? "" : "在 Mac 打开系统状态并检查 LaunchAgent。",
    ));

    if (canInspectLocalMac) {
      try {
        const report = await getAgentDiagnostics();
        setAgent(report);
        next.push(
          item("agent_connection", "Agent 端口", "ok", `127.0.0.1:${report.agent_port} 可以访问。`),
          item("agent_version", "Agent 版本", report.agent_version === __APP_VERSION__ ? "ok" : "warning", report.agent_version, report.agent_version === __APP_VERSION__ ? "" : "运行网页中的自动更新。"),
          ...report.items,
          item("worker_state", "当前后台任务", workerStateStatus(report.worker.state), `${report.worker.state}${report.worker.error ? `：${report.worker.error}` : ""}`),
          item("recent_error", "最近一次真实错误", report.recent_error ? "warning" : "ok", report.recent_error ? `${report.recent_error.error_code} · ${report.recent_error.message}` : "没有记录到错误。", report.recent_error ? `完整日志：${report.log_path}` : ""),
          item("log_path", "本地日志位置", "ok", report.log_path),
        );
      } catch (error) {
        setAgent(null);
        next.push(item(
          "agent_connection",
          "Agent 端口",
          "failed",
          error instanceof Error ? error.message : "127.0.0.1:17832 无法访问。",
          "双击安装器，或检查 LaunchAgent 是否运行。",
        ));
      }
    } else {
      setAgent(null);
      next.push(item("agent_connection", "Mac 本地环境", "warning", "手机无法直接检查 Mac 的本地端口和运行时。", "请在 Mac 打开本页面执行完整诊断。"));
    }
    setItems(next);
    setLoading(false);
  }, [activeMac, canInspectLocalMac, user]);

  useEffect(() => { void refresh(); }, [refresh]);

  async function repair(action: string) {
    setRepairing(action);
    try {
      await startAgentRepair(action);
      onNotice("自动修复已开始。模型修复可能需要几分钟，完成后请重新检查。");
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "自动修复没有启动。请重新运行安装器。");
    } finally {
      setRepairing("");
    }
  }

  const failures = items.filter((entry) => entry.status === "failed").length;
  const warnings = items.filter((entry) => entry.status === "warning").length;

  return (
    <div className="status-backdrop" role="presentation">
      <section className="status-panel" role="dialog" aria-modal="true" aria-labelledby="status-title">
        <header className="status-heading">
          <div>
            <span className="modal-kicker">环境诊断</span>
            <h2 id="status-title">系统状态</h2>
            <p>{loading ? "正在检查真实环境..." : failures ? `${failures} 项失败，${warnings} 项警告。` : warnings ? `${warnings} 项需要留意。` : "所有已执行检查均正常。"}</p>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="关闭系统状态">×</button>
        </header>
        <div className="status-list" aria-live="polite">
          {items.map((entry) => (
            <article className={`status-row status-row--${entry.status}`} key={`${entry.key}-${entry.label}`}>
              <span className="status-mark">{entry.status === "ok" ? "✓" : entry.status === "warning" ? "!" : "×"}</span>
              <div>
                <b>{entry.label}</b>
                <small>{statusName(entry.status)}</small>
                <p>{entry.detail}</p>
                {entry.suggestion && <em>{entry.suggestion}</em>}
              </div>
              {entry.repair_action && canInspectLocalMac && (
                <button
                  className="quiet-button status-repair"
                  disabled={Boolean(repairing)}
                  onClick={() => void repair(entry.repair_action)}
                >
                  {repairing === entry.repair_action ? "修复中..." : "自动修复"}
                </button>
              )}
            </article>
          ))}
          {loading && <p className="loading">正在读取 Agent、Qwen、模型与磁盘状态...</p>}
        </div>
        <footer className="status-footer">
          <span>{agent ? `数据目录：${agent.data_root}` : "诊断不会上传本机路径、书籍或声音。"}</span>
          <button className="quiet-button" onClick={() => void refresh()} disabled={loading}>重新检查</button>
          <button className="primary-button" onClick={onClose}>完成</button>
        </footer>
      </section>
    </div>
  );
}
