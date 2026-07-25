import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/client";

import { clearHistoryImages, deleteHistoryImageSet, listHistoryImages } from "@/lib/api/history-images";
import { createConversation, deleteConversation, listConversations } from "@/lib/aibrain/api";

// HISTORY-CHAT-DELETE-UI-0001 · **mock 契约承重**（真走 apiFetch → 全局 MSW，非 stub）。
// mock 纪律（任务包 §三）：删除必须**真的从列表里去掉**；清空必须**只清当前分类**（比 BE 宽松 = 假绿）；
// 跨租户/404 分支要能表达。这几条只有在"真跑一遍 list → delete → 再 list"里才验得出来。

describe("图片历史删除 · mock 契约（apiFetch 真走 MSW）", () => {
  // 🔴 门 1 的数据侧：删一条后**重新拉列表**，该条真没了、其余一条不少。
  it("删一条 → 再拉列表：该条消失、其余条目数恰好 -1", async () => {
    const before = await listHistoryImages({ category: "image_gen", page_size: 100 });
    expect(before.items.length).toBeGreaterThan(1);
    const target = before.items[0];

    const res = await deleteHistoryImageSet("image_gen", target.id);
    expect(res.deleted).toBe(true);

    const after = await listHistoryImages({ category: "image_gen", page_size: 100 });
    expect(after.items.length).toBe(before.items.length - 1); // 确定计数
    expect(after.items.find((i) => i.id === target.id)).toBeUndefined();
  });

  // 🔴 门 2 的数据侧：清空**只清当前分类**——其余分类一条不少（mock 比 BE 宽松地"顺手清全部" = 假绿）。
  it("清空 ecom_white → 该分类空、其它分类计数不变", async () => {
    const otherBefore = await listHistoryImages({ category: "ecom_model", page_size: 100 });
    const targetBefore = await listHistoryImages({ category: "ecom_white", page_size: 100 });
    expect(targetBefore.items.length).toBeGreaterThan(0);

    const res = await clearHistoryImages("ecom_white");
    expect(res.deleted_count).toBe(targetBefore.items.length);

    const targetAfter = await listHistoryImages({ category: "ecom_white", page_size: 100 });
    const otherAfter = await listHistoryImages({ category: "ecom_model", page_size: 100 });
    expect(targetAfter.items.length).toBe(0); // 该分类空
    expect(otherAfter.items.length).toBe(otherBefore.items.length); // 🔴 其余分类一条不少
  });

  // 🔴 FIX1 真联调：BE 实打响应 404 的 code 是 **IMAGE_HISTORY_NOT_FOUND**（services/image_history.py:703），
  // 原 mock 写的是 HISTORY_NOT_FOUND —— 形状/码不一致正是今天付了三次学费的那类缺陷。变异：改回 HISTORY_NOT_FOUND → 本条红。
  it("防假绿：不存在/跨租户 id → 404 且 code=IMAGE_HISTORY_NOT_FOUND（逐字对齐 #221 真实响应）", async () => {
    let caught: unknown;
    try {
      await deleteHistoryImageSet("image_gen", "not-mine");
    } catch (e) {
      caught = e;
    }
    expect((caught as ApiError)?.status).toBe(404);
    expect((caught as ApiError)?.code).toBe("IMAGE_HISTORY_NOT_FOUND");
  });

  // FIX1 真联调：**成功响应形状**是 200 + {deleted:true}（不是 204 无 body）——冻结 §5 写的"204 或 {deleted:true}"，
  // BE 实际取的是后者（ApiResponse[ImageHistoryDeletedResponse] 包裹）。adapter 若按 204 解析会拿到 undefined。
  it("成功形状：删除返 {deleted:true}、清空返 {deleted_count:number}（200 + body，非 204）", async () => {
    const list = await listHistoryImages({ category: "cover", page_size: 100 });
    if (list.items.length > 0) {
      const del = await deleteHistoryImageSet("cover", list.items[0].id);
      expect(del).toEqual({ deleted: true });
    }
    const cleared = await clearHistoryImages("cover");
    expect(typeof cleared.deleted_count).toBe("number");
  });

  it("防假绿：非法分类 → 422（mock 不比 BE 宽松，BE 对 Literal 参数 422）", async () => {
    await expect(deleteHistoryImageSet("bogus_cat", "h1")).rejects.toThrow();
    await expect(clearHistoryImages("bogus_cat")).rejects.toThrow();
  });

  // 🔴 门 5 的数据侧：失败时**服务端一条都没删**（前端不乐观移除 + 后端未变 = 界面与真相一致）。
  it("失败注入（__FAIL__）→ 500 且列表一条不少", async () => {
    const before = await listHistoryImages({ category: "image_gen", page_size: 100 });
    await expect(deleteHistoryImageSet("image_gen", "h__FAIL__")).rejects.toThrow();
    const after = await listHistoryImages({ category: "image_gen", page_size: 100 });
    expect(after.items.length).toBe(before.items.length);
  });
});

describe("智脑会话删除 · mock 契约（apiFetch 真走 MSW）", () => {
  it("删一个会话 → 再拉列表：该会话消失、其余不少；重复删 → 404", async () => {
    // 智脑 mock 初始无种子会话（新租户空态）→ 先建两个，再验删除只删掉指定那个。
    await createConversation();
    await createConversation();
    const before = await listConversations();
    expect(before.length).toBeGreaterThan(1);
    const target = before[0];

    const res = await deleteConversation(target.id);
    expect(res.deleted).toBe(true);

    const after = await listConversations();
    expect(after.length).toBe(before.length - 1);
    expect(after.find((c) => c.id === target.id)).toBeUndefined();

    // 已删 → 再删即 404（与跨租户同码：BE 租户过滤后就是"不存在"）。
    await expect(deleteConversation(target.id)).rejects.toThrow();
  });
});
