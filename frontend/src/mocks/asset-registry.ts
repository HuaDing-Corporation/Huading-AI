// MSW 资产注册表（AIBRAIN-UI-0001 · FIX4 · P1-2）——**mock 里资产只能有一个来源**。
//
// 🔴 上一版智脑 mock 为任意 id 凭空伪造图片 → 传 `missing-asset` 也「成功」并扣费，而真 BE 会 404 → 附件 e2e 假绿。
// 参照 #164「两套状态源」的根治形状（收敛成单一 resolveMockState）：这里把「资产是否存在/合法」收敛成**唯一注册表**——
// 上传 mock（/uploads/images 等）登记，发消息 mock 查表。查不到 = 真的不存在，不再自欺。
//
// ══ REVERSE-DEEP-UI-0001-FIX3 · 反推接入本表 ═════════════════════════════════════════════
// 上一轮反推 estimate 自己写了个 `mockAssetExists` **正则**判存在性 —— 那是另起炉灶：合法形状但从未上传的
// `upload-999` 照样拿到 200 报价，而真 BE 会 404。**同一个问题、同一个仓库、同一份先例，却各写各的。**
// 现在反推改为查本表，并补齐 BE 侧真正参与判定的两个维度：
//   · `tenant_id` —— BE `source_asset_or_raise` 会比对租户，不匹配即 404（不是 403，别猜）。
//   · `duration_ms` —— D9 明确「档位由**上传时已落库的 duration_ms** 判定，不由客户端传参决定」。
//     故时长必须在**上传时**登记，estimate 只查表。上一版在 estimate 里按 id 现场猜时长，
//     方向正好反了（把生产端的事实挪到消费端去猜）。

/** mock 的「当前租户」。真 BE 从 token 推；mock 只有一个登录态，故用常量表示「我方」。 */
export const MOCK_TENANT_ID = "ten-mock";

export interface MockAsset {
  asset_id: string;
  /** BE Asset.type（avatar_image / product_image / generated_image / cover / video / …）。 */
  asset_type: string;
  mime_type: string;
  status: string;
  /** 供智脑历史附件缩略图用（presign 的 mock 对应物）。 */
  download_url: string;
  /**
   * 归属租户。缺省 = 当前租户；显式给别的值即「别人的资产」——
   * 这样「跨租户 → 404」在 mock 里**可被真实表达**，而不是加个字段自己跟自己比对。
   */
  tenant_id?: string;
  /** 视频资产的已落库时长（BE Asset.duration_ms）。图片为 null。反推分档只认这个值。 */
  duration_ms?: number | null;
}

const assets = new Map<string, MockAsset>();

/**
 * 基线资产 —— mock 一启动就存在的那几条。
 * 为什么需要：反推历史 seed 里的 job 引用了 `upload-1` / `video-asset-1` 作为 source_asset_id，
 * 而测试与 e2e 常常在「还没走过上传流程」时就对它们发起 estimate。没有基线就会全体 404，
 * 那是**比 BE 更严**（误杀合法请求）—— 与「不比 BE 宽松」同等重要的另一半纪律。
 */
const BASELINE_ASSETS: MockAsset[] = [
  {
    asset_id: "upload-1",
    asset_type: "avatar_image",
    mime_type: "image/png",
    status: "ready",
    download_url: "https://mock.local/u1.jpg",
    duration_ms: null
  },
  {
    asset_id: "video-asset-1",
    asset_type: "video",
    mime_type: "video/mp4",
    status: "ready",
    download_url: "https://mock.local/v1.mp4",
    duration_ms: 3_000 // e2e fixture avatar-sample.mp4 的真时长 → video_short 档
  },
  {
    // 长视频档（61–180s）的固定样本：浏览器上传流程造不出它（mock 的上传端点不知道真实时长），
    // 故以基线资产形式提供，让 API 级测试能覆盖 video_long 档。
    asset_id: "video-asset-long-1",
    asset_type: "video",
    mime_type: "video/mp4",
    status: "ready",
    download_url: "https://mock.local/v-long.mp4",
    duration_ms: 180_000
  },
  {
    // 🔴 **别人租户的资产** —— 专供「跨租户 → 404」承重使用。
    //    它真实存在于表中（所以「查不到」这条路径不成立），只是不属于当前租户，
    //    从而能把 BE `source.tenant_id != tenant_id → 404` 这条逻辑真正测到。
    asset_id: "upload-other-tenant-1",
    asset_type: "avatar_image",
    mime_type: "image/png",
    status: "ready",
    download_url: "https://mock.local/other.jpg",
    tenant_id: "ten-someone-else",
    duration_ms: null
  }
];

const seedBaseline = (): void => {
  for (const asset of BASELINE_ASSETS) assets.set(asset.asset_id, asset);
};
seedBaseline();

/** 上传 mock 成功时登记（唯一写入口）。 */
export function registerMockAsset(asset: MockAsset): void {
  assets.set(asset.asset_id, asset);
}

/** 发消息 mock 查表——查不到即真的不存在（返 undefined）。**不做租户过滤**（智脑既有调用方语义不变）。 */
export function getMockAsset(id: string): MockAsset | undefined {
  return assets.get(id);
}

/**
 * 按租户取 —— 镜像 BE `source_asset_or_raise` 的租户比对：
 * **不存在**与**属于别的租户**在 BE 侧是同一个结果（404 REVERSE_PROMPT_SOURCE_NOT_FOUND，不泄露存在性），
 * 故此处也统一返回 undefined，由调用方转 404。
 */
export function getMockAssetForTenant(id: string, tenantId: string = MOCK_TENANT_ID): MockAsset | undefined {
  const asset = assets.get(id);
  if (!asset) return undefined;
  return (asset.tenant_id ?? MOCK_TENANT_ID) === tenantId ? asset : undefined;
}

/** 测试重置 —— 清掉上传登记，回到只有基线资产的初态。 */
export function resetMockAssets(): void {
  assets.clear();
  seedBaseline();
}
