"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth/auth-context";

// Client-side auth gate: the JWT lives in localStorage, so the gate must run in
// the browser (Next middleware can't read it). True server-side gating arrives
// with #M2-AUTH-COOKIE. Renders an aria-busy placeholder until the session is
// known, so we never flash the console or fire authed requests while logged out.
export default function AppLayout({ children }: { children: ReactNode }) {
  const { session, ready } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (ready && !session) router.replace("/login");
  }, [ready, session, router]);

  if (!ready || !session) return <main className="min-h-screen" aria-busy="true" />;
  return <>{children}</>;
}
