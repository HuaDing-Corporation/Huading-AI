import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { HistoryImageSetItem } from "@/lib/api/history-images";
import { useMediaUrlRefreshScope } from "@/lib/media/use-media-url-refresh";
import { HistoryImageTile } from "./history-image-tile";

// FIX1：`onUrlError: () => void` → `refresh: MediaUrlRefreshScope`（预算属于 query，不属于每张 tile）。
// 只换接线，下面两条原图红线断言一字未动。
function Tile({ item }: { item: HistoryImageSetItem }) {
  const refresh = useMediaUrlRefreshScope(vi.fn().mockResolvedValue(undefined));
  return <HistoryImageTile item={item} refresh={refresh} />;
}

// HISTORY-UI-0001 · 整套单张·原图红线：下载给原图 bytes（<a download>）+ 显示原始尺寸 + 零 canvas。
// FIX2 对齐真实 BE：详情只返成功张、失败张已 omit（schema download_url:str 非空）→ 无「缺图」形态，删掉缺图禁用用例。

describe("HistoryImageTile (原图红线)", () => {
  const base: HistoryImageSetItem = {
    index: 0,
    download_url: "https://cdn/hist-0.png?dl=1",
    width: 1254,
    height: 1254,
    theme: "layout_match"
  };

  it("有 download_url：预览 <img src=download_url object-contain> + 下载 <a href download> + 原始尺寸 + 零 canvas", () => {
    render(<Tile item={base} />);
    const img = document.querySelector("img");
    expect(img).toHaveAttribute("src", "https://cdn/hist-0.png?dl=1");
    expect(img?.className).toContain("object-contain");
    const link = screen.getByRole("link", { name: copy.historyImages.download });
    expect(link).toHaveAttribute("href", "https://cdn/hist-0.png?dl=1");
    expect(link).toHaveAttribute("download");
    // 不隐藏原始尺寸
    expect(screen.getByText(copy.historyImages.sizeLabel("1254x1254"))).toBeInTheDocument();
    // 红线：零前端后处理
    expect(document.querySelector("canvas")).toBeNull();
  });

  it("详情图 theme 机器键本地化展示（复用既有映射）", () => {
    render(<Tile item={base} />);
    expect(screen.getByText(copy.workbench.ecomReplicateTheme("layout_match"))).toBeInTheDocument();
  });
});
