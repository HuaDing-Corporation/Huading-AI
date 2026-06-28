import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { copy } from "@/lib/copy";
import { AiLabelNotice } from "./ai-label-notice";

describe("AiLabelNotice (产物处 AI 标识知情提示)", () => {
  it("渲染「已含 AI 生成标识」", () => {
    render(<AiLabelNotice />);
    expect(screen.getByText(copy.label.productNotice)).toBeInTheDocument();
  });
});
