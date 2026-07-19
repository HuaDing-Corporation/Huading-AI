// MSW 资产注册表（AIBRAIN-UI-0001 · FIX4 · P1-2）——**mock 里资产只能有一个来源**。
//
// 🔴 上一版智脑 mock 为任意 id 凭空伪造图片 → 传 `missing-asset` 也「成功」并扣费，而真 BE 会 404 → 附件 e2e 假绿。
// 参照 #164「两套状态源」的根治形状（收敛成单一 resolveMockState）：这里把「资产是否存在/合法」收敛成**唯一注册表**——
// 上传 mock（/uploads/images 等）登记，发消息 mock 查表。查不到 = 真的不存在，不再自欺。

export interface MockAsset {
  asset_id: string;
  /** BE Asset.type（avatar_image / product_image / generated_image / cover / …）。 */
  asset_type: string;
  mime_type: string;
  status: string;
  /** 供智脑历史附件缩略图用（presign 的 mock 对应物）。 */
  download_url: string;
}

const assets = new Map<string, MockAsset>();

/** 上传 mock 成功时登记（唯一写入口）。 */
export function registerMockAsset(asset: MockAsset): void {
  assets.set(asset.asset_id, asset);
}

/** 发消息 mock 查表——查不到即真的不存在（返 undefined）。 */
export function getMockAsset(id: string): MockAsset | undefined {
  return assets.get(id);
}

/** 测试重置。 */
export function resetMockAssets(): void {
  assets.clear();
}
