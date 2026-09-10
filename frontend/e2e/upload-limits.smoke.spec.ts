import { expect, test, type Page } from "@playwright/test";

// Real production UI and multipart client; only the transport response is controlled.
// No generation/estimate/paid request is triggered. Byte literals are independent of
// the production limit, so reverting the cap to 10 MiB breaks both consumers.
declare global {
  interface Window {
    __uploadLimitQA: {
      mode: "success" | "413" | "415" | "422";
      sent: { path: string; bytes: number }[];
    };
  }
}

async function login(page: Page) {
  await page.goto("/login");
  await page.waitForFunction(() => !!navigator.serviceWorker?.controller);
  const inputs = page.locator("form input");
  await inputs.nth(0).fill("huading");
  await inputs.nth(1).fill("qa@huading.test");
  await inputs.nth(2).fill("pw123456");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "生成视频", exact: true })).toBeVisible();
  await page.evaluate(() => {
    const nativeFetch = window.fetch.bind(window);
    window.__uploadLimitQA = { mode: "success", sent: [] };
    window.fetch = async (input, init) => {
      const path = new URL(input instanceof Request ? input.url : String(input), location.href).pathname;
      if (init?.body instanceof FormData && ["/api/v1/uploads", "/api/v1/uploads/images"].includes(path)) {
        const file = init.body.get("file");
        if (file instanceof File) window.__uploadLimitQA.sent.push({ path, bytes: file.size });
        const mode = window.__uploadLimitQA.mode;
        if (mode === "413") return new Response("<h1>413 Request Entity Too Large</h1>", { status: 413 });
        if (mode === "415" || mode === "422") {
          return Response.json({ data: null, error: { code: "UPLOAD_INVALID", message: "图片内容无法解析", detail: null }, request_id: "qa" }, { status: Number(mode) });
        }
      }
      return nativeFetch(input, init);
    };
  });
}

async function imageOfSize(page: Page, bytes: number) {
  const encoded = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 16;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "#be9c5d";
    context.fillRect(0, 0, 16, 16);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const png = Buffer.from(encoded, "base64");
  return { name: `local-${bytes}.png`, mimeType: "image/png", buffer: Buffer.concat([png, Buffer.alloc(bytes - png.length)]) };
}

for (const width of [1280, 360]) {
  test(`upload limits ${width}px: Asset/key 20–30 MiB, +1 byte blocked, 413 distinct from 415/422`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error" && !/Failed to load resource|net::ERR_/.test(message.text())) errors.push(message.text());
    });
    await page.setViewportSize({ width, height: 900 });
    await login(page);

    for (const kind of ["asset", "key"] as const) {
      if (kind === "key") await page.getByRole("button", { name: "图片生成 / 修改", exact: true }).click();
      const panel = page.getByTestId(kind === "asset" ? "panel-avatar_talk" : "panel-photo");
      const input = panel.locator(kind === "asset" ? "#avatar-image" : "#photo-ref");
      const remove = panel.getByRole("button", { name: /移除/ });
      for (const bytes of [20971520, 31457280]) {
        await input.setInputFiles(await imageOfSize(page, bytes));
        if (kind === "asset") await expect(panel.getByText("已上传，可生成")).toBeVisible();
        else await expect(panel.getByRole("button", { name: /上传参考图（1\/1）/ })).toBeVisible();
        expect(await page.evaluate(() => window.__uploadLimitQA.sent.at(-1))).toEqual({
          path: kind === "asset" ? "/api/v1/uploads/images" : "/api/v1/uploads", bytes
        });
        await remove.click();
      }
      const before = await page.evaluate(() => window.__uploadLimitQA.sent.length);
      await input.setInputFiles(await imageOfSize(page, 31457281));
      await expect(panel.getByRole("alert")).toContainText(/30\s*MB/);
      expect(await page.evaluate(() => window.__uploadLimitQA.sent.length)).toBe(before);

      for (const mode of ["413", "415", "422"] as const) {
        await page.evaluate((next) => { window.__uploadLimitQA.mode = next; }, mode);
        await input.setInputFiles(await imageOfSize(page, 20971520));
        const alert = panel.getByRole("alert");
        if (mode === "413") await expect(alert).toContainText(/大小|过大|体积|上限/);
        else {
          await expect(alert).toContainText("图片内容无法解析");
          await expect(alert).not.toContainText(/30\s*MB|文件过大/);
        }
        expect(await page.evaluate(() => window.__uploadLimitQA.sent.length)).toBe(before + ["413", "415", "422"].indexOf(mode) + 1);
        if (kind === "asset") await remove.click();
      }
      await page.evaluate(() => { window.__uploadLimitQA.mode = "success"; });
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    expect(errors).toEqual([]);
  });
}
