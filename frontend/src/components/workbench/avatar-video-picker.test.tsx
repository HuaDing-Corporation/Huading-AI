import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import { AvatarVideoPicker } from "./avatar-video-picker";

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
});

const mp4 = () => new File([new Uint8Array(8)], "v.mp4", { type: "video/mp4" });
function selectFile(file = mp4()) {
  fireEvent.change(document.querySelector("#avatar-video")!, { target: { files: [file] } });
}

describe("AvatarVideoPicker（本人出镜视频源 · 预检接线）", () => {
  it("预检拒绝（如时长超限）→ 显友好提示、不上传、不出预览", async () => {
    const onUpload = vi.fn();
    const validate = vi.fn().mockResolvedValue(copy.errors.videoTooLong);
    render(
      <AvatarVideoPicker value={null} onChange={vi.fn()} uploading={false} onUpload={onUpload} validate={validate} />
    );
    selectFile();
    await waitFor(() => expect(screen.getByText(copy.errors.videoTooLong)).toBeInTheDocument());
    expect(onUpload).not.toHaveBeenCalled();
    expect(screen.queryByLabelText(copy.workbench.videoPreviewAlt)).not.toBeInTheDocument();
  });

  it("预检通过 → 触发 onUpload + 出视频预览", async () => {
    const onUpload = vi.fn();
    const validate = vi.fn().mockResolvedValue(null);
    render(
      <AvatarVideoPicker value={null} onChange={vi.fn()} uploading={false} onUpload={onUpload} validate={validate} />
    );
    selectFile();
    await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(1));
    expect(screen.getByLabelText(copy.workbench.videoPreviewAlt)).toBeInTheDocument();
  });

  it("上传成功(value 有值) → 显「已上传，可生成」；移除 → onChange(null) 且预览消失", async () => {
    const onChange = vi.fn();
    const validate = vi.fn().mockResolvedValue(null);
    const { rerender } = render(
      <AvatarVideoPicker value={null} onChange={onChange} uploading={false} onUpload={vi.fn()} validate={validate} />
    );
    selectFile();
    await waitFor(() => expect(screen.getByLabelText(copy.workbench.videoPreviewAlt)).toBeInTheDocument());
    // 模拟容器上传完成回填 value
    rerender(
      <AvatarVideoPicker value="video-asset-1" onChange={onChange} uploading={false} onUpload={vi.fn()} validate={validate} />
    );
    expect(screen.getByText(copy.workbench.videoReady)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(copy.workbench.removeVideo));
    expect(onChange).toHaveBeenCalledWith(null);
    expect(screen.queryByLabelText(copy.workbench.videoPreviewAlt)).not.toBeInTheDocument();
  });

  it("上传中 → 显「上传中…」", async () => {
    const validate = vi.fn().mockResolvedValue(null);
    render(
      <AvatarVideoPicker value={null} onChange={vi.fn()} uploading onUpload={vi.fn()} validate={validate} />
    );
    selectFile();
    await waitFor(() => expect(screen.getByText(copy.workbench.videoUploading)).toBeInTheDocument());
  });
});
