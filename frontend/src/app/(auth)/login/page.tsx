"use client";

import { type FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Logo } from "@/components/ui/logo";

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
      await login({ tenantSlug: tenantSlug.trim(), email: email.trim(), password });
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "登录失败，请重试。");
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
        <CardTitle className="text-center">登录控制台</CardTitle>
        <CardSubtitle className="mb-6 mt-1 text-center">输入租户与账号以继续</CardSubtitle>

        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div>
            <label htmlFor="tenant-slug" className={labelClass}>
              租户标识 (tenant slug)
            </label>
            <Input
              id="tenant-slug"
              name="tenant-slug"
              autoComplete="organization"
              placeholder="huading"
              value={tenantSlug}
              onChange={(e) => setTenantSlug(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="email" className={labelClass}>
              邮箱
            </label>
            <Input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="password" className={labelClass}>
              密码
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
            <p
              role="alert"
              className="rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg"
            >
              {error}
            </p>
          )}

          <Button type="submit" variant="primary" size="lg" disabled={disabled} className="mt-1 w-full">
            {submitting ? "登录中…" : "登录"}
          </Button>
        </form>
      </Card>
    </main>
  );
}
