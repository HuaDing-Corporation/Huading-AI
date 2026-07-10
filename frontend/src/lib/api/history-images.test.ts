import { describe, expect, it } from "vitest";

import {
  getHistoryImageSet,
  historyImageDimensions,
  listHistoryImages,
  type HistoryImageSetItem
} from "./history-images";

// HISTORY-UI-0001 · adapter ↔ MSW（镜像归一契约：list 分页 + detail 整套，4 category）。mock 先行，BE 合并后仅对齐本文件。

describe("listHistoryImages · GET /history/images（分页 + 分类隔离）", () => {
  it("image_gen：第 1 页 20 条、total 23；第 2 页 3 条（加载更多）", async () => {
    const p1 = await listHistoryImages({ category: "image_gen", page: 1 });
    expect(p1.total).toBe(23);
    expect(p1.items).toHaveLength(20);
    expect(p1.page).toBe(1);
    expect(p1.items.every((it) => it.category === "image_gen")).toBe(true);
    // 每条归一字段齐备
    expect(p1.items[0]).toMatchObject({ id: expect.any(String), title: expect.any(String), cover_url: expect.any(String), item_count: 1 });
    const p2 = await listHistoryImages({ category: "image_gen", page: 2 });
    expect(p2.items).toHaveLength(3);
  });

  it("ecom_detail：2 条（主图套 + 详情套），item_count 分别 5 / 12", async () => {
    const res = await listHistoryImages({ category: "ecom_detail" });
    expect(res.total).toBe(2);
    const counts = res.items.map((i) => i.item_count).sort((a, b) => a - b);
    expect(counts).toEqual([5, 12]);
    // partial_failed 记录如实带 status
    expect(res.items.some((i) => i.status === "partial_failed")).toBe(true);
  });

  it("分类隔离：ecom_white 只返白底图（不串其它类）", async () => {
    const res = await listHistoryImages({ category: "ecom_white" });
    expect(res.items.length).toBeGreaterThan(0);
    expect(res.items.every((i) => i.category === "ecom_white")).toBe(true);
  });
});

describe("getHistoryImageSet · GET /history/images/{category}/{id}（重开整套）", () => {
  it("主图套 hd-main-1：5 张 succeeded，含 download_url + 原始尺寸 1254x1254", async () => {
    const set = await getHistoryImageSet("ecom_detail", "hd-main-1");
    expect(set.status).toBe("completed");
    expect(set.items).toHaveLength(5);
    const o0 = set.items[0];
    expect(o0.download_url).toBeTruthy();
    expect(o0.width).toBe(1254);
    expect(o0.height).toBe(1254);
    expect(historyImageDimensions(o0)).toBe("1254x1254");
  });

  it("详情套 hd-detail-1：12 张，partial_failed，缺图张 download_url 为 null", async () => {
    const set = await getHistoryImageSet("ecom_detail", "hd-detail-1");
    expect(set.status).toBe("partial_failed");
    expect(set.items).toHaveLength(12);
    const missing = set.items.filter((it) => it.download_url == null);
    expect(missing.length).toBe(1);
  });

  it("跨租户 / 不存在 → 404", async () => {
    await expect(getHistoryImageSet("ecom_detail", "nope")).rejects.toMatchObject({ status: 404 });
  });
});

describe("historyImageDimensions", () => {
  it("width/height 均在才给「WxH」；缺一即 null（不冒充）", () => {
    const base: HistoryImageSetItem = { index: 0 };
    expect(historyImageDimensions({ ...base, width: 1086, height: 1448 })).toBe("1086x1448");
    expect(historyImageDimensions({ ...base, width: null, height: 1448 })).toBeNull();
    expect(historyImageDimensions({ ...base, width: 1086, height: null })).toBeNull();
    expect(historyImageDimensions(base)).toBeNull();
  });
});
