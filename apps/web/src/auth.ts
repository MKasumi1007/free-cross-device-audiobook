import {
  GoogleAuthProvider,
  getRedirectResult,
  onAuthStateChanged,
  signInWithPopup,
  signInWithRedirect,
  signOut,
  type User,
} from "firebase/auth";

import { getFirebaseServices } from "./firebase";

export type AuthListener = (user: User | null) => void;

export function watchAuth(listener: AuthListener): () => void {
  const services = getFirebaseServices();
  if (!services) {
    listener(null);
    return () => undefined;
  }
  void getRedirectResult(services.auth).catch(() => undefined);
  return onAuthStateChanged(services.auth, listener);
}

export async function signInWithGoogle(): Promise<void> {
  const services = getFirebaseServices();
  if (!services) throw new Error("Firebase 尚未配置，当前只使用本机书架。");
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  try {
    await signInWithPopup(services.auth, provider);
  } catch (error) {
    const code = typeof error === "object" && error && "code" in error ? String(error.code) : "";
    const redirectCodes = new Set([
      "auth/popup-blocked",
      "auth/cancelled-popup-request",
      "auth/operation-not-supported-in-this-environment",
      "auth/web-storage-unsupported",
    ]);
    if (!redirectCodes.has(code)) throw error;
    await signInWithRedirect(services.auth, provider);
  }
}

export async function signOutCurrentUser(): Promise<void> {
  const services = getFirebaseServices();
  if (services) await signOut(services.auth);
}
