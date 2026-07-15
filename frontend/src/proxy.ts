import { NextResponse } from "next/server";

// Thin shell only — renamed from middleware.ts per Next.js 16 (middleware → proxy). The session JWT
// lives in localStorage (client-side), which proxy cannot read, so real auth gating happens in
// app/(app)/layout.tsx. A true server-side gate arrives with #M2-AUTH-COOKIE (httpOnly cookie); at
// that point this is where we redirect unauthenticated requests away from (app) routes.
// Note: the proxy runtime is nodejs only (edge is not supported); this no-op shell is runtime-agnostic.
export function proxy() {
  return NextResponse.next();
}

export const config = {
  // Run on app routes, excluding Next internals and static assets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"]
};
