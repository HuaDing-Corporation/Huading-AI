import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { token: "test", tenantId: "ten-mock" }, ready: true })
}));

import { resetAdminConsole } from "@/mocks/handlers";
import { BrandVoiceOrderList } from "./brand-voice-order-list";

beforeEach(() => resetAdminConsole());

describe("BrandVoiceOrderList authoritative refund refresh", () => {
  it("changes pending refund copy to currently credited after the user refreshes", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><BrandVoiceOrderList /></QueryClientProvider>);
    const pendingOrder = (await screen.findByText("待下期补回")).closest("li");
    expect(pendingOrder).not.toBeNull();
    expect(within(pendingOrder as HTMLElement).getByText("退款将在下次订阅激活时到账")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "查询退款状态" }));

    expect(await within(pendingOrder as HTMLElement).findByText("已退回当前订阅，积分现在可用")).toBeVisible();
    expect(within(pendingOrder as HTMLElement).queryByText("退款将在下次订阅激活时到账")).not.toBeInTheDocument();
  });
});
