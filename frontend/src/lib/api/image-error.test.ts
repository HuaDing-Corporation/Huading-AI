import { describe, expect, it } from "vitest";

import { friendlyImageError } from "./image-error";
import { copy } from "@/lib/copy";

describe("friendlyImageError", () => {
  it("maps each known backend error_code to its friendly copy", () => {
    expect(friendlyImageError("IMAGE_MODERATION_BLOCKED")).toBe(copy.errors.imageModeration);
    expect(friendlyImageError("IMAGE_CONNECTION_ERROR")).toBe(copy.errors.imageConnection);
    expect(friendlyImageError("IMAGE_INVALID_REQUEST")).toBe(copy.errors.imageInvalid);
    expect(friendlyImageError("IMAGE_GEN_FAILED")).toBe(copy.errors.imageGeneric);
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
