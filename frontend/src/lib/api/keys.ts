export const videoKeys = {
  all: ["videos"] as const,
  list: () => [...videoKeys.all, "list"] as const,
  detail: (id: string) => [...videoKeys.all, "detail", id] as const
};

export const meKey = ["me"] as const;
export const quotaKey = ["quota"] as const;
