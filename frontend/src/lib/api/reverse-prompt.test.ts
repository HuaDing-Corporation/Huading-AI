import { beforeEach, describe, expect, it } from "vitest";

import { copy } from "@/lib/copy";
import { resetReverseJobs } from "@/mocks/handlers";
import { apiUrl } from "./client";
import {
  fillTargetToPrefill,
  friendlyReverseError,
  getReversePromptJob,
  isReverseSettled,
  regenerateReversePrompt,
  reverseFromAsset,
  saveReversePrompt,
  type ReversePromptFillTargets
} from "./reverse-prompt";

// 🔴 FIX4：与 reverse-prompt.history.test.ts 同口径 —— mock job store 每条测试前重置，顺序无关。
// （本文件里那两条 regenerate/save 原本硬编码 "rp-1"，赌的正是「reverseSeq 从 0 起 + 前面有条 POST」。
//  FIX3 已改为先建再用返回 id；重置让这个前提变成**确定**的，而不是碰巧成立。）
beforeEach(() => resetReverseJobs());

// REVERSE-PROMPT-UI · FIX1：对齐 BE 真契约。核心是「带入 6 键」的落点映射——BE 已给预填载荷，FE 直接
// apply、不猜字段。此处逐一锁死 6 个 BE fill_target 键 → WorkbenchPrefill 的落点，缺键 → null（置灰）。

const FULL_FILL: ReversePromptFillTargets = {
  avatar_talk: { topic: "便携保温杯", script: "大家好，这款保温杯……" },
  seedance_i2v: { topic: "保温杯卖点", scene_prompt: "桌面暖光特写，蒸汽升腾" },
  video_gen: { topic: "保温杯", prompt: "极简产品广告，缓慢环绕运镜" },
  photo: { topic: "白底保温杯特写" },
  ecom_model: { extra_prompt: "工作室柔光、白底" },
  ecom_poster: { title: "年中大促", subtitle: "限时 5 折" }
};

describe("fillTargetToPrefill · 带入 6 键落点（BE 载荷直落，不猜字段）", () => {
  it("avatar_talk → 数字人口播 topic+script", () => {
    expect(fillTargetToPrefill("avatar_talk", FULL_FILL)).toEqual({
      target: "avatar_talk",
      topic: "便携保温杯",
      script: "大家好，这款保温杯……"
    });
  });

  it("seedance_i2v → 电商带货 topic + scene_prompt(→scenePrompt)", () => {
    expect(fillTargetToPrefill("seedance_i2v", FULL_FILL)).toEqual({
      target: "seedance_i2v",
      topic: "保温杯卖点",
      scenePrompt: "桌面暖光特写，蒸汽升腾"
    });
  });

  it("video_gen → 视频生成 prompt(同时作 topic)", () => {
    expect(fillTargetToPrefill("video_gen", FULL_FILL)).toEqual({
      target: "video_gen",
      prompt: "极简产品广告，缓慢环绕运镜"
    });
  });

  it("photo → 图片生成 topic→prompt(提示词即主题)", () => {
    expect(fillTargetToPrefill("photo", FULL_FILL)).toEqual({
      target: "photo",
      prompt: "白底保温杯特写"
    });
  });

  it("ecom_model → 电商图·AI 模特 extra_prompt→自定义补充", () => {
    expect(fillTargetToPrefill("ecom_model", FULL_FILL)).toEqual({
      target: "ecom_image",
      tool: "model",
      custom: "工作室柔光、白底"
    });
  });

  it("ecom_poster → null（营销海报已下线 ECOM-REPLICATE-UI-0001，无落点、按钮已移除）", () => {
    expect(fillTargetToPrefill("ecom_poster", FULL_FILL)).toBeNull();
  });

  it("缺某 fill_target 键 → null（结果页据此置灰该模块「带入」）", () => {
    const only = { avatar_talk: { topic: "x", script: "y" } } as unknown as ReversePromptFillTargets;
    expect(fillTargetToPrefill("seedance_i2v", only)).toBeNull();
    expect(fillTargetToPrefill("photo", only)).toBeNull();
    expect(fillTargetToPrefill("ecom_poster", only)).toBeNull();
    expect(fillTargetToPrefill("avatar_talk", only)).not.toBeNull();
  });
});

