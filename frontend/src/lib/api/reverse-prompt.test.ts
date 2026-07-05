import { describe, expect, it } from "vitest";

import { copy } from "@/lib/copy";
import { apiUrl } from "./client";
import {
  fillTargetToPrefill,
  friendlyReverseError,
  regenerateReversePrompt,
  reverseFromAsset,
  saveReversePrompt,
  type ReversePromptFillTargets
} from "./reverse-prompt";

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

  it("ecom_poster → 电商图·营销海报 title/subtitle", () => {
    expect(fillTargetToPrefill("ecom_poster", FULL_FILL)).toEqual({
      target: "ecom_image",
      tool: "poster",
      title: "年中大促",
      tagline: "限时 5 折"
    });
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

  it("regenerate：/jobs/{id}/regenerate 重新反推，仍返回 succeeded result", async () => {
    const job = await regenerateReversePrompt("rp-1");
    expect(job.status).toBe("succeeded");
    expect(job.result?.prompt_zh).toBeTruthy();
  });

  it("save：/jobs/{id}/save → { id, status:'saved', saved_at }", async () => {
    const res = await saveReversePrompt("rp-1");
    expect(res.id).toBe("rp-1");
    expect(res.status).toBe("saved");
    expect(res.saved_at).toBeTruthy();
  });
});
