import { type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api/client";
import { useUploadProductImage } from "@/lib/api/hooks";
import { uploadProductImage } from "@/lib/api/uploads";
import { useTrackedUpload } from "@/lib/api/use-tracked-upload";
import { AuthProvider } from "@/lib/auth/auth-context";
import { authStore } from "@/lib/auth/store";
import { copy } from "@/lib/copy";
import { VideoTasksProvider } from "@/lib/videos/tasks-context";
import { EcomImageCutoutForm } from "./ecom-image-cutout-form";
import { ImagePicker } from "./image-picker";
import { ReferenceImagesPicker } from "./reference-images-picker";

// Real pickers, hooks and upload transports. Only fetch and browser object URLs
// are replaced. A smaller cap, >= comparison, missing gate, or File replacement
// must fail these tests; expectations do not derive from production constants.
function ProductPicker() {
  const upload = useUploadProductImage();
  const source = useTrackedUpload(upload.mutateAsync, (r) => r.image_key);
  return <ImagePicker value={source.value} onChange={source.setValue} uploading={upload.isPending}
    onUpload={source.onUpload} uploadError={source.error} />;
}

const consumers = [
  { name: "ImagePicker product key", path: "/api/v1/uploads", element: () => <ProductPicker /> },
  { name: "ReferenceImagesPicker Asset", path: "/api/v1/uploads/images", element: () => <ReferenceImagesPicker /> },
  { name: "ReferenceImagesPicker product key", path: "/api/v1/uploads", element: () =>
    <ReferenceImagesPicker uploadFile={async (file) => (await uploadProductImage(file)).image_key} /> },
  { name: "EcomImageTool direct-constant batch", path: "/api/v1/uploads/images", element: () => <EcomImageCutoutForm />, batch: true }
];

let client: QueryClient;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  localStorage.clear();
  authStore.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL() { return "blob:upload-limits"; }
    static revokeObjectURL() {}
  });
  let id = 0;
  fetchMock = vi.fn(async (url: string, init: RequestInit) => {
    if (![`${API_BASE_URL}/api/v1/uploads`, `${API_BASE_URL}/api/v1/uploads/images`].includes(url)) {
      throw new Error(`Unexpected request: ${url}`);
    }
    const file = (init.body as FormData).get("file") as File;
    const data = url === `${API_BASE_URL}/api/v1/uploads`
      ? { key: `uploads/image-${++id}.png`, uri: "s3://test/image.png", content_type: file.type, size: file.size }
      : { asset_id: `asset-${++id}`, type: "avatar_image", status: "ready" };
    return new Response(JSON.stringify({ data, error: null, request_id: "test-upload" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mount(element: ReactNode, batch?: boolean) {
  const result = render(<QueryClientProvider client={client}><AuthProvider><VideoTasksProvider>
    {element}
  </VideoTasksProvider></AuthProvider></QueryClientProvider>);
  if (batch) fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomModeBatch }));
  return result.container.querySelector('input[type="file"]') as HTMLInputElement;
}

describe.each(consumers)("$name upload boundaries", ({ path, element, batch }) => {
  it.each([10485761, 20971520, 31457280])("uploads the original %i-byte File", async (bytes) => {
    const input = mount(element(), batch);
    const file = new File([new Uint8Array(bytes)], "original.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await act(async () => {});
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE_URL}${path}`);
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.body.get("file")).toBe(file);
    expect(init.body.get("file").size).toBe(bytes);
    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("rejects 31457281 bytes before any upload", async () => {
    const input = mount(element(), batch);
    const file = new File([new Uint8Array(31457281)], "oversize.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    await act(async () => {});
    expect(screen.getByRole("alert")).toHaveTextContent(/30MB/);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("still rejects a disguised non-image before upload", async () => {
    const input = mount(element(), batch);
    fireEvent.change(input, { target: { files: [new File(["x"], "fake.png", { type: "image/gif" })] } });
    await act(async () => {});
    expect(screen.getByRole("alert")).toHaveTextContent(copy.errors.uploadType);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows gateway 413 guidance even for a small accepted file", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<h1>413 Request Entity Too Large</h1>", { status: 413 }));
    const input = mount(element(), batch);
    fireEvent.change(input, { target: { files: [new File(["x"], "small.png", { type: "image/png" })] } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/上传请求.*服务或网关.*大小限制/);
    expect(screen.getByRole("alert")).toHaveTextContent(/即使.*仍可能/);
    await act(async () => {});
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
