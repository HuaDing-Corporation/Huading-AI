"use client";

import { type FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-context";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Logo } from "@/components/ui/logo";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

export default function LoginPage() {
  const { session, ready, login } = useAuth();
  const router = useRouter();

  const [tenantSlug, setTenantSlug] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (ready && session) router.replace("/");
  }, [ready, session, router]);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      // 字段/逻辑不动（AUTH-UI-0001 只改显示文案）：仍 tenantSlug + email + password。
      await login({ tenantSlug: tenantSlug.trim(), email: email.trim(), password });
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : copy.auth.loginFailed);
    } finally {
      setSubmitting(false);
    }
  };

  const disabled = submitting || !tenantSlug.trim() || !email.trim() || !password;

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-[420px]" animateIn>
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        {/* 副标题「输入用户与账号以继续」已删（AUTH-UI-0001） */}
        <CardTitle className="mb-6 text-center">{copy.auth.loginTitle}</CardTitle>

        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div>
            <label htmlFor="tenant-slug" className={labelClass}>
              {copy.auth.usernameLabel}
            </label>
            <Input
              id="tenant-slug"
              name="tenant-slug"
              autoComplete="organization"
              placeholder={copy.auth.usernamePlaceholder}
              value={tenantSlug}
              onChange={(e) => setTenantSlug(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="email" className={labelClass}>
              {copy.auth.emailLabel}
            </label>
            <Input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              placeholder={copy.auth.emailPlaceholder}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="password" className={labelClass}>
              {copy.auth.passwordLabel}
            </label>
            <Input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {error && (
            <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
              {error}
            </p>
          )}

          <Button type="submit" variant="primary" size="lg" disabled={disabled} className="mt-1 w-full">
            {submitting ? copy.auth.loginSubmitting : copy.auth.loginSubmit}
          </Button>
        </form>

        {/* 登录 ↔ 注册互链 */}
        <p className="mt-5 text-center text-[13px] text-ink-soft">
          {copy.auth.loginNoAccount}{" "}
          <Link
            href="/register"
            className="rounded-pill font-medium text-gold-deep outline-none hover:underline focus-visible:shadow-focus-gold"
          >
            {copy.auth.loginToRegister}
          </Link>
        </p>
      </Card>
    </main>
  );
}
