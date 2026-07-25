import { beforeEach, describe, expect, it } from "vitest";

import { copy } from "@/lib/copy";
import { resetReverseJobs } from "@/mocks/handlers";
import { uploadImage } from "@/lib/api/uploads";
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

// REVERSE-PROMPT-UI：对齐 BE 真契约。核心是「带入 5 键」的落点映射——BE 已给预填载荷，FE 直接
// apply、不猜字段。此处逐一锁死 5 个 BE fill_target 键 → WorkbenchPrefill 的落点，缺键 → null（置灰）。
// 🔴 FIX2 真联调：删掉了第 6 个键 ecom_poster —— BE fill_targets 只有 5 键，自测显式
//    `assert "ecom_poster" not in fill_targets`（backend/tests/test_reverse_prompt_pipeline.py:2713）。

const FULL_FILL: ReversePromptFillTargets = {
  avatar_talk: { topic: "便携保温杯", script: "大家好，这款保温杯……" },
  seedance_i2v: { topic: "保温杯卖点", scene_prompt: "桌面暖光特写，蒸汽升腾" },
  video_gen: { topic: "保温杯", prompt: "极简产品广告，缓慢环绕运镜" },
  photo: { topic: "白底保温杯特写" },
  ecom_model: { extra_prompt: "工作室柔光、白底" }
};

