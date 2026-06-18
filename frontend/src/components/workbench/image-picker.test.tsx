import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ImagePicker } from "./image-picker";

// jsdom doesn't implement object-URL APIs — provide them for the preview flow.
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => vi.restoreAllMocks());

describe("ImagePicker clearUpload (P1)", () => {
  it("clears the parent avatarAssetId (onChange(null)) when the uploaded image is removed", () => {
    const onChange = vi.fn();
    const onUpload = vi.fn();
    const { container, rerender } = render(
      <ImagePicker value={null} onChange={onChange} presets={[]} uploading={false} onUpload={onUpload} />
    );

    // Select a valid image → preview appears + parent uploads it.
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["x"], "a.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(onUpload).toHaveBeenCalledWith(file);

    // Container set the asset id after a successful upload.
    rerender(
      <ImagePicker value="asset-1" onChange={onChange} presets={[]} uploading={false} onUpload={onUpload} />
    );

    // Remove the image → parent avatarAssetId must be cleared so 生成 can't
    // submit the removed asset.
    fireEvent.click(screen.getByLabelText("移除图片"));
    expect(onChange).toHaveBeenCalledWith(null);
  });
});