describe("friendlyReverseError · 反推失败友好中文（不泄裸串，承 friendlyVideoError 思路）", () => {
  it("缺码 → 通用中文兜底", () => {
    expect(friendlyReverseError()).toBe(copy.errors.reverseFailed);
    expect(friendlyReverseError(null)).toBe(copy.errors.reverseFailed);
  });

  it("承重：裸串 fallback（英文/JSON/栈帧/URL）→ 丢弃、回落通用中文，绝不吐裸串", () => {
    for (const raw of ["Error code: 500 - {raw}", "RuntimeError: boom", "at foo (bar.js:1:2)", "https://x/err"]) {
      const out = friendlyReverseError("SOME_CODE", raw);
      expect(out).toBe(copy.errors.reverseFailed);
      expect(out).not.toBe(raw);
    }
  });

  it("安全中文 fallback 不误伤（原样透出）", () => {
    expect(friendlyReverseError(undefined, copy.errors.generic)).toBe(copy.errors.generic);
  });
});

describe("apiUrl · reverse-prompt 路径单前缀 + /jobs/{id}/（坑②真守卫 + FIX1 路由）", () => {
  for (const p of [
    "/api/v1/reverse-prompt",
    "/api/v1/reverse-prompt/jobs/job-1/regenerate",
    "/api/v1/reverse-prompt/jobs/job-1/save"
  ]) {
    it(`${p} → 无 /api/api 双前缀`, () => {
      expect(apiUrl(p)).not.toContain("/api/api");
      expect(apiUrl(p)).toContain("/api/v1/reverse-prompt");
    });
  }
});

describe("reverse-prompt API ↔ MSW（mock 镜像 BE 真形状：ReversePromptJobRead + 6 键）", () => {
  it("reverseFromAsset：请求体仅 source_asset_id → succeeded + 完整扁平 result + 6 fill_targets", async () => {
    const job = await reverseFromAsset({ source_asset_id: "upload-1" });
    expect(job.status).toBe("succeeded"); // 非自造 "completed"
    expect(job.id).toBeTruthy(); // 读 .id（非 jobId）
    expect(job.source_kind).toBe("image");
    const r = job.result!;
    expect(r.prompt_zh).toBeTruthy();
    expect(r.prompt_en).toBeTruthy();
    expect(r.negative_prompt).toBeTruthy();
    expect(r.subject).toBeTruthy();
    expect(Array.isArray(r.style_tags)).toBe(true);
    expect(typeof r.confidence).toBe("number");
    // 6 键 fill_targets 齐备，供「带入」六路全亮
    expect(r.fill_targets.avatar_talk).toBeTruthy();
    expect(r.fill_targets.seedance_i2v).toBeTruthy();
    expect(r.fill_targets.video_gen).toBeTruthy();
    expect(r.fill_targets.photo).toBeTruthy();
    expect(r.fill_targets.ecom_model).toBeTruthy();
    expect(r.fill_targets.ecom_poster).toBeTruthy();
  });

  // 🔴 FIX3：这两条原本硬编码 `rp-1`。它们能绿是**碰巧的** —— 同文件前面有个 POST 图片反推，
  // reverseSeq 从 0 起、第一个 POST 正好造出 "rp-1"。也就是说它们赌的是**测试执行顺序 + 计数器**，
  // 而当时的 mock 对任意 id 恒成功，所以就算赌错也照样绿 —— 「job 不存在」这个问题被完全掩盖。
  // 改为先创建、再用**返回的 id**：既去掉隐藏依赖，也让「必须存在才成功」这件事真的被这两条测到。
  it("regenerate：/jobs/{id}/regenerate 重新反推，仍返回 succeeded result", async () => {
    const created = await reverseFromAsset({ source_asset_id: "upload-1" });
    const job = await regenerateReversePrompt(created.id);
    expect(job.status).toBe("succeeded"); // 图片反推 BE 是同步的（services:148 → mark_..._succeeded）
    expect(job.result?.prompt_zh).toBeTruthy();
  });

  it("save：/jobs/{id}/save → { id, status:'saved', saved_at }", async () => {
    const created = await reverseFromAsset({ source_asset_id: "upload-1" });
    const res = await saveReversePrompt(created.id);
    expect(res.id).toBe(created.id);
    expect(res.status).toBe("saved"); // BE ReversePromptSavedResponse 有 status（默认值也进响应，schemas:86-89）
    expect(res.saved_at).toBeTruthy();
  });
});

