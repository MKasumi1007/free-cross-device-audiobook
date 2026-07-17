import { doc, serverTimestamp, setDoc } from "firebase/firestore";

import { getFirebaseServices } from "./firebase";
import { recordEstimatedUsage } from "./usage";

const DEVICE_KEY = "audiobook-device-id";

export function getDeviceId(): string {
  const existing = localStorage.getItem(DEVICE_KEY);
  if (existing) return existing;
  const created = crypto.randomUUID();
  localStorage.setItem(DEVICE_KEY, created);
  return created;
}

function platformName(): string {
  if (/iPhone|iPad/i.test(navigator.userAgent)) return "iPhone / iPad";
  if (/Mac/i.test(navigator.platform)) return "Mac";
  return "Web browser";
}

export async function registerCurrentDevice(ownerUid: string): Promise<void> {
  const services = getFirebaseServices();
  if (!services) return;
  const deviceId = getDeviceId();
  await setDoc(doc(services.db, `users/${ownerUid}/devices/${deviceId}`), {
    owner_uid: ownerUid,
    device_id: deviceId,
    name: platformName(),
    platform: navigator.userAgent.slice(0, 180),
    push_capability: "NONE",
    last_seen_at: serverTimestamp(),
    updated_at: serverTimestamp(),
  }, { merge: true });
  recordEstimatedUsage({ writes: 1 });
}
