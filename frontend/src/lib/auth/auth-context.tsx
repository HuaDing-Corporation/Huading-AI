"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import {
  fetchMe,
  login as apiLogin,
  registerTenant as apiRegister,
  type LoginInput,
  type RegisterInput
} from "@/lib/api/auth";
import type { TokenResponse } from "@/lib/api/types";
import { authStore, type Session } from "@/lib/auth/store";

interface AuthContextValue {
  session: Session | null;
  /** True once localStorage has been read (avoids redirect flicker). */
  ready: boolean;
  login: (input: LoginInput) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    authStore.hydrate();
    const current = authStore.get();
    setSession(current);
    setReady(true);
    const unsubscribe = authStore.subscribe(() => setSession(authStore.get()));
    // Entitlement 刷新（ADMIN-VIP-GATE-UI-0001-FIX1）：localStorage 里的 user/permissions 只是缓存，
    // 套餐/权限可能已被运营变更（如开通 huading）。已登录时 mount 拉最新 /me 覆盖 user → 刷新页面即拿到
    // 新的 voice_clone_vip，不永久吃旧 session。失败静默保留缓存（离线/瞬断不误登出，与 landToken 一致）。
    // 竞态守卫：只有活动会话仍是发起刷新时的同一账号才写回——防「刷新未落定时登出、改登另一账号」的旧 /me
    // 晚到把 A 的 user/权限并进 B 的会话（SPA 内根布局不卸载，in-flight 请求可跨重登录存活）。
    if (current?.token) {
      fetchMe()
        .then((me) => {
          if (authStore.get()?.userId === current.userId) authStore.update({ user: me });
        })
        .catch(() => {
          /* keep cached session */
        });
    }
    return unsubscribe;
  }, []);

  // 登录/注册共用同一「落 token → fetchMe 补全」路径：会话形状零分叉。
  const landToken = useCallback(async (token: TokenResponse) => {
    authStore.set({
      token: token.access_token,
      tenantId: token.tenant_id,
      userId: token.user_id,
      role: token.role
    });
    // Enrich with profile (name/permissions); non-fatal if it fails.
    try {
      const me = await fetchMe();
      authStore.update({ user: me });
    } catch {
      // keep the bare session
    }
  }, []);

  const login = useCallback(
    async (input: LoginInput) => {
      await landToken(await apiLogin(input));
    },
    [landToken]
  );

  // 注册成功 → 后端已随响应发 token → 直接落会话进控制台（AUTH-UI-0001）。
  const register = useCallback(
    async (input: RegisterInput) => {
      const res = await apiRegister(input);
      await landToken(res.token);
    },
    [landToken]
  );

  const logout = useCallback(() => authStore.clear(), []);

  return (
    <AuthContext.Provider value={{ session, ready, login, register, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
