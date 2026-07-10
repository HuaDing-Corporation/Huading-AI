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
    setSession(authStore.get());
    setReady(true);
    return authStore.subscribe(() => setSession(authStore.get()));
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
