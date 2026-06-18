export const copy = {
  workbench: {
    topicLabel: "视频主题",
    topicPlaceholder: "输入一句话主题，如：三分钟看懂咖啡的起源",
    scriptLabel: "AI 文案（可编辑）",
    regenerate: "重写文案",
    avatarLabel: "数字人形象",
    upload: "上传形象图（JPG / PNG / WebP，≤10MB）",
    voiceLabel: "音色",
    more: "更多设置",
    speed: "语速",
    aspectLocked: "9:16 竖屏（锁定）",
    subtitleLocked: "字幕已开启（锁定）",
    generate: "生成视频",
    generating: "提交中…",
    scriptTooLong: (s: number) => `预计 ${s}s，将截断到 60s，建议精简文案`
  },
  errors: {
    quota: "额度不足，无法生成，请充值或精简任务",
    uploadTooLarge: "图片过大，请控制在 10MB 以内",
    uploadType: "仅支持 JPG / PNG / WebP 图片",
    network: "网络连接失败，请检查后端服务是否在线",
    generic: "操作失败，请重试"
  },
  tasks: {
    empty: "暂无任务，输入主题开始生成。",
    retry: "重试",
    retryUnavailable: "请到工作台重新发起",
    open: "查看详情"
  },
  detail: { notFound: "视频不存在或无权访问", back: "返回", download: "下载 MP4" },
  status: { queued: "排队中", failed: "失败", done: "已完成" }
} as const;
