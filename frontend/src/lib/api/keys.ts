export const videoKeys = {
  all: ["videos"] as const,
  list: () => [...videoKeys.all, "list"] as const,
  detail: (id: string) => [...videoKeys.all, "detail", id] as const,
  history: (mode: string, kind?: string) => [...videoKeys.all, "history", mode, kind ?? null] as const
};

// 口播生产力增强 (ORAL-PROD-UI-0001)
export const subtitleTemplatesKey = ["oral", "subtitle-templates"] as const;
export const coverKeys = {
  all: ["covers"] as const,
  frameCandidates: (videoTaskId: string, count: number) => [...coverKeys.all, "frames", videoTaskId, count] as const
};

export const copyKeys = {
  all: ["copy"] as const,
  drafts: () => [...copyKeys.all, "drafts"] as const
};

// 电商图扩展 Phase2 (ECOM-MODEL-UI-0001) — AI 模特风格预设
export const ecomModelStylesKey = ["ecom-images", "model-styles"] as const;
// 电商图扩展 Phase3 (ECOM-POSTER-UI-0001) — 营销海报版式预设
export const ecomPosterTemplatesKey = ["ecom-images", "poster-templates"] as const;

// 品牌音色 / 声音克隆 (BRAND-VOICE-UI-0001)
export const brandVoiceKeys = {
  all: ["brand-voices"] as const,
  list: () => [...brandVoiceKeys.all, "list"] as const
};

// 深度合成标识设置 (LABEL-UI-0001)
export const labelSettingsKey = ["tenant", "label-settings"] as const;

// 发布中心 (PUBLISH-UI-0001)
export const publishKeys = {
  all: ["publish"] as const,
  platforms: () => [...publishKeys.all, "platforms"] as const,
  records: () => [...publishKeys.all, "records"] as const
};

// 视频生成 · 配乐库 (VIDEOGEN-UI-0001)
export const bgmLibraryKey = ["bgm-library"] as const;

// 批量生产中心 (BATCH-PROD-UI-0001)
export const batchKeys = {
  all: ["batches"] as const,
  list: () => [...batchKeys.all, "list"] as const,
  detail: (id: string) => [...batchKeys.all, "detail", id] as const
};

// 管理员数据看板 (ANALYTICS-UI-0001) — 以日期区间 + 查询参数为 key，区间变化即联动刷新。
export const analyticsKeys = {
  all: ["analytics"] as const,
  overview: (from: string, to: string) => [...analyticsKeys.all, "overview", from, to] as const,
  byTenant: (from: string, to: string, sort: string, limit: number, offset: number) =>
    [...analyticsKeys.all, "by-tenant", from, to, sort, limit, offset] as const,
  byProvider: (from: string, to: string) => [...analyticsKeys.all, "by-provider", from, to] as const,
  timeseries: (from: string, to: string, granularity: string) =>
    [...analyticsKeys.all, "timeseries", from, to, granularity] as const
};

// 图片历史·统一模块 (HISTORY-UI-0001) — 按 category 分 key，切 tab 各自缓存不串数据。
export const historyImageKeys = {
  all: ["history-images"] as const,
  list: (category: string) => [...historyImageKeys.all, "list", category] as const,
  detail: (category: string, id: string) => [...historyImageKeys.all, "detail", category, id] as const
};

export const meKey = ["me"] as const;
export const quotaKey = ["quota"] as const;
export const voicesKey = ["voices"] as const;
export const avatarPresetsKey = ["avatars", "presets"] as const;