describe("fillTargetToPrefill · 带入 5 键落点（BE 载荷直落，不猜字段）", () => {
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

  it("缺某 fill_target 键 → null（结果页据此置灰该模块「带入」）", () => {
    const only = { avatar_talk: { topic: "x", script: "y" } } as unknown as ReversePromptFillTargets;
    expect(fillTargetToPrefill("seedance_i2v", only)).toBeNull();
    expect(fillTargetToPrefill("photo", only)).toBeNull();
    expect(fillTargetToPrefill("ecom_model", only)).toBeNull();
    expect(fillTargetToPrefill("avatar_talk", only)).not.toBeNull();
  });

  // 🔴 FIX2 承重门14「BE 恒发键、缺失表示为 null」：BE 是 pydantic 模型，图片源下 duration_sec 实发 **null**
  //    而不是省略键（运行时证据 backend/tests/test_reverse_prompt_pipeline.py:2688/2699 的整字典相等断言）。
  //    映射层若用 `!== undefined` 判「给没给」，null 会穿过闸门 → 弹窗渲染「时长 null 秒」、null 落进控件。
  //    变异：把 reverse-prompt.ts 的 `t.duration_sec != null` 改回 `t.duration_sec !== undefined` → 本条必红。
  it("🔴 承重门14 · BE 恒发的 null 不算「给了」：duration_sec/shot_section 为 null → 整键不下发", () => {
    const beImageShape: ReversePromptFillTargets = {
      ...FULL_FILL,
      // 逐字照抄 BE 图片源的真实形状（tests:2684-2700）
      video_gen: {
        topic: "杯",
        prompt: "p",
        negative_prompt: "n",
        aspect_ratio: "3:4",
        duration_sec: null,
        duration_clamped: false,
        generate_audio: false,
        shot_section: null
      },
      seedance_i2v: {
        topic: "杯",
        script: null,
        scene_prompt: "s",
        negative_prompt: "n",
        aspect_ratio: "9:16",
        duration_sec: null,
        duration_clamped: false,
        shot_section: null
      }
    };
    const vg = fillTargetToPrefill("video_gen", beImageShape) as Record<string, unknown>;
    expect("durationSec" in vg).toBe(false);
    expect("durationClamped" in vg).toBe(false);
    expect("shotSection" in vg).toBe(false);
    // generate_audio: false 是**真实观测**（这段素材没台词），且弹窗里是可取消的可见项 → 照常下发
    expect(vg.generateAudio).toBe(false);

    const ec = fillTargetToPrefill("seedance_i2v", beImageShape) as Record<string, unknown>;
    expect("durationSec" in ec).toBe(false);
    expect("durationClamped" in ec).toBe(false);
    expect("shotSection" in ec).toBe(false);
    expect("script" in ec).toBe(false); // BE 无台词时发 null
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

describe("reverse-prompt API ↔ MSW（mock 镜像 BE 真形状：ReversePromptJobRead + 5 键）", () => {
  it("reverseFromAsset：请求体仅 source_asset_id → succeeded + 完整扁平 result + 5 fill_targets", async () => {
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
    // 🔴 FIX2 承重门15「mock 的 fill_targets 键集合 == BE 的 5 键，一个不多一个不少」。
    //    断**键集合严格相等**而不是逐个 toBeTruthy：后者对「mock 多造一个 BE 不发的键」完全无感，
    //    而那正是 ecom_poster 混进来两年没被发现的原因。BE 侧同款断言见
    //    backend/tests/test_reverse_prompt_pipeline.py:2713 `assert "ecom_poster" not in fill_targets`。
    //    变异：往 handlers.ts 的 fill_targets 里加回 ecom_poster → 本条必红。
    expect(Object.keys(r.fill_targets).sort()).toEqual([
      "avatar_talk",
      "ecom_model",
      "photo",
      "seedance_i2v",
      "video_gen"
    ]);
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
    // 🔴 FIX2 真联调订正：**ASR 已落地**（§八 M6 / BE #219）。上一版这里断言 audio_transcript 恒 null，
    //    钉的是「D4 SPIKE 未出结论、功能未启用」那个已经作废的状态 —— 属于典型的「测试把过期契约锁死」。
    //    BE 现在真调转写（services/reverse_prompt_video.py:497-528），有台词就给串；
    //    BE 自测里 24s 视频拿到的是 "这是一段完整台词。"（tests:1765）。
    expect(typeof va.audio_transcript).toBe("string");
    expect(va.audio_transcript).toBeTruthy();
    // bgm_style 仍恒 null，但理由变了：不是「一期没做」，而是 §八 M6「ASR 判不了音乐风格 → 不许编」，
    // BE 写死 None（reverse_prompt_video.py:375/461）。这条继续钉住，防止哪天有人拿模型幻觉去填它。
    expect(va.bgm_style).toBeNull();

    // 🔴 FIX2 承重门17「mock 的段头与 BE 逐字一致」：BE 是 `f"Shots:\n{summary}"`
    //    （backend/app/services/reverse_prompt.py:872）—— 冒号后是**换行、不是空格**，真机串恒为两行。
    //    BE 自测同款判据：tests:1996-1997 `startswith("Shots:")`。
    //    这条钉在 mock 上：谁把它改回 `Shots: `（空格）就红，防止 fixture 悄悄漂回去。
    const ft = poll2.result!.fill_targets;
    expect(ft.video_gen.shot_section).toMatch(/^Shots:\n/);
    expect(ft.seedance_i2v.shot_section).toMatch(/^Shots:\n/);
    // 两个模块共用 BE 同一个变量（services:731 算一次 → :749/:766 挂两处）→ 必须逐字相同
    expect(ft.seedance_i2v.shot_section).toBe(ft.video_gen.shot_section);
    // photo 不给 shot_section（§八 M2 只列 video_gen / seedance_i2v）
    expect("shot_section" in ft.photo).toBe(false);
    // ASR 联动：BE `script = transcript or None`、`generate_audio = bool(transcript)`（services:736/767）
    expect(ft.seedance_i2v.script).toBe(va.audio_transcript);
    expect(ft.video_gen.generate_audio).toBe(true);
  });

  // 🔴 FIX3：这条原先用的是 `upload-9` —— 一个**从没上传过**的 id。它当时能绿，是因为 mock 只判 id 前缀；
  //    现在 POST /reverse-prompt 与 estimate 同走唯一资产注册表，编造的 id 会正确地 404（真 BE 就是这样）。
  //    改为先真的走一次上传拿 asset_id：既保住本条的原意（图片源走同步路径），
  //    又顺带把「上传 → 登记 → 可反推」这条链真的跑通，而不是绕过上传直接喂一个自造 id。
  it("图片源仍同步 succeeded（视频异步不回归图片路径）", async () => {
    const uploaded = await uploadImage(new File(["x"], "a.png", { type: "image/png" }));
    const job = await reverseFromAsset({ source_asset_id: uploaded.asset_id });
    expect(job.status).toBe("succeeded");
    expect(job.source_kind).toBe("image");
    expect(job.result).toBeTruthy();
  });

  // 🔴 FIX3 承重门16 的第三面（创建端点侧）：estimate 与 POST /reverse-prompt **必须同源**。
  //    只给 estimate 加守卫、创建端点仍放行，就会出现「报价说 404、真提交却成功」的自相矛盾。
  //    变异：把 POST /reverse-prompt 的 `getMockAssetForTenant` 换回 `startsWith("video-")` → 本条红。
  it("🔴 创建端点与 estimate 同源：从未上传的资产 → 404（不会「报价 404、提交却成功」）", async () => {
    await expect(reverseFromAsset({ source_asset_id: "upload-999" })).rejects.toMatchObject({
      status: 404,
      code: "REVERSE_PROMPT_SOURCE_NOT_FOUND"
    });
  });

  it("isReverseSettled：succeeded/failed 终态；queued/running/processing 未终态", () => {
    expect(isReverseSettled("succeeded")).toBe(true);
    expect(isReverseSettled("failed")).toBe(true);
    expect(isReverseSettled("queued")).toBe(false); // FIX1③
    expect(isReverseSettled("running")).toBe(false);
    expect(isReverseSettled("processing")).toBe(false);
  });
});
