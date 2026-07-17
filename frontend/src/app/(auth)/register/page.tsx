"use client";

import { type FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-context";
import { markJustRegistered } from "@/lib/contact/welcome-flag";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Logo } from "@/components/ui/logo";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const hintClass = "mt-1.5 text-[12px] text-ink-faint";

// 对齐 BE TenantRegisterRequest：tenant_slug ^[a-z0-9][a-z0-9-]*$ 且 2–80；password ≥8；email 含 @。
const SLUG_RE = /^[a-z0-9][a-z0-9-]*$/;

/** 服务端错误 → friendly 中文（不泄露原始异常串）。 */
function friendlyRegisterError(err: unknown): string {
  if (err instanceof ApiError && (err.code === "tenant_slug_taken" || err.status === 409)) {
    return copy.auth.errSlugTaken;
  }
  return copy.auth.errRegisterFailed;
}

export default function RegisterPage() {
  const { session, ready, register } = useAuth();
  const router = useRouter();

  const [slug, setSlug] = useState("");
  const [teamName, setTeamName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (ready && session) router.replace("/");
  }, [ready, session, router]);

  // 提交前 friendly 校验（对齐 BE TenantRegisterRequest 上下界，避免无谓往返 + 明确指引）。
  const clientError = (): string | null => {
    const s = slug.trim();
    if (s.length < 2 || s.length > 80 || !SLUG_RE.test(s)) return copy.auth.errUsername;
    const team = teamName.trim();
    if (!team || team.length > 200) return copy.auth.errTeamName;
    const mail = email.trim();
    if (!mail.includes("@") || mail.length < 3 || mail.length > 320) return copy.auth.errEmail;
    if (password.length < 8 || password.length > 128) return copy.auth.errPassword;
    // full_name 选填：空则跳过/省略键；非空则 ≤200（对齐 BE，超长中文提交前拦截、不调 register）。
    if (fullName.trim().length > 200) return copy.auth.errFullName;
    return null;
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const invalid = clientError();
    if (invalid) {
      setError(invalid);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await register({
        tenantSlug: slug.trim(),
        tenantName: teamName.trim(),
        email: email.trim(),
        password,
        fullName: fullName.trim() || undefined
      });
      // LANDING-CONTACT-UI-0001：落标记 → 工作台首屏显示「联系开通额度」欢迎横幅。
      // 跳转行为不动（#155：landToken → 直接进控制台）—— 提示由控制台侧读标记显示，不在这里拦。
      markJustRegistered();
      router.replace("/"); // 后端随注册发 token → 直接进控制台
    } catch (err) {
      setError(friendlyRegisterError(err));
    } finally {
      setSubmitting(false);
    }
  };

  const disabled = submitting || !slug.trim() || !teamName.trim() || !email.trim() || !password;

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-[420px]" animateIn>
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <CardTitle className="text-center">{copy.auth.registerTitle}</CardTitle>
        <CardSubtitle className="mb-6 mt-1 text-center">{copy.auth.registerSubtitle}</CardSubtitle>

        <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
          <div>
            <label htmlFor="reg-slug" className={labelClass}>
              {copy.auth.usernameLabel}
            </label>
            <Input
              id="reg-slug"
              name="reg-slug"
              autoComplete="username"
              placeholder={copy.auth.usernamePlaceholder}
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
            />
            <p className={hintClass}>{copy.auth.usernameHint}</p>
          </div>
          <div>
            <label htmlFor="reg-team" className={labelClass}>
              {copy.auth.teamNameLabel}
            </label>
            <Input
              id="reg-team"
              name="reg-team"
              autoComplete="organization"
              placeholder={copy.auth.teamNamePlaceholder}
              value={teamName}
              onChange={(e) => setTeamName(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="reg-email" className={labelClass}>
              {copy.auth.emailLabel}
            </label>
            <Input
              id="reg-email"
              name="reg-email"
              type="email"
              autoComplete="email"
              placeholder={copy.auth.emailPlaceholder}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="reg-password" className={labelClass}>
              {copy.auth.passwordLabel}
            </label>
            <Input
              id="reg-password"
              name="reg-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p className={hintClass}>{copy.auth.passwordHint}</p>
          </div>
          <div>
            <label htmlFor="reg-fullname" className={labelClass}>
              {copy.auth.fullNameLabel}
            </label>
            <Input
              id="reg-fullname"
              name="reg-fullname"
              autoComplete="name"
              maxLength={200}
              placeholder={copy.auth.fullNamePlaceholder}
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
            />
          </div>

          {error && (
            <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
              {error}
            </p>
          )}

          <Button type="submit" variant="primary" size="lg" disabled={disabled} className="mt-1 w-full">
            {submitting ? copy.auth.registerSubmitting : copy.auth.registerSubmit}
          </Button>
        </form>

        {/* 注册 ↔ 登录互链 */}
        <p className="mt-5 text-center text-[13px] text-ink-soft">
          {copy.auth.registerHasAccount}{" "}
          <Link
            href="/login"
            className="rounded-pill font-medium text-gold-deep outline-none hover:underline focus-visible:shadow-focus-gold"
          >
            {copy.auth.registerToLogin}
          </Link>
        </p>
      </Card>
    </main>
  );
}
