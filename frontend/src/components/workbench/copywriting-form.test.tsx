import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const rewriteMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const titlesMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const topicsMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const saveMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
  useRewriteCopy: () => ({ mutateAsync: rewriteMock.mutateAsync, isPending: false }),
  useGenerateTitles: () => ({ mutateAsync: titlesMock.mutateAsync, isPending: false }),
  useGenerateTopics: () => ({ mutateAsync: topicsMock.mutateAsync, isPending: false }),
  useSaveCopyDraft: () => ({ mutateAsync: saveMock.mutateAsync, isPending: false })
}));

import { CopywritingForm } from "./copywriting-form";

const SOURCE = /粘贴你有权使用/;

beforeEach(() => {
  rewriteMock.mutateAsync.mockResolvedValue({ results: [{ text: "改写后的文案" }] });
  titlesMock.mutateAsync.mockResolvedValue({ titles: ["标题A", "标题B"] });
  topicsMock.mutateAsync.mockResolvedValue({ topics: ["#话题A", "#话题B"] });
  saveMock.mutateAsync.mockResolvedValue({ id: "draft-1", created_at: "" });
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
    writable: true
  });
});
afterEach(() => vi.clearAllMocks());

function typeSource(text: string) {
  fireEvent.change(screen.getByPlaceholderText(SOURCE), { target: { value: text } });
}

describe("CopywritingForm (文案仿写 + 标题/话题)", () => {
  it("源文案为空时禁用生成并提示", () => {
    render(<CopywritingForm />);
    expect(screen.getByRole("button", { name: /生成文案/ })).toBeDisabled();
    expect(screen.getByText("请先粘贴参考文案")).toBeInTheDocument();
  });

  it("一键生成并发调 rewrite + titles + topics 并渲染结果", async () => {
    render(<CopywritingForm />);
    typeSource("原始参考文案");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    await waitFor(() => {
      expect(rewriteMock.mutateAsync).toHaveBeenCalledTimes(1);
      expect(titlesMock.mutateAsync).toHaveBeenCalledTimes(1);
      expect(topicsMock.mutateAsync).toHaveBeenCalledTimes(1);
    });
    expect(rewriteMock.mutateAsync.mock.calls[0][0]).toMatchObject({
      source_text: "原始参考文案",
      mode: "smart"
    });
    // smart 模式不带 n / instruction
    expect(rewriteMock.mutateAsync.mock.calls[0][0].n).toBeUndefined();
    expect(rewriteMock.mutateAsync.mock.calls[0][0].instruction).toBeUndefined();

    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
    expect(screen.getByText("标题A")).toBeInTheDocument();
    expect(screen.getByText("#话题A")).toBeInTheDocument();
  });

  it("自定义模式：显示指令框、未填指令禁用、填后启用", () => {
    render(<CopywritingForm />);
    typeSource("原文");
    expect(screen.queryByPlaceholderText(/更口语/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "自定义" }));
    expect(screen.getByPlaceholderText(/更口语/)).toBeInTheDocument();
    // 指令空 → 仍禁用
    expect(screen.getByRole("button", { name: /生成文案/ })).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/更口语/), { target: { value: "更口语、更简短" } });
    expect(screen.getByRole("button", { name: /生成文案/ })).toBeEnabled();

    // 切回智能 → 指令框隐藏
    fireEvent.click(screen.getByRole("button", { name: "智能" }));
    expect(screen.queryByPlaceholderText(/更口语/)).not.toBeInTheDocument();
  });

  it("自定义模式生成时带 instruction、不带 n", async () => {
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: "自定义" }));
    fireEvent.change(screen.getByPlaceholderText(/更口语/), { target: { value: "换高端风格" } });
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    await waitFor(() => expect(rewriteMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(rewriteMock.mutateAsync.mock.calls[0][0]).toMatchObject({
      mode: "custom",
      instruction: "换高端风格"
    });
    expect(rewriteMock.mutateAsync.mock.calls[0][0].n).toBeUndefined();
  });

  it("自动多版：渲染候选卡片、默认载入首条、点击换条、请求带 n", async () => {
    rewriteMock.mutateAsync.mockResolvedValue({
      results: [{ text: "候选一" }, { text: "候选二" }, { text: "候选三" }]
    });
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: "自动多版" }));
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    // 默认载入首条进编辑框
    expect(await screen.findByDisplayValue("候选一")).toBeInTheDocument();
    // 候选卡片可见
    expect(screen.getByText("候选二")).toBeInTheDocument();
    // 点击候选二 → 载入编辑框
    fireEvent.click(screen.getByText("候选二"));
    expect(await screen.findByDisplayValue("候选二")).toBeInTheDocument();
    // auto 请求带 n（默认 3）
    expect(rewriteMock.mutateAsync.mock.calls[0][0]).toMatchObject({ mode: "auto", n: 3 });
  });

  it("保存到历史调 saveCopyDraft，带 source/result/mode/titles/topics", async () => {
    render(<CopywritingForm />);
    typeSource("原文XYZ");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
    await screen.findByDisplayValue("改写后的文案");

    fireEvent.click(screen.getByRole("button", { name: /保存到历史/ }));
    await waitFor(() => expect(saveMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(saveMock.mutateAsync.mock.calls[0][0]).toMatchObject({
      source_text: "原文XYZ",
      result_text: "改写后的文案",
      mode: "smart",
      titles: ["标题A", "标题B"],
      topics: ["#话题A", "#话题B"]
    });
  });

  it("一键串联：用此文案回调带 result_text（口播 + 电商）", async () => {
    const onUse = vi.fn();
    render(<CopywritingForm onUseInVideo={onUse} />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
    await screen.findByDisplayValue("改写后的文案");

    fireEvent.click(screen.getByRole("button", { name: /数字人口播/ }));
    expect(onUse).toHaveBeenCalledWith("avatar_talk", "改写后的文案");

    fireEvent.click(screen.getByRole("button", { name: /电商带货/ }));
    expect(onUse).toHaveBeenCalledWith("seedance_i2v", "改写后的文案");
  });

  it("复制按钮写入剪贴板", async () => {
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
    await screen.findByDisplayValue("改写后的文案");

    fireEvent.click(screen.getByRole("button", { name: /复制/ }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith("改写后的文案"));
  });

  it("标题端点失败时降级不渲染标题区，但文案与话题仍出", async () => {
    titlesMock.mutateAsync.mockRejectedValue(new Error("titles boom"));
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
    expect(screen.queryByText("标题候选（点击复制）")).not.toBeInTheDocument();
    expect(screen.getByText("#话题A")).toBeInTheDocument();
  });
});
