export const videoKeys = {
  all: ["videos"] as const,
  list: () => [...videoKeys.all, "list"] as const,
  detail: (id: string) => [...videoKeys.all, "detail", id] as const,
  history: (mode: string) => [...videoKeys.all, "history", mode] as const
};

export const copyKeys = {
  all: ["copy"] as const,
  drafts: () => [...copyKeys.all, "drafts"] as const
};

export const meKey = ["me"] as const;
export const quotaKey = ["quota"] as const;
export const voicesKey = ["voices"] as const;
export const avatarPresetsKey = ["avatars", "presets"] as const;