describe("视频反推异步（VIDEO-REVERSE-PROMPT-UI-0001）↔ MSW", () => {
  it("视频源(video-asset-*) → 202 queued（无 result）→ 轮询 GET 第 2 次 succeeded + result.video_analysis；job.credits=provider（非 100）", async () => {
    const created = await reverseFromAsset({ source_asset_id: "video-asset-1" });
    expect(created.status).toBe("queued"); // FIX1③：BE 202 queued（非 running）
    expect(created.source_kind).toBe("video");
    expect(created.result).toBeNull();
    // FIX1④：job.credits=provider 引擎成本，非租户固定 100 扣费（100 走 BE UsageRecord）。
    expect(created.credits).not.toBe(100);
    // 轮询：第 1 次仍 queued，第 2 次终态。
    const poll1 = await getReversePromptJob(created.id);
    expect(poll1.status).toBe("queued");
    const poll2 = await getReversePromptJob(created.id);
    expect(poll2.status).toBe("succeeded");
    expect(poll2.result?.prompt_zh).toBeTruthy();
    // FIX1①：video_analysis 内嵌于 result。
    const va = poll2.result!.video_analysis!;
    expect(va.duration_sec).toBeGreaterThan(0);
    expect(va.shot_list.length).toBeGreaterThan(0);
    // FIX2：pacing 是 BE 枚举（slow|medium|fast|variable，非中文串）。
    expect(["slow", "medium", "fast", "variable"]).toContain(va.pacing);
    // FIX1②/FIX2：分镜字段 index(必填,≥0)/start_sec/end_sec/visual/camera/motion/transition。
    const shot0 = va.shot_list[0];
    expect(shot0.index).toBe(0); // FIX2：每个 shot 必含 index
    expect(va.shot_list.every((s) => Number.isInteger(s.index) && s.index >= 0)).toBe(true);
    expect(shot0.visual).toBeTruthy();
    expect(shot0.start_sec).toBe(0);
    expect(shot0.end_sec).toBeGreaterThan(0);
    expect(typeof shot0.camera).toBe("string"); // BE 默认 ""，恒 string
    expect(va.audio_transcript).toBeNull();
    expect(va.bgm_style).toBeNull();
  });

  it("图片源仍同步 succeeded（视频异步不回归图片路径）", async () => {
    const job = await reverseFromAsset({ source_asset_id: "upload-9" });
    expect(job.status).toBe("succeeded");
    expect(job.source_kind).toBe("image");
    expect(job.result).toBeTruthy();
  });

  it("isReverseSettled：succeeded/failed 终态；queued/running/processing 未终态", () => {
    expect(isReverseSettled("succeeded")).toBe(true);
    expect(isReverseSettled("failed")).toBe(true);
    expect(isReverseSettled("queued")).toBe(false); // FIX1③
    expect(isReverseSettled("running")).toBe(false);
    expect(isReverseSettled("processing")).toBe(false);
  });
});
