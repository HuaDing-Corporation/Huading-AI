import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { getHistoryImageSet, listHistoryImages, type HistoryCategory } from "@/lib/api/history-images";

// HISTORY-IMAGE-TAB-UI-0001 归一契约 mock 承重（真打 MSW /history/images，不 mock adapter）：
// 6 分类（含新 cover）+ 省略 category=全部图片。全确定值断言。
// ⚠️ mock 先行：cover 分类由 HISTORY-REFACTOR-BE-0001（Codex A）补，BE 合后按真契约逐字段核对。
// FIX1：图片删除端点被摘（用户「三拆」改 GC 方案）→ 本文件不再测硬删（不留打 DELETE 的用例/mock）。
beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("图片历史归一契约（6 分类 + 全部）", () => {
  it("cover 分类：恰 2 条（hc-1/hc-2），item_count=1", async () => {
    const r = await listHistoryImages({ category: "cover", page_size: 100 });
    expect(r.total).toBe(2);
    expect(r.items.map((i) => i.id).sort()).toEqual(["hc-1", "hc-2"]);
    expect(r.items[0].item_count).toBe(1);
  });

  it("省略 category = 全部图片：total=30（各类合计），按 created_at 倒序", async () => {
    const r = await listHistoryImages({ page_size: 100 });
    // ecom_detail 2 + ecom_model 1 + ecom_white 2 + cover 2 + image_gen 23 = 30
    expect(r.total).toBe(30);
    // 倒序：seed 时间戳递减，最新（histTs(0)）= hd-main-1 在首位。
    expect(r.items[0].id).toBe("hd-main-1");
  });

  it("各分类计数确定：image_gen 23 / ecom_detail 2 / ecom_model 1 / ecom_white 2", async () => {
    expect((await listHistoryImages({ category: "image_gen", page_size: 100 })).total).toBe(23);
    expect((await listHistoryImages({ category: "ecom_detail", page_size: 100 })).total).toBe(2);
    expect((await listHistoryImages({ category: "ecom_model", page_size: 100 })).total).toBe(1);
    expect((await listHistoryImages({ category: "ecom_white", page_size: 100 })).total).toBe(2);
  });

  it("详情整套：ecom_detail 主图 5 张（completed）；partial 详情只返 11 张成功（失败张 BE 已 omit，全可下载）", async () => {
    const main = await getHistoryImageSet("ecom_detail", "hd-main-1");
    expect(main.items).toHaveLength(5);
    expect(main.status).toBe("completed");
    // FIX2 对齐真实 BE：services/image_history.py:106 `if not output.download_url: continue` —— 详情只返成功张，
    // 失败张已 omit（schema download_url:str 非空）。套级 status 仍标 partial_failed（从 task 带出），但无「缺图」项。
    const detail = await getHistoryImageSet("ecom_detail", "hd-detail-1");
    expect(detail.items).toHaveLength(11);
    expect(detail.status).toBe("partial_failed");
    expect(detail.items.every((it) => typeof it.download_url === "string" && it.download_url.length > 0)).toBe(true);
  });

  it("非法 category → 422（🔴 mock 不比 BE 宽松；BE routes/history.py category:ImageHistoryCategory 非枚举即 422）", async () => {
    // 列表端点：非枚举分类
    await expect(listHistoryImages({ category: "bogus" as HistoryCategory, page_size: 100 })).rejects.toMatchObject({
      status: 422,
      code: "VALIDATION_ERROR"
    });
    // 详情端点：path 段非法 category 同样 422（先于跨租户/不存在 404）
    await expect(getHistoryImageSet("bogus", "hd-main-1")).rejects.toMatchObject({
      status: 422,
      code: "VALIDATION_ERROR"
    });
  });
});
