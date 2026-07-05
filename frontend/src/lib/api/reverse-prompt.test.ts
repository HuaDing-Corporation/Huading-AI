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

// REVERSE-PROMPT-UI-0001：扁平 result + fill_targets。核心是「带入 4 模块」的落点映射——
// BE 已给预填载荷，FE 直接 apply、不猜字段。此处逐一锁死 4 个 fill_target → WorkbenchPrefill 的落点，
// 缺失 fill_target → null（结果页据此置灰「带入」）。改动落点此断言应红。

const FULL_FILL: ReversePromptFillTargets = {
  avatar_talk: { topic: "便携保温杯", script: "大家好，这款保温杯……" },
  seedance_i2v: { topic: "保温杯卖点", scene_prompt: "桌面暖光特写，蒸汽升腾" },
  video_gen: { prompt: "极简产品广告，缓慢环绕运镜", topic: "保温杯" },
  ecom_image: { topic: "保温杯", extra_prompt: "工作室柔光、白底", poster_title: "年中大促", poster_subtitle: "限时 5 折" }
};

describe("fillTargetToPrefill · 带入 4 模块落点（BE 载荷直落，不猜字段）", () => {
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

  it("ecom_image：有海报标题 → 落营销海报(poster)，title/subtitle + extra_prompt", () => {
    expect(fillTargetToPrefill("ecom_image", FULL_FILL)).toEqual({
      target: "ecom_image",
      tool: "poster",
      custom: "工作室柔光、白底",
      title: "年中大促",
      tagline: "限时 5 折"
    });
  });

  it("ecom_image：无海报标题 → 落 AI 模特(model)，extra_prompt→custom", () => {
    const noPoster: ReversePromptFillTargets = {
      ecom_image: { topic: "保温杯", extra_prompt: "工作室柔光、白底" }
    };
    expect(fillTargetToPrefill("ecom_image", noPoster)).toEqual({
      target: "ecom_image",
      tool: "model",
      custom: "工作室柔光、白底",
      title: undefined,
      tagline: undefined
    });
  });

  it("缺失 fill_target → null（结果页据此置灰该模块「带入」）", () => {
    const only: ReversePromptFillTargets = { avatar_talk: { topic: "x" } };
    expect(fillTargetToPrefill("seedance_i2v", only)).toBeNull();
    expect(fillTargetToPrefill("video_gen", only)).toBeNull();
    expect(fillTargetToPrefill("ecom_image", only)).toBeNull();
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

describe("apiUrl · reverse-prompt 路径单前缀（坑②真守卫）", () => {
  for (const p of [
    "/api/v1/reverse-prompt",
    "/api/v1/reverse-prompt/job-1/regenerate",
    "/api/v1/reverse-prompt/job-1/save"
  ]) {
    it(`${p} → 无 /api/api 双前缀`, () => {
      expect(apiUrl(p)).not.toContain("/api/api");
      expect(apiUrl(p)).toContain("/api/v1/reverse-prompt");
    });
  }
});

describe("reverse-prompt API ↔ MSW（mock 忠实：扁平 result + 4 fill_targets）", () => {
  it("reverseFromAsset：只收 source_asset_id + 语言 + 细节 → completed + 完整扁平 result", async () => {
    const job = await reverseFromAsset({
      source_asset_id: "upload-1",
      output_language: "bilingual",
      detail_level: "standard"
    });
    expect(job.status).toBe("completed");
    expect(job.jobId).toBeTruthy();
    const r = job.result!;
    // 扁平字段齐备
    expect(r.prompt_zh).toBeTruthy();
    expect(r.prompt_en).toBeTruthy();
    expect(r.negative_prompt).toBeTruthy();
    expect(r.subject).toBeTruthy();
    expect(Array.isArray(r.style_tags)).toBe(true);
    expect(typeof r.confidence).toBe("number");
    // 4 模块 fill_targets 齐备，供「带入」全亮
    expect(r.fill_targets.avatar_talk).toBeTruthy();
    expect(r.fill_targets.seedance_i2v).toBeTruthy();
    expect(r.fill_targets.video_gen).toBeTruthy();
    expect(r.fill_targets.ecom_image).toBeTruthy();
  });

  it("regenerate：同 job 重新反推，仍返回 completed result", async () => {
    const job = await regenerateReversePrompt("job-1");
    expect(job.status).toBe("completed");
    expect(job.result?.prompt_zh).toBeTruthy();
  });

  it("save：保存成功", async () => {
    const res = await saveReversePrompt("job-1");
    expect(res.saved).toBe(true);
  });
});
