import { describe, expect, it } from "vitest";

import { friendlyImageError } from "./image-error";
import { copy } from "@/lib/copy";

describe("friendlyImageError", () => {
  it("maps each known backend error_code to its friendly copy", () => {
    expect(friendlyImageError("IMAGE_MODERATION_BLOCKED")).toBe(copy.errors.imageModeration);
    expect(friendlyImageError("IMAGE_CONNECTION_ERROR")).toBe(copy.errors.imageConnection);
    expect(friendlyImageError("IMAGE_INVALID_REQUEST")).toBe(copy.errors.imageInvalid);
    expect(friendlyImageError("IMAGE_GEN_FAILED")).toBe(copy.errors.imageGeneric);
    expect(friendlyImageError("IMAGE_ALPHA_MISSING")).toBe(copy.errors.imageAlphaMissing);
  });

  it("IMAGE_ALPHA_MISSING 落专属可操作文案，不落通用兜底（FIX1 P2-1）", () => {
    const result = friendlyImageError("IMAGE_ALPHA_MISSING");
    expect(result).toBe(copy.errors.imageAlphaMissing);
    expect(result).not.toBe(copy.errors.imageGeneric);
    expect(result).toContain("白底"); // 给出「改用白底」可操作出路
  });

  it("falls back to a generic friendly line for unknown / missing codes", () => {
    expect(friendlyImageError("SOMETHING_ELSE")).toBe(copy.errors.imageGeneric);
    expect(friendlyImageError(undefined)).toBe(copy.errors.imageGeneric);
    expect(friendlyImageError(null)).toBe(copy.errors.imageGeneric);
    expect(friendlyImageError("")).toBe(copy.errors.imageGeneric);
  });

  it("returns a clean line (never raw JSON / 'Error code') for an unknown code", () => {
    const result = friendlyImageError("WEIRD_400");
    expect(result).toBe(copy.errors.imageGeneric);
    expect(result).not.toContain("Error code");
    expect(result).not.toContain("{");
  });
});
