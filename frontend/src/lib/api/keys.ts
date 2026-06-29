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

export const meKey = ["me"] as const;
export const quotaKey = ["quota"] as const;
export const voicesKey = ["voices"] as const;
export const avatarPresetsKey = ["avatars", "presets"] as const;
