"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { fetchMe, login as apiLogin, type LoginInput } from "@/lib/api/auth";
import { authStore, type Session } from "@/lib/auth/store";

interface AuthContextValue {
  session: Session | null;
  /** True once localStorage has been read (avoids redirect flicker). */
  ready: boolean;
  login: (input: LoginInput) => Promise<void>;
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

  const login = useCallback(async (input: LoginInput) => {
    const token = await apiLogin(input);
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

  const logout = useCallback(() => authStore.clear(), []);

  return (
    <AuthContext.Provider value={{ session, ready, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
