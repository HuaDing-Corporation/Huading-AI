// 华鼎AI智脑 · react-query 键工厂（AIBRAIN-UI-0001）。沿用项目 { all, list(), detail(id) } 惯例。

export const aibrainKeys = {
  all: ["aibrain"] as const,
  conversations: () => [...aibrainKeys.all, "conversations"] as const,
  conversation: (id: string) => [...aibrainKeys.all, "conversation", id] as const,
  wallet: () => [...aibrainKeys.all, "wallet"] as const
};
