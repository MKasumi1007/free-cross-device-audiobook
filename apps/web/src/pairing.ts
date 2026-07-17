import {
  Timestamp,
  collection,
  doc,
  onSnapshot,
  query,
  runTransaction,
  serverTimestamp,
  updateDoc,
  where,
  type Unsubscribe,
} from "firebase/firestore";

import { getFirebaseServices } from "./firebase";
import { classifyFirebaseError, type SyncError } from "./firebase-errors";
import { recordEstimatedUsage } from "./usage";

export interface WorkerLink {
  worker_uid: string;
  owner_uid: string;
  worker_type: "MAC_AGENT";
  scopes: string[];
  revoked_at: unknown;
  last_seen_at: unknown;
}

export class PairingError extends Error {
  constructor(public readonly code: "BAD_CODE" | "EXPIRED" | "RATE_LIMITED") {
    super(code === "RATE_LIMITED" ? "尝试次数过多，请十分钟后重新配对。" : "配对码无效或已过期。")
  }
}

async function hashCode(code: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(code));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function recordPairingAttempt(ownerUid: string, pairingHash: string): Promise<void> {
  const services = getFirebaseServices();
  if (!services) return;
  await runTransaction(services.db, async (transaction) => {
    const reference = doc(services.db, `pairingAttempts/${ownerUid}`);
    const snapshot = await transaction.get(reference);
    const previous = snapshot.exists() ? snapshot.data() : undefined;
    const updatedAt = previous?.updated_at as Timestamp | undefined;
    const windowExpired = Boolean(updatedAt && updatedAt.toMillis() <= Date.now() - 10 * 60_000);
    const current = previous && !windowExpired ? Number(previous.attempt_count || 0) : 0;
    if (current >= 5) throw new PairingError("RATE_LIMITED");
    transaction.set(reference, {
      owner_uid: ownerUid,
      attempt_count: current + 1,
      last_pairing_hash: pairingHash,
      updated_at: serverTimestamp(),
    });
  });
  recordEstimatedUsage({ reads: 1, writes: 1 });
}

export async function pairMacAgent(ownerUid: string, code: string): Promise<string> {
  if (!/^\d{6}$/.test(code)) throw new PairingError("BAD_CODE");
  const services = getFirebaseServices();
  if (!services) throw new Error("Firebase 尚未配置。");
  const pairingHash = await hashCode(code);
  await recordPairingAttempt(ownerUid, pairingHash);
  try {
    const workerUid = await runTransaction(services.db, async (transaction) => {
      const attemptReference = doc(services.db, `pairingAttempts/${ownerUid}`);
      const pairingReference = doc(services.db, `pairingRequests/${pairingHash}`);
      const [attemptSnapshot, pairingSnapshot] = await Promise.all([
        transaction.get(attemptReference),
        transaction.get(pairingReference),
      ]);
      const attempts = attemptSnapshot.exists() ? Number(attemptSnapshot.data().attempt_count || 0) : 0;
      if (attempts < 1 || attempts > 5) throw new PairingError("RATE_LIMITED");
      if (!pairingSnapshot.exists()) throw new PairingError("BAD_CODE");
      const pairing = pairingSnapshot.data();
      const expiresAt = pairing.expires_at as Timestamp;
      if (!expiresAt || expiresAt.toMillis() <= Date.now() || pairing.used_at) {
        throw new PairingError("EXPIRED");
      }
      const workerUid = String(pairing.worker_uid || "");
      if (!workerUid) throw new PairingError("BAD_CODE");

      transaction.update(pairingReference, {
        owner_uid: ownerUid,
        used_at: serverTimestamp(),
        attempt_count: Number(pairing.attempt_count || 0) + 1,
      });
      transaction.set(doc(services.db, `workerLinks/${workerUid}`), {
        worker_uid: workerUid,
        owner_uid: ownerUid,
        worker_type: "MAC_AGENT",
        scopes: ["generation"],
        pairing_hash: pairingHash,
        revoked_at: null,
        created_at: serverTimestamp(),
        updated_at: serverTimestamp(),
        last_seen_at: null,
      });
      return workerUid;
    });
    recordEstimatedUsage({ reads: 2, writes: 2 });
    return workerUid;
  } catch (error) {
    throw error instanceof PairingError ? error : new PairingError("BAD_CODE");
  }
}

export function watchMacAgents(
  ownerUid: string,
  onLinks: (links: WorkerLink[]) => void,
  onError: (error: SyncError) => void,
): Unsubscribe {
  const services = getFirebaseServices();
  if (!services) return () => undefined;
  const links = query(collection(services.db, "workerLinks"), where("owner_uid", "==", ownerUid));
  return onSnapshot(links, (snapshot) => {
    recordEstimatedUsage({ reads: snapshot.size });
    onLinks(snapshot.docs.map((item) => item.data() as WorkerLink));
  }, (error) => onError(classifyFirebaseError(error)));
}

export async function revokeMacAgent(ownerUid: string, workerUid: string): Promise<void> {
  const services = getFirebaseServices();
  if (!services) throw new Error("Firebase 尚未配置。");
  await updateDoc(doc(services.db, `workerLinks/${workerUid}`), {
    owner_uid: ownerUid,
    revoked_at: serverTimestamp(),
    updated_at: serverTimestamp(),
  });
  recordEstimatedUsage({ writes: 1 });
}
