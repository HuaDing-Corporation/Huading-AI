import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BeforeAfter } from "./before-after";
import type { AdminAuditRow } from "@/lib/api/admin-console";

// ADMIN-AUDIT-DIFF-NOISE-0001 组件承重（信息只折叠不删）：变化键默认可见（在 <details> 外）、未变键收进
// <details> 折叠区（在 <details> 内、summary 为确定标题）。jsdom 不实现 details 开合隐藏，故用**结构断言**
// （谁在 details 内/外）而非可见性。期望值手写常量。变异哨兵见回执：① changed 判定反向 → 分组错乱必红；
// ② 删折叠区（未变键不渲染）→ 未变键不在 DOM 必红（钉「信息不可丢」）；③ 单边键判未变 → 必红。
function row(before: AdminAuditRow["before"], after: AdminAuditRow["after"]): AdminAuditRow {
  return {
    id: "a1", actor_user_id: "u", actor_email: "q@h.test", actor_tenant_id: "ten-mock",
    action: "plan_change", target_tenant_id: "ten-acme", target_tenant_slug: "acme", target_id: null,
    before, after, reason: null, created_at: "2026-07-15T09:00:00Z"
  };
}
const UUID = "8a4edef5-4075-4dce-a031-8a6ece6618fc";

describe("BeforeAfter · 变化默认展示 / 未变折叠（信息只折叠不删）", () => {
  it("§一 套餐变更：默认只见 plan_code（在折叠区外），未变 5 项收进 <details>「另有 5 项未变化」", () => {
    const { container } = render(
      <BeforeAfter
        row={row(
          { plan_code: "free", subscription: { id: UUID, used: 110, total: 10000000, reserved: 0, remaining: 9999890 } },
          { plan_code: "huading", subscription: { id: UUID, used: 110, total: 10000000, reserved: 0, remaining: 9999890 } }
        )}
      />
    );
    const details = container.querySelector("details")!;
    expect(details).toBeTruthy();
    // 折叠标题确定文案。
    expect(details.querySelector("summary")!.textContent).toBe("另有 5 项未变化");
    // 变化键 plan_code 在折叠区**外**、含前→后确定值。
    expect(details.textContent).not.toContain("plan_code");
    expect(container.textContent).toContain("plan_code");
    expect(container.textContent).toContain("free");
    expect(container.textContent).toContain("huading");
    // 未变 5 项确实**仍在**（收进折叠区，信息不丢）——逐键点号路径 + 值可复核。
    for (const k of ["subscription.id", "subscription.used", "subscription.total", "subscription.reserved", "subscription.remaining"]) {
      expect(details.textContent).toContain(k);
    }
    expect(details.textContent).toContain("10,000,000"); // total 未变值仍可见（点开）
  });

  it("🔴 单边键（voice_slot 首次分配：只有 after）算「变化」、默认可见，无折叠区（无未变键）", () => {
    const { container } = render(
      <BeforeAfter row={row({ speaker_ids: [] }, { speaker_ids: ["S_acme_001"], speaker_id: "S_acme_001", changed: true })} />
    );
    expect(container.querySelector("details")).toBeNull(); // 全变化 → 不出现折叠区（不留「另有 0 项」）
    expect(container.textContent).toContain("speaker_id");
    expect(container.textContent).toContain("S_acme_001");
    expect(container.textContent).toContain("changed");
  });

  it("全部未变（credits 无实际变化）→ 折叠标题「本次无字段变化」，仍可展开逐键复核", () => {
    const same = { id: "sub-acme", total: 20000, used: 5000, reserved: 1000, remaining: 14000 };
    const { container } = render(<BeforeAfter row={row({ ...same }, { ...same })} />);
    const details = container.querySelector("details")!;
    expect(details).toBeTruthy();
    expect(details.querySelector("summary")!.textContent).toBe("本次无字段变化");
    expect(details.textContent).toContain("total"); // 未变字段仍在、可展开
    expect(details.textContent).toContain("20,000");
  });

  it("全部变化（credits total+remaining 变、id/used/reserved 未变）→ 变化在外、未变入折叠", () => {
    const { container } = render(
      <BeforeAfter
        row={row(
          { id: "sub-acme", total: 20000, used: 5000, reserved: 1000, remaining: 14000 },
          { id: "sub-acme", total: 25000, used: 5000, reserved: 1000, remaining: 19000 }
        )}
      />
    );
    const details = container.querySelector("details")!;
    expect(details.querySelector("summary")!.textContent).toBe("另有 3 项未变化");
    // 变化的 total/remaining 前→后在折叠区外。
    expect(details.textContent).not.toContain("25,000");
    expect(container.textContent).toContain("25,000");
    expect(container.textContent).toContain("19,000");
  });

  it("长 UUID 变化 → 中截显示 + 全值仍可获取（sr-only 全值，不只靠 title）", () => {
    const other = "1b2c3d4e-0000-1111-2222-333344445555";
    const { container } = render(<BeforeAfter row={row({ target: UUID }, { target: other })} />);
    // target 变化 → 在折叠区外；可视是截断串，但**全值**在 sr-only（读屏可得）。
    expect(container.querySelector("details")).toBeNull();
    expect(container.textContent).toContain("8a4edef5…6618fc"); // 截断可视
    const srOnly = Array.from(container.querySelectorAll(".sr-only")).map((n) => n.textContent);
    expect(srOnly).toContain(UUID); // 全值在 sr-only（读屏拿得到）
    expect(srOnly).toContain(other);
    // title 也带全值（鼠标兜底）。
    const titled = Array.from(container.querySelectorAll("[title]")).map((n) => n.getAttribute("title"));
    expect(titled).toContain(UUID);
  });

  it("两侧皆空 → 维持 #169 的 —（读屏读「无」）", () => {
    const { container } = render(<BeforeAfter row={row(null, null)} />);
    expect(container.querySelector("details")).toBeNull();
    expect(container.textContent).toContain("—");
  });
});
