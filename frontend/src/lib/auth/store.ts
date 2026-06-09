// Framework-agnostic auth store: holds the session (token + tenant + user) and
// mirrors it to localStorage so a refresh keeps the user signed in. The fetch
// client reads from here (so it doesn't depend on React), and React subscribes
// via the AuthProvider.
//
// Security note: the backend returns the JWT in the response body, so it must
// live in JS-reachable storage. localStorage is the pragmatic SPA choice;
// moving to an httpOnly, SameSite cookie (set by the backend) is the M3
// hardening to remove XSS token theft.

import type { CurrentUserResponse, Role } from "@/lib/api/types";

export interface Session {
  token: string;
  tenantId: string;
  userId: string;
  role: Role;
  user?: CurrentUserResponse;
}

const STORAGE_KEY = "huading.session";

let session: Session | null = null;
const listeners = new Set<() => void>();

function notify() {
  for (const listener of listeners) listener();
}

function persist() {
  if (typeof window === "undefined") return;
  if (session) {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } else {
    window.localStorage.removeItem(STORAGE_KEY);
  }
}

export const authStore = {
  hydrate(): Session | null {
    if (typeof window === "undefined") return null;
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) {
      try {
        session = JSON.parse(raw) as Session;
      } catch {
        session = null;
      }
    }
    return session;
  },
  get(): Session | null {
    return session;
  },
  set(next: Session) {
    session = next;
    persist();
    notify();
  },
  update(patch: Partial<Session>) {
    if (!session) return;
    session = { ...session, ...patch };
    persist();
    notify();
  },
  clear() {
    session = null;
    persist();
    notify();
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }
};
