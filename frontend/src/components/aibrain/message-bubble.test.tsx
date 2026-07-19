import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "./message-bubble";
import type { ChatMessage } from "@/lib/aibrain/types";

const base: ChatMessage = {
  id: "m1",
  conversation_id: "c1",
  role: "user",
  content: "看这张图",
  attachments: [],
  status: "completed",
  created_at: "2026-07-19T10:00:00Z"
};

describe("MessageBubble · 附件缩略图（FIX2）", () => {
  it("🔴 附件带 download_url → 显真缩略图 <img>", () => {
    render(
      <MessageBubble
        message={{
          ...base,
          attachments: [{ asset_id: "a1", asset_type: "generated_image", mime_type: "image/png", download_url: "https://mock.local/a1.png" }]
        }}
      />
    );
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://mock.local/a1.png");
  });

  it("download_url 为 null → 降级占位片（不显 img）", () => {
    render(
      <MessageBubble
        message={{
          ...base,
          attachments: [{ asset_id: "a1", asset_type: "document", mime_type: "application/pdf", download_url: null }]
        }}
      />
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("图片")).toBeInTheDocument();
  });
});
