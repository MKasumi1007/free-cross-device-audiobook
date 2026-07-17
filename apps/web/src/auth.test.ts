import { beforeEach, describe, expect, it, vi } from "vitest";

const authMocks = vi.hoisted(() => ({
  popup: vi.fn(),
  redirect: vi.fn(),
}));

vi.mock("firebase/auth", () => ({
  GoogleAuthProvider: class {
    setCustomParameters() {}
  },
  getRedirectResult: vi.fn(async () => null),
  onAuthStateChanged: vi.fn(() => () => undefined),
  signInWithPopup: authMocks.popup,
  signInWithRedirect: authMocks.redirect,
  signOut: vi.fn(async () => undefined),
}));

vi.mock("./firebase", () => ({
  getFirebaseServices: () => ({ auth: {} }),
}));

import { signInWithGoogle } from "./auth";

describe("Google 登录回退", () => {
  beforeEach(() => {
    authMocks.popup.mockReset();
    authMocks.redirect.mockReset();
  });

  it("桌面浏览器优先使用弹窗", async () => {
    authMocks.popup.mockResolvedValue(undefined);
    await signInWithGoogle();
    expect(authMocks.popup).toHaveBeenCalledOnce();
    expect(authMocks.redirect).not.toHaveBeenCalled();
  });

  it("弹窗被拦截时自动改用页面跳转", async () => {
    authMocks.popup.mockRejectedValue({ code: "auth/popup-blocked" });
    authMocks.redirect.mockResolvedValue(undefined);
    await signInWithGoogle();
    expect(authMocks.redirect).toHaveBeenCalledOnce();
  });
});
