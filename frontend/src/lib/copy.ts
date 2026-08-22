export const copy = {
  // copy/copied 由共享的 CopyableBlock 消费（HISTORY-FULL-PROMPT-UI-0001）。原先它们是 copy.reverse.copy/copied ——
  // 组件提升到 components/ui 后，一个 ui 基元依赖 copy.reverse.* 是命名说谎（它跟反推没关系）→ 归入 common。
  // 原 reverse.copy/copied 搬走后**零消费者**，已删：值相同的重复 key 就是陷阱（COPY-DRAFT 那条教训）。
  // close：LANDING-CONTACT-UI-0001 加入 —— 联系弹窗的关闭钮曾借用 copy.historyImages.close，
  // 一个与图片历史无关的域消费别人域的文案 = 跨域隐性耦合（改历史域文案会静默改联系弹窗的可及名），归入 common。
  common: { cancel: "取消", close: "关闭", processing: "处理中…", copy: "复制", copied: "已复制" },
  // 侧边栏导航（BATCH-PROD-UI-0001-FIX3）
  nav: { comingSoon: "即将上线" },
  // 板块「即将上线」占位（UI-COMINGSOON-TENANT-RENAME-0001）—— 导航后缀 + 统一友好占位页
  comingSoon: {
    navSuffix: "（即将上线）",
    title: "该功能即将上线",
    desc: "敬请期待，我们正在加紧打造这一功能。",
    back: "返回工作台"
  },
  // 登录 / 注册（AUTH-UI-0001）—— 暖金玻璃；「用户名」口径与后端 tenant_slug 对齐
  auth: {
    // 登录
    loginTitle: "登录控制台",
    loginSubmit: "登录",
    loginSubmitting: "登录中…",
    loginNoAccount: "没有账号？",
    loginToRegister: "去注册",
    loginFailed: "登录失败，请重试。",
    // 注册
    registerTitle: "创建账号",
    registerSubtitle: "注册即可体验全部模块",
    registerSubmit: "注册",
    registerSubmitting: "注册中…",
    registerHasAccount: "已有账号？",
    registerToLogin: "去登录",
    // 字段（登录/注册共用）
    usernameLabel: "用户名",
    usernamePlaceholder: "huading",
    usernameHint: "小写字母、数字、连字符，2–80 位",
    teamNameLabel: "团队 / 公司名称",
    teamNamePlaceholder: "华鼎科技",
    emailLabel: "邮箱",
    emailPlaceholder: "you@example.com",
    passwordLabel: "密码",
    passwordHint: "至少 8 位",
    fullNameLabel: "姓名（选填）",
    fullNamePlaceholder: "你的姓名",
    // 客户端校验（friendly 中文；对齐 BE 规则，提交前拦截）
    errUsername: "用户名只能用小写字母、数字、连字符，且需 2–80 位",
    errTeamName: "请填写团队 / 公司名称（不超过 200 字）",
    errEmail: "请输入有效邮箱地址",
    errPassword: "密码需 8–128 位",
    errFullName: "姓名不超过 200 字",
    // 服务端错误映射（不泄露原始异常串）
    errSlugTaken: "该用户名已被占用，请换一个",
    errRegisterFailed: "注册失败，请稍后重试。"
  },
  // 管理员数据看板（ANALYTICS-UI-0001）
  analytics: {
    pageTitle: "数据看板",
    // PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001：副标题据 entitlement 分「全站」（平台方 analytics_platform）与
    // 「我的用量」（VIP 客户 analytics_view，只看自己数据）。去掉误导的「管理员专属」措辞（门禁走 permissions，非 role）。
    pageSubtitle: "全站用量、成本与趋势总览",
    pageSubtitleOwn: "我的用量、成本与趋势总览",
    // 日期区间
    rangeLabel: "日期区间",
    rangeFrom: "起",
    rangeTo: "止",
    preset7: "近 7 天",
    preset30: "近 30 天",
    preset90: "近 90 天",
    rangeInvalid: "起始日期不得晚于结束日期",
    // 概览卡
    ovCredits: "总消耗积分",
    ovCost: "总成本",
    ovTasks: "任务量",
    ovTasksHint: "成功 + 失败",
    ovSuccess: "成功",
    ovFailed: "失败",
    ovTenants: "用户数",
    ovReserved: "预留积分",
    ovReservedHintAll: (n: number) => `全 ${n} 用户合计 · 与已消耗分列`,
    ovReservedHintTop: (n: number) => `消耗榜前 ${n} 用户 · 与已消耗分列`,
    // 用户排行表（数据模型仍为 tenant，仅展示文案改「用户」）
    tenantTitle: "用户排行",
    colTenant: "用户",
    colCredits: "消耗积分",
    colCost: "成本",
    colTasks: "任务量",
    colSuccessRate: "成功率",
    colBalance: "余额（总 / 已用 / 预留 / 剩余）",
    balTotal: "总",
    balUsed: "已用",
    balReserved: "预留",
    balRemaining: "剩余",
    sortAsc: "升序",
    sortDesc: "降序",
    // 分页
    pageRange: (from: number, to: number, total: number) => `第 ${from}–${to} / 共 ${total}`,
    prevPage: "上一页",
    nextPage: "下一页",
    // 按功能/模型
    providerTitle: "按功能 / 模型拆分",
    colProvider: "功能 / 模型",
    colBillingCount: "计费笔数", // ⚠️ 非「任务量」：count(UsageRecord)，含文案/图片等无 VideoTask 的记录
    billingCountHint: "计费笔数 = 计费记录条数（覆盖文案 / 图片等无视频任务的功能），非视频任务数",
    colShare: "占比",
    // 趋势图
    trendTitle: "趋势",
    granDay: "按日",
    granWeek: "按周",
    metricCredits: "积分",
    metricCost: "成本",
    metricTasks: "笔数",
    trendSummary: (metric: string, n: number) => `${metric}趋势，共 ${n} 个数据点`,
    // 通用态
    loading: "加载中…",
    error: "加载失败，请重试",
    retry: "重试",
    empty: "该区间暂无数据",
    // VIP 门禁友好页（ADMIN-VIP-GATE-UI-0001）：非管理员且非 huading plan → 403 code=ANALYTICS_PLAN_REQUIRED。
    // 与「即将上线」占位页视觉/文案区分——这是「权限不足·VIP 专享」，不是「即将上线」。
    planRequiredTitle: "仅 huading plan 用户可查看",
    planRequiredDesc: "数据看板为 huading plan 专属。开通后可查看全站用量、成本与趋势总览；如需开通请联系我们。",
    planRequiredBack: "返回工作台"
  },
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
    generatePhoto: "生成图片",
    scriptTooLong: (s: number) => `预计 ${s}s，将截断到 60s，建议精简文案`,
    // 生成模式切换（数字人口播 / 电商带货）
    modeGroupLabel: "生成模式",
    modeAvatar: "数字人口播",
    modeEcom: "电商带货",
    aspectBadge: "竖屏 9:16",
    avatarPreviewAlt: "形象预览",
    // 数字人形象来源：照片 / 本人出镜视频二选一（AVATAR-VIDEO-SOURCE-UI-0001）
    avatarSourceLabel: "数字人形象来源",
    sourcePhoto: "照片",
    sourceVideo: "本人出镜视频",
    videoUpload: "上传 MP4 本人出镜视频（3–10 秒）",
    videoHint: "MP4，3–10 秒，360p–1080p，单人正脸出镜效果最佳",
    videoPreviewAlt: "视频预览",
    removeVideo: "移除视频",
    videoValidating: "校验视频中…",
    videoReady: "已上传，可生成",
    videoUploading: "上传中…",
    videoUploadFailed: "上传失败",
    // 电商带货（图生视频 i2v）表单
    ecomTitle: "电商带货视频",
    ecomSubtitle: "上传产品图、输入卖点，AI 一键生成带货短片",
    ecomTopicLabel: "产品卖点 / 主题",
    ecomTopicPlaceholder: "输入产品卖点，如：316 不锈钢保温杯，24 小时持续锁温",
    ecomImageRequired: "请上传产品图后再生成",
    // 电商带货：视频时长选择器
    durationLabel: "视频时长",
    durationLabelAligned: "视频时长（与文案、字幕一致）",
    durationSeconds: (s: number) => `${s} 秒`,
    durationCustom: "自定义",
    durationCustomLabel: "自定义时长（秒）",
    // VIDEO-GEN-PARAMS-UI-0001：随 per-call 区间（电商 5–120 / 视频生成 4–15）。FIX1：BE duration_sec 是 int，5.5/5.4 等小数不合法（friendly 提示含「整数」）
    durationCustomPlaceholder: (min: number, max: number) => `${min}–${max}`,
    durationRange: (min: number, max: number) => `请输入 ${min}–${max} 的整数秒`,
    durationHint: "时长越长，生成越慢、消耗额度越多",
    // 文案/画面解耦（电商带货）
    ecomScriptLabel: "AI 口播文案（仅配音）",
    scenePromptLabel: "画面提示词",
    scenePromptGenerate: "AI 生成画面",
    scenePromptPlaceholder: "描述想要的产品画面 / 场景 / 镜头，可点「AI 生成画面」自动生成，也可手动编辑",
    scenePromptHint: "画面与口播已解耦：此处只描述画面，不影响配音文案",
    // ── 电商带货视频优化（ECOM-VIDEO-OPTIMIZE-UI-0001）──
    // 「AI生成文案」：ScriptReview 共享组件的 actionLabel 覆盖（口播不传 → 仍「重写文案」，不改共享 regenerate key）。
    ecomScriptGenerate: "AI生成文案",
    // 文案字数档位（短/中/长，默认中）
    scriptLengthLabel: "文案长度",
    scriptLengthShort: "短",
    scriptLengthMedium: "中",
    scriptLengthLong: "长",
    scriptLengthHint: "档位决定 AI 生成文案的目标字数（短/中/长），与所选时长共同影响长度",
    // 负面提示词（可选、无字数限制；「AI生成画面」返回值自动填入）
    negativePromptLabel: "负面提示词（可选）",
    negativePromptPlaceholder: "不希望出现的元素，如：水印、多余文字、杂乱背景、变形。留空即可；点「AI 生成画面」会自动填入建议，可再改",
    negativePromptHint: "描述要避免的画面元素；生成时作为负面约束（BE 转发视频模型）",
    // 「AI生成画面」需产品图（前端友好拦，BE 会 422）
    sceneNeedProductImage: "请先上传产品图，再生成画面",
    // 产品图张数选择器（1–5 档 + 自定义，上限 9）
    productImageCountLabel: "产品图张数",
    productImageCount: (n: number) => `${n} 张`,
    productImageCountCustom: "自定义",
    productImageCountCustomLabel: "自定义张数",
    productImageCountPlaceholder: (max: number) => `1–${max}`, // IMAGE-GEN-OPTIMIZE-UI-0001：随 per-call max（电商 9 / 图片生成 6），与 range 错误一致
    productImageCountRange: (max: number) => `请输入 1–${max} 张`, // IMAGE-GEN-OPTIMIZE-UI-0001：上限 per-call（电商 9 / 图片生成 6）
    productImageCountHint: "选择要用几张产品图；多图会分配到不同分镜。切换张数不会自动删图",
    // 产品图多图 picker（复用 ReferenceImagesPicker）
    productImagesLabel: "产品图（必填，至少 1 张）",
    productImagesUpload: "上传产品图",
    productImagesOverLimit: "超过所选张数，多余产品图未添加",
    // 已上传数 > 所选张数时的表单级拦截（不静默丢图，明确让用户删减）
    productImagesExceed: (uploaded: number, allowed: number) =>
      `已上传 ${uploaded} 张，超过所选 ${allowed} 张，请删除多余产品图或调高张数`,
    // 图片生成 / 修改（mode 值仍为 photo，仅显示文案）
    modePhoto: "图片生成 / 修改",
    photoTitle: "图片生成 / 修改",
    photoSubtitle: "一句话生成图片，或上传参考图换背景 / 修图",
    photoPromptLabel: "提示词",
    photoPromptPlaceholder: "描述想要的图片，如：白色大理石台面上的香水瓶，柔光氛围，高级质感",
    photoPromptRequired: "请先输入提示词",
    photoRefLabel: "参考图（可选）",
    photoRefUpload: "上传参考图（JPG / PNG / WebP，≤10MB）",
    photoRefPreviewAlt: "参考图预览",
    photoRefHint: "上传参考图做换背景 / 修图；留空则纯文生图",
    photoResultAlt: "生成的图片",
    // ── 图片生成/修改 优化（IMAGE-GEN-OPTIMIZE-UI-0001）——只追加，勿重排（与智脑线共享 copy.ts）──
    // 参考图：单张 → 1–6 张（复用张数选择器 + 多图 picker）
    photoRefCountLabel: "参考图张数",
    photoRefCountHint: "选择要用几张参考图（留空可纯文生图）；多图共同参考生成一张。切换张数不会自动删图",
    photoRefImagesLabel: "参考图（可选，最多 6 张）",
    photoRefImagesUpload: "上传参考图",
    photoRefImagesOverLimit: "超过所选张数，多余参考图未添加",
    photoRefImagesExceed: (uploaded: number, allowed: number) =>
      `已上传 ${uploaded} 张，超过所选 ${allowed} 张，请删除多余参考图或调高张数`,
    // 三个强度滑块（诚实文案：软性倾向、编码进提示词，非 provider 原生精确参数）；背景参考强度已于 2026-07-19 砍除
    strengthGroupLabel: "生成强度（可选）",
    strengthGroupHint: "以下为软性倾向控制——底层编码进提示词、并非精确参数；默认关闭，开启后才生效并参与生成",
    strengthOff: "未开启",
    strengthToggleSuffix: "开关",
    strengthSimilarity: "图片相似度",
    strengthSimilarityHint: "越高越倾向贴近参考图的整体风格与构图（软性倾向）",
    strengthCreativity: "AI 创意程度",
    strengthCreativityHint: "越高 AI 发挥空间越大、越可能偏离参考图（软性倾向）",
    strengthSubject: "主体保持强度",
    strengthSubjectHint: "越高越倾向保留参考图主体的特征（软性倾向，非精确锁定）",
    // 四层提示词（总控类可折叠、默认收起）。长度：每层各 ≤20000 字符（BE schema 校验，超限 422、非静默截断；前端不设 maxLength 仅因正常使用远不及）
    photoMasterGroupLabel: "任务总控（可选 · 全局风格）",
    masterPromptLabel: "任务总控提示词（可选）",
    masterPromptPlaceholder: "全局风格前缀，如：统一暖色胶片质感、柔光——会拼进本次图片提示词",
    masterNegativeLabel: "任务统一负面提示词（可选）",
    masterNegativePlaceholder: "本次统一想避免的元素（软性约束，非硬性禁止），如：文字、水印",
    imageNegativeLabel: "图片负面提示词（可选）",
    imageNegativePlaceholder: "这张图想尽量避免出现的元素（软性约束，非硬性禁止），如：多余的手、畸变",
    // 清晰度档位 1K/2K/4K（§3之二）——诚实文案：是「更大尺寸」不是「变清晰」；暂不提价格差异（本期三档同价）。
    imageResolutionLabel: "清晰度档位",
    imageResolutionHint: "决定输出尺寸大小（与画面比例共同决定像素）：档位越高图越大越细腻，但模型没画出的细节不会凭空出现——不是「一键提升画质」",
    // 画面比例（IMAGE-ASPECT-RATIO-UI-0001）——替代旧「尺寸 / 质量」下拉；8 定比 + 自适应，默认 1:1
    aspectLabel: "画面比例",
    aspectAuto: "自适应",
    aspectAutoHint: "自适应：有输入图按输入图比例输出，无输入图默认 1:1",
    // 文案仿写 + 标题/话题生成（mode: copywriting）
    modeCopywriting: "文案仿写",
    copyTitle: "文案仿写",
    copySubtitle: "粘贴参考文案，AI 改写并生成标题、话题",
    copySourceLabel: "参考文案",
    copySourcePlaceholder: "粘贴你有权使用的参考文案，AI 将据此改写",
    copySourceRequired: "请先粘贴参考文案",
    copyModeLabel: "改写模式",
    copyModeSmart: "智能",
    copyModeCustom: "自定义",
    copyModeAuto: "自动多版",
    copyInstructionLabel: "自定义指令",
    copyInstructionPlaceholder: "如：更口语、更简短、换高端风格",
    copyInstructionRequired: "请填写自定义改写指令",
    copyCountLabel: "候选数量",
    copyPlatformLabel: "目标平台（可选）",
    copyPlatformAny: "不限",
    copyPlatformDouyin: "抖音",
    copyPlatformXiaohongshu: "小红书",
    copyCompliance: "请确保你有权使用与改写所参考的文案",
    copyGenerate: "生成文案",
    copyGenerating: "生成中…",
    copyResultLabel: "改写文案（可编辑）",
    copyCandidatesLabel: "候选文案（点击选用）",
    copyTitlesLabel: "标题候选（点击复制）",
    copyTopicsLabel: "话题候选（点击复制）",
    copyEmptyTitles: "本次未生成标题候选",
    copyEmptyTopics: "本次未生成话题候选",
    // ── 计费披露 + 部分失败 ─────────────────────────────────────────────────────────────
    // estimate 成功且 total/breakdown 自洽时使用 copyPriceEstimate；未返回、失败或契约损坏时
    // 使用本条无金额回退。回退只说明三个端点各计一次，不复制可变费率，也不猜本次金额。
    // 全局披露不承诺失败项未计费；逐项失败的资金措辞由 copyFailureBilling 按服务端 outcome 分流。
    copyPriceDisclosure:
      "计费：「生成文案」会同时生成改写 / 标题 / 话题三项，按三次计费（每项各计一次）。实际单价以你的套餐费率为准。",
    copyPriceEstimate: (credits: string) =>
      `预计本次生成约 ${credits} 积分，包含文案改写、标题和话题三项；最终以服务端实际结算为准。`,
    /**
     * 部分失败必须看得见——否则用户只会看到「少了话题」而不知为何。
     * 🔴 **两句措辞，按前端到底知不知道分流**（`copyFailureBilling`）：
     *   `Released`：拿到了服务端自己生成的 failed outcome → 服务端明确失败，可据实说未计费
     *   `Unknown` ：没拿到响应 / 没有 outcome（网络中断、网关超时、反代 5xx）
     *               → **一个字的资金承诺都不给**，只说没完成 + 指向账单这个权威来源
     */
    copyPartFailedReleased: (part: string, reason: string) => `${part}生成失败（该项未计费）：${reason}`,
    copyPartFailedUnknown: (part: string, reason: string) =>
      `${part}没有完成：${reason}。这次请求没能拿到服务端的结果，是否计费请以用量记录为准。`,
    copyPartTitles: "标题",
    copyPartTopics: "话题",
    copyCopy: "复制",
    copyCopied: "已复制",
    copySave: "保存到历史",
    copySaving: "保存中…",
    copySaved: "已保存",
    copyUseInAvatar: "用此文案 · 数字人口播",
    copyUseInEcom: "用此文案 · 电商带货",
    // 字幕样式（口播生产力增强 ORAL-PROD-UI-0001）；不选 = 默认烧入（不回归 0001）
    subtitleStyleLabel: "字幕样式（可选）",
    subtitleStyleHint: "不选用默认烧入样式；选预设后可微调字号 / 颜色 / 位置",
    subtitleStyleNone: "跟随默认",
    subtitleFontSizeLabel: "字号",
    subtitleColorLabel: "颜色",
    subtitlePositionLabel: "位置",
    subtitlePositionTop: "顶部",
    subtitlePositionCenter: "居中",
    subtitlePositionBottom: "底部",
    subtitleFontSizeRange: "字号需在 16–96",
    subtitlePreviewSample: "字幕预览示例文本",
    // 电商图 / 白底图抠图 (ECOM-IMG-UI-0001)
    modeEcomImage: "电商图",
    ecomCutoutTitle: "白底图 / 抠图",
    ecomCutoutSubtitle: "上传商品图，抠出商品换白底或透明底；支持批量",
    ecomModeSingle: "单张",
    ecomModeBatch: "批量",
    ecomBgLabel: "背景",
    ecomBgWhite: "白底",
    ecomBgTransparent: "透明底",
    ecomUploadLabel: "商品图",
    ecomUploadBatchLabel: "商品图（可多选，最多 20 张）",
    ecomUploadRequired: "请先上传商品图",
    ecomGenerate: "生成",
    ecomGenerating: "生成中…",
    ecomResultsLabel: "结果",
    ecomDownload: "下载",
    ecomDownloadAll: "批量下载",
    ecomBatchOverLimit: "批量最多 20 张，超出部分未添加",
    removeImage: "移除图片",
    // 电商图 · AI 模特 (ECOM-MODEL-UI-0001) — 子工具切换 + 模特偏好
    ecomSubToolLabel: "电商图工具",
    ecomSubToolCutout: "白底图",
    ecomSubToolModel: "AI 模特",
    ecomSubToolPoster: "营销海报", // 已下线（ECOM-REPLICATE-UI-0001）——保留键供 inert adapter/mock，不再入 SUBTOOLS
    ecomSubToolDetail: "电商详情图",
    ecomModeGroupLabel: "生成方式",
    ecomModelTitle: "AI 模特",
    ecomModelSubtitle: "上传商品图（可加模特图），AI 生成上身模特展示图",
    ecomGenderLabel: "模特性别",
    ecomGenderFemale: "女",
    ecomGenderMale: "男",
    ecomGenderAny: "不限",
    ecomStyleLabel: "风格预设（选填）",
    ecomStyleLoading: "加载风格预设…",
    ecomStyleError: "风格预设加载失败，请重试",
    ecomCustomLabel: "自定义补充（可选）",
    ecomCustomPlaceholder: "补充场景 / 姿态 / 氛围，如：暖光、街头、微笑站姿",
    ecomModelCompliance: "模特图为 AI 生成，商品以实物为准",
    // ECOM-MODEL-OPTIMIZE-UI-0001（D1–D3）：商品图/模特图多张 + 合计额度联动 + 组合语义 + 自定义风格互斥
    ecomModelProductLabel: "商品图（必填，至少 1 张）",
    ecomModelProductUpload: "上传商品图",
    ecomModelModelLabel: "模特图（选填）",
    ecomModelModelUpload: "上传模特图",
    ecomModelModelHint: "不传就是纯文生模特",
    ecomModelOverLimit: "商品图 + 模特图合计最多 6 张，请先移除部分再上传",
    ecomModelBudgetRemaining: (n: number) => `还可上传 ${n} 张（商品图 + 模特图合计 ≤ 6）`,
    ecomModelBudgetFull: "已选满 6 张（商品图 + 模特图合计 ≤ 6）",
    ecomModelProductRequired: "请至少上传 1 张商品图",
    ecomProductModeLabel: "商品图组合方式",
    ecomProductModeMultiItem: "多件商品搭配",
    ecomProductModeMultiAngle: "同一件商品的多角度",
    ecomProductModeHint: "多件搭配：同一模特同时上身多件商品（如衣服、裤子、鞋子、耳环、项链）；多角度：同一件商品的不同角度。",
    ecomCustomStyleLabel: "自定义风格",
    ecomCustomStylePlaceholder: "描述你想要的风格，如：赛博朋克霓虹夜景",
    ecomStylePresetDisabledHint: "已填自定义风格，清空后可选预设",
    ecomCustomStyleDisabledHint: "已选风格预设，取消后可自定义",
    // 电商图 · 营销海报 (ECOM-POSTER-UI-0001)
    ecomPosterTitle: "营销海报",
    ecomPosterSubtitle: "上传商品图，套版式 + 标题文案，一键生成营销海报；支持批量",
    ecomTemplateLabel: "版式预设",
    ecomTemplateLoading: "加载版式预设…",
    ecomTemplateError: "版式预设加载失败，请重试",
    ecomPosterTitleLabel: "标题（可选）",
    ecomPosterTitlePlaceholder: "海报主标题，如：年中大促 全场 5 折",
    ecomPosterTaglineLabel: "自定义一行（可选）",
    ecomPosterTaglinePlaceholder: "副标题 / 卖点一行，如：限时 3 天 错过再等一年",
    // 电商详情图·强制复刻向导 (ECOM-REPLICATE-UI-0001)
    ecomDetailTitle: "电商详情图 · 复刻生成",
    ecomDetailSubtitle: "上传参考图 + 商品图，AI 按参考图强制复刻，只换商品与文案（相似度 ≥80%）",
    ecomDetailModeLabel: "出图模式",
    ecomDetailModeMain: "主图（5 张）",
    ecomDetailModeDetail: "详情页（12 张）",
    // ECOM-REF-LIMIT-UI-0001：参考图上限随模式（主图 1–5 / 详情 1–12）；商品图仍 1–4。
    ecomDetailRefLabel: (max: number) => `参考图（复刻模板，1–${max} 张）`,
    ecomDetailRefUpload: "上传参考图（JPG / PNG / WebP，≤10MB）",
    ecomDetailProductLabel: (max: number) => `商品图（唯一商品依据，1–${max} 张）`,
    ecomDetailProductUpload: "上传商品图（JPG / PNG / WebP，≤10MB）",
    ecomDetailInfoLabel: "商品信息",
    ecomDetailInfoPlaceholder: "品类 / 材质 / 规格等（只用你提供的信息，AI 不补全材质、认证、功效、销量）",
    ecomDetailPointsLabel: "核心卖点",
    ecomDetailPointPlaceholder: "一条卖点，如：316 不锈钢，24 小时持续锁温",
    ecomDetailAddPoint: "添加卖点",
    ecomDetailRemovePoint: "删除该卖点",
    ecomDetailPlan: "生成规划表",
    ecomDetailPlanning: "分析参考图 · 生成规划中…",
    // 规划表确认（§10 列 + 整套总价 + 扣费确认）
    ecomPlanTitle: "生成规划（确认后按此复刻，仅确认一次）",
    ecomPlanColPage: "页码",
    ecomPlanColTheme: "页面主题",
    ecomPlanColSize: "尺寸",
    ecomPlanColPrompt: "生成要点",
    ecomPlanColOutput: "最终输出",
    ecomPlanNoCrop: "原图不裁剪",
    // BE plan.outputs[].theme 为机器枚举键（backend _MAIN_THEMES/_DETAIL_THEMES）；本地化展示，未知键原样透出。
    ecomReplicateTheme: (theme: string) =>
      (({
        layout_match: "版式复刻",
        color_match: "配色复刻",
        campaign_match: "营销卖点",
        social_match: "种草风格",
        white_background: "纯白底图",
        hero: "首屏主视觉",
        material: "材质细节",
        function: "功能展示",
        size: "尺寸规格",
        scenario: "使用场景",
        detail: "细节特写",
        comparison: "对比展示",
        packing: "包装展示",
        care: "养护说明",
        selling_point: "核心卖点",
        closing: "信任收尾"
      }) as Record<string, string>)[theme] ?? theme,
    ecomPlanTotalPrice: (credits: number) => `整套预计 ${credits} 积分`,
    ecomPlanConfirm: "确认并生成",
    ecomPlanBack: "返回修改",
    ecomChargeTitle: "确认扣费生成整套？",
    ecomChargeMessage: (credits: number) =>
      `将一次性扣除 ${credits} 积分生成整套图片，确认后开始复刻；质检失败的单张会自动重试、不重复扣费。`,
    ecomChargeConfirm: "确认扣费生成",
    // 生成中（禁分批展示，全部完成才一次性展示）
    ecomGenTitle: "复刻生成中",
    ecomGenProgress: (done: number, total: number) => `已完成 ${done} / ${total}，全部完成后一次性展示`,
    ecomGenWait: "整套生成中，请稍候…全部完成后统一展示",
    // 轮询瞬时失败（500/离线）：不放弃整套（已扣费、后端仍在生成），软提示 + 下一拍自动重试
    ecomGenRetrying: "网络波动，正在重试…整套仍在后端生成，请勿离开",
    // 结果（一次性 + 原始尺寸 + 平台建议 §18 + 下载原图 + 单张重试）
    ecomResultTitle: "复刻结果（整套）",
    ecomResultPageNo: (n: number) => `第 ${n} 张`,
    ecomResultSizeMain: (actual: string) =>
      `当前图片为 AI 原始输出尺寸：${actual}。平台建议主图尺寸：800x800。系统未自动裁剪，请下载后按平台要求自行裁剪或上传时调整。`,
    ecomResultSizeDetail: (actual: string) =>
      `当前图片为 AI 原始输出尺寸：${actual}。平台建议详情页尺寸：750x1000。系统未自动裁剪，请下载后按平台要求自行裁剪或上传时调整。`,
    // actual_dimensions 缺失（后端未回）→ 不把请求尺寸冒充实际输出尺寸（原图尺寸透明红线）
    ecomResultSizeUnknown: "AI 原图已生成，原始尺寸以下载文件为准。系统未自动裁剪，请下载后按平台要求自行调整。",
    ecomResultDownload: "下载原图",
    ecomResultDownloadUnavailable: "原图暂不可用",
    ecomResultRetry: "重试该张",
    ecomResultRetrying: "重试中…",
    ecomResultFailed: "该张生成失败",
    ecomResultPartialHint: "部分图片生成失败，可对失败图单张重试（不重复扣费）",
    ecomResultAllFailed: "整套生成失败，可返回重新发起；已扣费问题请联系客服。",
    ecomResultPreviewAlt: (n: number) => `复刻图第 ${n} 张`,
    // 提示词反推 第7模式 (REVERSE-PROMPT-UI-0001) — 上传图片反推提示词 + 一键带入
    modeReverse: "提示词反推",
    // 视频生成 第6模式 (VIDEOGEN-UI-0001) — 多参考图 + prompt(最大 2000 字) + 时长/分辨率 + BGM
    modeVideoGen: "视频生成",
    vgTitle: "视频生成",
    vgSubtitle: "多张参考图 + 提示词，生成创意短视频；可加背景音乐",
    vgRefImagesLabel: "参考图（最多 9 张）",
    vgRefImagesUpload: "添加参考图",
    vgRefOverLimit: "最多 9 张参考图，超出部分未添加",
    // 名词中性 + 随上限动态（供复用 picker 如电商详情图·商品图 max=4，避免误显「参考图」「9 张」）
    refImagesOverLimit: (max: number) => `最多 ${max} 张，超出部分未添加`,
    vgPromptLabel: "提示词",
    vgPromptPlaceholder: "描述你想要的画面、风格、运镜、氛围…（最大 2000 字）",
    vgPromptRequired: "请填写提示词",
    // 提示词 2000 字墙（VIDEO-GEN-PARAMS-UI-0001 需求2/D4）：超限红字（用户原话措辞）；前端拦不发 + BE 422 兜底
    vgPromptOverLimit: "提示词输入最大上限为 2000 字",
    // 负面提示词（需求1，可选、不限字数——BE negative_prompt 无 max_length）
    vgNegativeLabel: "负面提示词（可选）",
    vgNegativePlaceholder: "不希望出现的元素，如：水印、多余文字、杂乱背景、人物变形（可留空）",
    // 画面比例（需求3，7 值 + 自适应，默认自适应）
    vgAspectLabel: "画面比例",
    vgAspectAdaptive: "自适应",
    vgAspectAdaptiveHint: "自适应：由模型按参考图与内容决定画面尺寸，不主动裁切",
    // SPIKE：选显式比例会裁切/重构画面（同一竖图 adaptive 560×752 → 16:9 864×496，开头横向裁切、随后缩小加留白），要说清不是换外框
    vgAspectCropHint: "选固定比例时，模型可能裁切或重构画面以贴合该比例（不只是换外框）",
    // 音频生成（需求4，默认关；SPIKE：开启真出 AAC、同价不额外收费）。文案区分「模型生成音频」vs 下方 BGM 混音；不承诺配音/对白
    vgAudioLabel: "音频生成",
    vgAudioToggleAria: "音频生成开关",
    vgAudioHint: "开启后由视频模型生成环境音 / 配乐（不额外收费）。与下方「背景音乐」不同——后者是你另配、生成后混音的 BGM。暂不保证人声对白或口型同步",
    // ── 视频生视频（VIDEO-GEN-V2V-UI-0001 需求6 · D8–D10）──
    vgRefMediaLabel: "参考图或视频（可选）",
    // D8 严格二选一：UI 直接互斥，别让用户传完才 422（provider image_with_roles 与 video_urls 不能同用，BE 也兜底）
    vgRefMediaExclusiveImages: "已上传参考图——移除全部参考图后才能改传参考视频（二选一）",
    vgRefMediaExclusiveVideos: "已上传参考视频——移除全部参考视频后才能改传参考图（二选一）",
    vgRefVideosLabel: "参考视频（最多 3 条）",
    vgRefVideosUpload: "添加参考视频",
    // D5 真人限制（provider 内容审核硬限，显著明示；被拒不扣积分——SPIKE 实测 credits_cost=0）
    vgRefVideoNoHuman: "参考视频不能包含真人（平台内容审核限制）；若被审核拒绝，不会扣除积分",
    vgRefVideoType: "仅支持 MP4 / MOV / WEBM 格式的视频",
    vgRefVideoTooLarge: "视频超过 100MB，请压缩后再上传",
    vgRefVideoTooLong: "请上传 15 秒以内的视频（超长请自行剪辑，系统不代剪）",
    vgRefVideoTooShort: "参考视频过短（需大于 1.8 秒），请更换更长的素材", // FIX1：BE 单条下限 1.8s（video_reference.py:128-137）
    vgRefVideoResolutionLow: "视频分辨率过低（短边不足 480p），请更换更清晰的素材",
    vgRefVideoOverCount: "最多 3 条参考视频，超出部分未添加",
    // D9 转码/降码告知（服务端自动处理，不静默）
    vgRefVideoWillTranscode: "将自动转为 MP4",
    vgRefVideoWillDownscale: "将自动压缩至 720p",
    // D10 合计时长联动（BE 开区间：需大于 1.8 秒且小于 15.2 秒——对齐 routes/videos.py:940 的权威 message；不让用户传完 3 条才被告知）
    vgRefVideoTotal: (total: string) => `已传视频合计 ${total} 秒（需大于 1.8 秒且小于 15.2 秒）`,
    vgRefVideoTotalLow: "合计时长不足 1.8 秒，请补充或更换更长的素材",
    vgRefVideoTotalOver: "合计时长已超 15.2 秒上限，请移除或更换素材后再生成",
    vgRefVideoSeconds: (sec: string) => `${sec} 秒`,
    // 审核拒（任务执行期异步失败）：FIX1 已接入 friendlyVideoError 映射（码=VIDEO_REFERENCE_CONTENT_REJECTED，
    // workers/video_gen.py:56-58）；curated 文案比 BE message 多「未扣积分」说明（SPIKE 实测 credits_cost=0）。
    vgVideoModerationRejected: "参考视频未通过内容审核（不能包含真人或违规内容）。本次未扣除积分，请更换素材重试",
    // 出片慢预期管理（SPIKE 实测 233–329s；看门狗 27 分钟不误杀）——用户要知道等几分钟是正常的
    vgSlowHint: "视频生成约需 3–5 分钟，提交后可在下方任务卡查看进度",
    vgDurationLabel: "时长",
    vgResolutionLabel: "分辨率",
    vgResolutionHint: "更高分辨率更清晰，生成更慢、消耗更多",
    vgBgmLabel: "背景音乐",
    vgBgmNone: "无",
    vgBgmUpload: "上传",
    vgBgmLibrary: "配乐库",
    vgBgmUploadBtn: "上传音乐文件",
    vgBgmUploading: "上传中…",
    vgBgmUploaded: "已添加音乐，可重新上传替换",
    vgBgmUploadedPreview: "试听已上传的背景音乐",
    vgBgmRemove: "移除音乐",
    vgBgmLibraryLoading: "加载配乐库…",
    vgBgmLibraryError: "配乐库加载失败，请重试",
    vgBgmLibraryEmpty: "暂无可用配乐",
    vgBgmSelect: "选用",
    vgBgmSelected: "已选用",
    vgBgmPreviewLabel: (name: string) => `试听 ${name}`,
    // V2V Code Review：该键只被两个 picker 的**上传态**消费（真·生成按钮用 generate），原值「生成中…」语义错配
    // ——用户传 100MB 视频时误以为已扣积分开始生成。改为如实的「上传中…」（一键两 picker 同修）。
    vgGenerating: "上传中…",
    // BGM 波形播放器 (BGM-WAVEFORM-UI-0001)
    wfPlay: "播放",
    wfPause: "暂停",
    wfSeek: "拖动或点击波形跳转播放位置",
    wfLoading: "加载波形…"
  },
  confirm: {
    title: "确定生成",
    estimating: "估算中…",
    estimatePrefix: "预计消耗 ",
    estimateSuffix: " 积分",
    estimateNote: "按实际生成时长结算",
    estimateUnavailable: "暂无法预估，按实际结算",
    warning: "确定生成即会消耗积分，生成过程中无法取消！",
    confirm: "确定",
    confirming: "提交中…",
    cancel: "取消"
  },
  errors: {
    quota: "额度不足，无法生成，请充值或精简任务",
    uploadTooLarge: "图片过大，请控制在 10MB 以内",
    uploadType: "仅支持 JPG / PNG / WebP 图片",
    network: "网络连接失败，请检查后端服务是否在线",
    generic: "操作失败，请重试",
    // 数字人出镜视频客户端预检（AVATAR-VIDEO-SOURCE-UI-0001，MP4/≤10s/360p–1080p）
    videoType: "仅支持 MP4 视频",
    videoTooLarge: "视频过大，请控制在 200MB 以内",
    videoTooLong: "请上传 3–10 秒的单人出镜视频",
    // D2 放宽 60→180 秒（依据 §八 8.1 SPIKE 实测：180s 走方案 A = 6 段 × 30 秒，端到端 198.312s 可跑）。
    // 数值单一真源是 lib/media/reverse-video.ts 的 MIN/MAX_REVERSE_VIDEO_SEC，此处文案与之同步改。
    reverseVideoDuration: "请上传 1–180 秒的视频",
    reverseVideoFailed: "视频反推失败，请稍后重试",
    // 计费预估失败（§八 M4）：🔴 **不给任何金额**、也不提「按实际结算」——分档是一口价预扣，
    // 估不到就不许提交（宁可挡住也不能报错价）。
    reverseEstimateFailed: "暂时无法获取本次反推的积分消耗，请稍后重试",
    videoResolution: "视频分辨率需在 360p–1080p",
    videoUnreadable: "无法读取视频信息，请换一个 MP4 文件",
    // 电商详情图复刻·客户端校验（ECOM-REPLICATE-UI-0001）
    ecomDetailNeedMode: "请选择生成模式：主图 / 详情页",
    ecomDetailNeedRef: "请上传参考图（至少 1 张）",
    // ECOM-REF-LIMIT-UI-0001：切换模式后已传数量超新上限 → 明确拦截、绝不静默丢图（口径对齐 BE 422）。
    ecomDetailRefOverLimit: (max: number) => `参考图最多 ${max} 张（当前模式），请删减到 ${max} 张以内后再生成`,
    ecomDetailProductOverLimit: (max: number) => `商品图最多 ${max} 张，请删减到 ${max} 张以内后再生成`,
    ecomDetailNeedProduct: "请上传商品图（至少 1 张）",
    ecomDetailNeedInfo: "请填写商品信息",
    ecomDetailNeedPoint: "请至少填写 1 条核心卖点",
    ecomDetailFailed: "生成规划失败，请稍后重试",
    ecomDetailGenFailed: "生成失败，请稍后重试",
    // 后端二次校验专属码（FE-INTEGRATION-0001；前端预检读不到编码，回显后端码时用）
    videoCodec: "视频编码需为 H.264，请用常见工具重新导出 MP4",
    videoAudioCodec: "视频音轨需为 AAC，请用常见工具重新导出 MP4",
    videoAssetLost: "视频资源已失效，请重新上传",
    // 商品表批量(seedance_i2v)缺音色 → 后端 422(voice_id is required) 的中文映射
    voiceRequired: "请选择音色",
    // 图片生成错误（按后端 error_code 映射；见 friendlyImageError）
    imageModeration: "内容被 AI 安全系统拦截，请调整描述后重试（避免敏感或人体性暗示内容）",
    imageConnection: "网络连接失败，请检查代理 / 网络后重试",
    imageInvalid: "生成参数有误，请调整尺寸 / 质量或描述后重试",
    imageGeneric: "图片生成失败，请重试",
    // 抠图透明底专属（IMAGE_ALPHA_MISSING）：可操作文案，不落通用兜底
    imageAlphaMissing: "透明底生成失败：未返回透明像素，请重试或改用白底",
    // 图片服务能力不匹配兜底（IMAGE_PROVIDER_* · FIX1）：BE 已按 provider 能力（如 OpenAI 仅 1K/1 张）在落钱前 422 且带
    // 动态友好中文（含“请选择 1K”/“最多 N 张”），errorText 优先透出 BE message；仅当 message 意外为空时才落此兜底。
    imageProviderCapability: "当前图片服务不支持所选清晰度或参考图数量，请调整后重试",
    // 视频失败友好映射（VIDEO-ERR-MAP-UI，与后端 VIDEO-ERR-MAP-BE 共用错误码）——绝不回落裸 error_message
    videoInsufficientBalance: "余额不足，无法生成，请充值后重试",
    videoTimeout: "生成超时，请稍后重试",
    videoConnection: "网络异常，请检查网络后重试",
    videoGeneric: "视频生成失败，请重试",
    // 批量电商行参考图下载失败（BATCH_IMAGE_DOWNLOAD_FAILED，可操作）——批量视频失败面复用视频友好映射
    batchImageDownloadFailed: "参考图下载失败，请改用本地上传后重试",
    // 品牌音色音频上传/录音 (BRAND-VOICE-UI-0001)
    audioType: "仅支持 WAV / MP3 / M4A 音频",
    audioTooLarge: "音频过大，请控制在 20MB 以内",
    audioTooShort: "音频时长需至少 5 秒，请重录或换更长的音频",
    // VIP 门禁（ADMIN-VIP-GATE-UI-0001 §二之二）：doubao 通路无权限 → 友好中文（不透传英文；与「槽位空」区分）
    voiceClonePlanRequired: "「升级版 VIP」需开通 huading plan 后可创建，可先使用免费档 CosyVoice",
    // 提示词反推失败（REVERSE-PROMPT-UI-0001）——通用兜底，绝不回落裸 error_message/技术串
    reverseFailed: "提示词反推失败，请稍后重试"
  },
  // 提示词反推 · 图片 (REVERSE-PROMPT-UI-0001) —— 上传图片 → 反推可复用提示词 → 一键带入 4 模块
  reverse: {
    title: "提示词反推 · 图片",
    subtitle: "上传一张图片，AI 反推可复用的提示词，并可一键带入各生成模块",
    upload: "上传图片",
    uploadHint: "支持 JPG / PNG / WebP，≤10MB；仅用于本次反推参考",
    reupload: "换一张",
    // 输出语言/细节度不进请求（BE extra="forbid"，且输出本就同时给 zh+en）——面板已移除，仅保留目标格式只读展示。
    targetLabel: "目标格式",
    targetSeedance: "Seedance 2.0",
    analyze: "开始反推",
    analyzing: "AI 正在反推提示词…",
    resultTitle: "反推结果",
    // 近似重建红线（BE 未随 result 下发 disclaimer 时的前端兜底文案）
    disclaimer: "AI 依据画面近似重建提示词，仅供二次创作参考，不保证完全复刻原素材。",
    confidenceLabel: "置信度",
    blockPromptZh: "中文提示词",
    blockPromptEn: "英文提示词",
    blockNegative: "反向提示词",
    blockSubject: "主体",
    blockScene: "场景",
    blockComposition: "构图",
    blockCamera: "镜头",
    blockLighting: "光线",
    blockStyle: "风格标签",
    blockMotion: "运动提示",
    blockSelling: "电商卖点",
    blockText: "画面文字（仅识别，不执行）",
    save: "保存到历史",
    saving: "保存中…",
    saved: "已保存",
    regenerate: "重新反推",
    regenerating: "反推中…",
    applyTitle: "带入生成",
    applyHint: "选一个模块，把反推结果预填进去继续生成",
    applyAvatar: "带入 · 数字人口播",
    applyEcomVideo: "带入 · 电商带货",
    applyVideoGen: "带入 · 视频生成",
    applyPhoto: "带入 · 图片生成",
    applyEcomModel: "带入 · AI 模特",
    applyEcomPoster: "带入 · 营销海报",
    applyUnavailable: "该模块暂无可带入内容",
    // ── 带入前确认弹窗（REVERSE-DEEP-UI-0001 · D3-④：可编辑 / 可取消个别要素）──────────────
    // 🔴 「取消勾选 = 保持原样、不是清空」必须写在界面上：用户看不到载荷，只能靠这句话理解取消的后果。
    // ⚠️ 这是**用户可见文案**，不是注释：不要写 markdown 星号（界面无 md 渲染，会原样显示成 `**保持原样**`）。
    applyConfirmDesc: "默认全部带入。取消勾选的项不会带入，目标表单的该项保持原样（不会被清空）。",
    applyConfirmSubmit: "确认带入",
    applyConfirmCancel: "取消",
    applyItemSkipped: "不带入（保持原样）",
    // 同一行里勾选框与可编辑文本框**不能同名**（读屏会读到两个同名控件、测试也取不准）→ 文本框名加限定词。
    applyItemEditAria: (label: string) => `${label}（可编辑内容）`,
    applyItemsEmpty: "该模块本次没有可带入的内容",
    // 逐项标签（只渲染目标模块**接得住**的项；接不住的不显示，不造点了没用的开关）
    applyItemPrompt: "主提示词",
    applyItemTopic: "主题",
    applyItemScript: "口播文案",
    applyItemScenePrompt: "画面提示词",
    applyItemNegative: "负面提示词",
    applyItemMasterPrompt: "总控前缀",
    applyItemCustom: "自定义补充",
    applyItemShots: "分镜表",
    applyItemAspect: "画面比例",
    applyItemDuration: "时长",
    applyItemGenerateAudio: "音频生成",
    applyValueOn: "开启",
    applyValueOff: "关闭",
    applyDurationSec: (sec: number) => `${sec} 秒`,
    // D8 clamp 明示（**不许静默改数**）：原素材时长已知 → 说清「原多少 / 上限多少 / 已按上限带入」
    applyClampNote: (originSec: number, moduleLabel: string, maxSec: number) =>
      `原视频 ${originSec} 秒，${moduleLabel}单条上限 ${maxSec} 秒，已按上限带入`,
    applyClampNoteNoOrigin: (moduleLabel: string, maxSec: number) =>
      `原素材时长超出${moduleLabel}单条上限 ${maxSec} 秒，已按上限带入`,
    // 结构化主提示词（§4.3）——中文供理解、英文供 provider 消费，故给中/英两个复制按钮
    blockStructuredZh: "结构化提示词（中文）",
    blockStructuredEn: "结构化提示词（英文）",
    blockShotSummary: "分镜表",
    // 视频反推（VIDEO-REVERSE-PROMPT-UI-0001）—— 一个入口两模式（图片同步 / 视频异步 + 计费门）
    sourceLabel: "反推来源",
    sourceImage: "图片",
    sourceVideo: "视频",
    videoTitle: "提示词反推 · 视频",
    videoSubtitle: "上传一段视频，AI 分析分镜与节奏并反推可复用的提示词",
    videoUpload: "上传视频",
    // 上限同 errors.reverseVideoDuration，真源 MAX_REVERSE_VIDEO_SEC（D2 放宽到 180 秒，依据 §八 8.1 SPIKE）
    videoUploadHint: "MP4（H.264），≤200MB，1–180 秒；音轨可选（不强制）",
    videoReupload: "换一个视频",
    videoValidating: "校验视频中…",
    videoReady: "已上传，可反推",
    videoUploading: "上传中…",
    videoAnalyze: "反推视频提示词",
    videoAnalyzing: "AI 正在分析视频并反推…",
    videoPollRetrying: "网络波动，正在重试…视频仍在后端分析，请稍候",
    // ── 分段进度（§八 M5 + D10）─────────────────────────────────────────────────
    // 🔴 只有 BE 真给了 segments_total/segments_done 才渲染；两者为 null（图片 / ≤60s 短视频）时**什么都不显示**。
    //    「别做假进度条」= 不拿 0、不拿 undefined、不按时间自增编一个百分比。
    videoSegmentProgress: (done: number, total: number) => `正在分析第 ${done}/${total} 段`,
    // 诚实预计：SPIKE 实测 180s 视频端到端 198.312s（§八 8.1），任务包记「约 3.5 分钟」。
    // 故写区间而非「约 3 分钟」——低报等待时间同样是不诚实的那一侧。
    videoSegmentEta: "长视频按段串行分析，预计约 3–4 分钟，请保持页面打开",
    // ── 计费门（§八 M4 + D9：视频反推按时长分档，金额一律由 BE estimate 返回）──────────
    videoChargeTitle: "确认扣费反推视频？",
    videoChargeMessage: (credits: number) => `视频反推将一次性扣除 ${credits} 积分（分析分镜 + 生成提示词）；确认后开始，取消不扣费。`,
    // ── 图片档计费门（REVERSE-CHARGE-GATE-UI-0001 范围1）─────────────────────────
    // 🔴 文案决策：**不复用 videoChargeMessage / videoChargeTitle**。正文写死「视频反推…分析分镜」是视频档的真话，
    // 硬塞给图片就是名字与内容双重说谎（本项目在 deleteConfirmSoft / reverseDeleteConfirmMsg 上栽过两次）；
    // 泛化成通用句又会丢掉「分析分镜」这类各档专属信息。故两档各说各的真话、共用下面三条纯态度性文案
    // （estimating / blocked / confirm 与档位无关，去掉 video 前缀后由两路共用，不造同义 key）。
    // ⚠️ 金额一律取 BE estimate 返回值，**不写死 30**——分档/费率是租户可覆写的 CreditRate（#220 栽过「照抄默认值 1、真值 30」）。
    imageChargeTitle: "确认扣费反推图片？",
    imageChargeMessage: (credits: number) => `图片反推将一次性扣除 ${credits} 积分（识别画面 + 生成提示词）；确认后开始，取消不扣费。`,
    chargeEstimating: "正在获取本次反推的积分消耗…",
    // 取不到金额时弹窗正文：解释「为什么这里没有数字、也不让点确认」。**一个数字都不许出现。**
    chargeEstimateBlocked: "为避免显示金额与实际扣费不符，未取到本次金额前不能提交。",
    chargeConfirm: "确认扣费反推",
    // 🔴 徽标不再写死数字：D9 之后金额取决于视频时长（≤60s / 61–180s 两档），而此处在**上传之前**就要显示，
    //    根本无从得知档位。写死「150 积分 / 次」= 长视频用户看到 150、实扣 250 的错价。改为定性说明，
    //    具体金额由计费门弹窗显示 BE estimate 的返回值。
    videoChargeBadge: "按视频时长计费",
    // 视频分析展示（在提示词结果之上）
    vaTitle: "视频分析",
    vaDuration: "时长",
    vaDurationValue: (sec: number) => `${sec} 秒`,
    vaPacing: "节奏",
    // pacing 枚举 → 中文显示（BE slow|medium|fast|variable，不显裸英文）
    vaPacingLabel: { slow: "慢", medium: "中", fast: "快", variable: "可变" } as Record<string, string>,
    vaShotList: "分镜列表",
    vaShot: (n: number) => `镜头 ${n}`,
    vaShotRange: (start: number, end: number) => `${start}–${end}s`,
    vaShotCamera: "运镜",
    vaShotMotion: "动作",
    vaShotTransition: "转场",
    vaAudioTranscript: "音频转写",
    vaBgmStyle: "背景音乐风格",
    // 🔴 FIX2 真联调：ASR 已随 BE #219 落地（§八 M6，BE 真调 gpt-4o-mini-transcribe）→ 空值的含义变了，
    //    两个字段不能再共用一句「未启用（一期）」：台词为空 = 这段素材没识别出台词；BGM 为空 = BE 判不了、不许编。
    vaNoTranscript: "未识别到台词（无音轨或语音不清晰）",
    vaBgmUnsupported: "暂不支持识别音乐风格",
    // 仅剩「时长读不到」这类真·未知场景在用（vaDurationValue 的兜底），已不再用于台词/BGM
    vaNotEnabled: "未知"
  },
  tasks: {
    title: "生成任务",
    empty: "暂无任务，输入主题开始生成。",
    retry: "重试",
    retryUnavailable: "请到工作台重新发起",
    open: "查看详情",
    moreInHistory: "更多任务见下方「历史生成」",
    // GEN-HEARTBEAT-UI-0001：收到心跳期间的诚实等待反馈。措辞只承诺「还在生成」+「已经等了多久」，
    // **不承诺任何进度**（不写「即将完成」「还需 X 分钟」这类前端猜不到、也无权承诺的话）。
    stillGenerating: (elapsed: string) => `仍在生成（已 ${elapsed}）`
  },
  history: {
    title: "历史生成",
    tabAvatar: "数字人视频历史",
    tabEcom: "电商视频历史",
    tabVideoGen: "视频生成历史",
    tabPhoto: "图片历史",
    tabCopy: "文案历史",
    empty: "暂无历史记录",
    loading: "加载中…",
    error: "加载失败，请重试",
    retry: "重试",
    loadMore: "加载更多",
    filterAllImages: "全部图片",
    filterCovers: "仅封面",
    deleteItem: "删除",
    clearAll: "清空",
    deleteConfirmTitle: "删除这条记录？",
    deleteConfirmHard: "将永久删除，不可恢复。", // 视频/图片：BE 真硬删（_hard_delete_tasks）→ 诚实
    /**
     * 🔴 COPY-DRAFT-DELETE-COPY-FIX-0001：软删域（反推历史 / 文案草稿）的删除确认。
     *
     * 这里原本是 `deleteConfirmSoft:「将从历史移除（可恢复）。」`，**已删除**。它是个陷阱：
     * **名字编码的是 BE 实现**（Soft = 软删）→ 谁看到「BE 是软删」就选它 → 再骗一次用户。
     * 而软删是**运维保险**（出事能救、不碰媒体），**不是给用户的功能**：用户侧列表过滤已删、详情 404、
     * 且**全仓零恢复入口**（`restore`/`undelete`/`deleted_at = None` 一处都搜不到）→ 说「可恢复」是骗人。
     *
     * 本 key 的名字说的是**用户能观察到的后果**（NoUndo = 界面上没有回头路），不是 BE 怎么存的。
     * 谁选它，是在断言一件**可核查的、用户可见的事**；选错会被用户当场发现，而不是像 Soft 那样永远没人知道。
     * 将来某个域真做了回收站 → 这个名字对它立刻变假，那正是我们要的信号。
     *
     * 消费者：反推历史（reverse-history-list）、文案草稿（copy-draft-list）—— 两域的这句话**逐字相同**
     * （都在「历史生成」模块内、都是软删+无恢复入口），故共用一个 key 而非造两个值相同的 key：
     * 值相同的两个 key 迟早会各改各的。
     */
    deleteConfirmNoUndo: "将从历史移除，无法撤销。",
    deleteConfirmBtn: "确认删除",
    clearConfirmTitle: "清空该历史？",
    clearConfirmHard: "将永久删除此模块全部记录，不可恢复。",
    clearConfirmPhotoHard: "将清空全部图片（含封面），硬删不可恢复；不受当前「仅封面」筛选影响（始终删除全部图片）。",
    /**
     * 🔴 同上：原 `clearConfirmSoft:「将清空此模块全部草稿（可恢复）。」` **已删除** —— 与 deleteConfirmSoft
     * 是同一个谎的孪生（BE `clear_drafts`（services/copy.py:384-394）同样只置 deleted_at、同样没有恢复入口）。
     * 任务包只点了 deleteConfirmSoft；只删那一个 = 把陷阱留了一半，下一个人照样能摸到 clearConfirmSoft。
     * 名字带 Drafts 是因为**内容就是草稿专属**（「全部草稿」）—— 不装成通用的。
     */
    clearDraftsConfirmNoUndo: "将清空此模块全部草稿，无法撤销。",
    clearConfirmBtn: "确认清空",
    deleteFailed: "删除失败，请重试",
    clearFailed: "清空失败，请重试",
    // ── 提示词反推历史（HISTORY-VIDEO-REVERSE-UI-0001）──
    // 用户拍板：**在该 tab 内部**再分「图片反推 / 视频反推」，不是在顶层加两个 tab。
    // 二级分类沿用图片历史 tab 的 chip 交互语言（同为「同一端点换 source_kind 筛选」的 filter 语义）。
    tabReverse: "提示词反推历史",
    reverseKindLabel: "反推来源",
    reverseKindAll: "全部",
    reverseKindImage: "图片反推",
    reverseKindVideo: "视频反推",
    reverseEmpty: "暂无反推记录",
    reverseDetailTitle: "反推详情",
    reverseSourceAlt: "反推源图",
    // BE 事实（services/reverse_prompt.py:320-322）：缩略图只对图片源生成，视频源恒 null → 显式占位，不留空洞。
    reverseNoThumb: "视频源无缩略图",
    reverseNoSummary: "暂无摘要",
    reversePendingHint: "反推尚未完成，暂无结果可看",
    reverseDeleteConfirmTitle: "删除这条反推记录？",
    // 删除正文见上方 **deleteConfirmNoUndo**（COPY-DRAFT-DELETE-COPY-FIX-0001 起与文案草稿共用）。
    // 原 `reverseDeleteConfirmMsg` 是 FIX2 为反推单独造的，值与文案草稿那句逐字相同 → 合成一个 key。
    // 它当初"单独造"是对的（§四 划了线不许动文案 tab），现在两域都归位了，就该合。
    reverseKindTag: (kind: string) => (kind === "video" ? "视频反推" : "图片反推"),
    // ── 清空反推历史（REVERSE-CHARGE-GATE-UI-0001 FIX1 范围2）──────────────────────
    // 🔴 正文**必须说清这次删的是哪一类**，不许含糊成「将清空历史」：CB 打回 BE 的第一条 P1 就是
    // 「用户在图片筛选下清空可能误删看不见的视频历史」，而 E1 定的是**不给恢复入口** ——
    // 文案含糊 + 删了没法后悔 = 用户会真的丢东西。故三种 scope 各一句、把范围写死在句子里。
    // 文案范式沿用 deleteConfirmNoUndo：只讲用户看得见的后果（「无法撤销」），BE 怎么实现一个字不提。
    reverseClear: "清空",
    reverseClearConfirmTitle: "清空反推历史？",
    reverseClearConfirmMsg: (scope: "all" | "image" | "video") =>
      scope === "all"
        ? "将清空全部反推历史（图片 + 视频），无法撤销。"
        : scope === "image"
          ? "将清空图片反推历史，无法撤销；视频反推历史不受影响。"
          : "将清空视频反推历史，无法撤销；图片反推历史不受影响。",
    reverseClearFailed: "清空失败，请重试",
    // ── 三视频 tab 升级（HISTORY-VIDEO-DIALOG-UI-0001）：与图片 tab 同款交互语言 ──
    // 点内容 → 大图/播放；点「查看详情」→ 详情弹窗（弹窗内保留「打开详情页」，跳 /videos/{id} 的能力不丢）。
    videoLightboxTitle: "视频预览",
    videoPlay: "播放视频",
    videoDetailTitle: "视频详情",
    videoOpenPage: "打开详情页",
    videoNoPlayback: "视频仍在处理，暂无法播放",
    // 🔴 HISTORY-FULL-PROMPT-UI-0001：用户翻历史的目的是**把上次的提示词拿回去复用**，而长提示词被标题
    // truncate 挡住了。各域叫法按用户填的东西命名，不按 BE 字段名：
    //  · 生图/视频生成 → 用户填的就是「提示词」
    //  · 数字人口播 → 用户填的是 script = 要念的**文案**，叫它「提示词」是错的
    promptLabel: "提示词",
    scriptLabel: "口播文案",
    /**
     * 反推任务状态（BE DB CheckConstraint 5 值：queued/running/succeeded/failed/saved）。
     * FIX2 · P2：未知值回退「未知状态」而**不是原样透出** —— 当前 5 值约束下暂不触发，但 BE 将来加状态就会
     * 在 UI 上露出裸英文。与「类型层用裸 `str` 忠实透出、不吞未知值」不冲突：**类型层忠实、展示层兜底**，两件事。
     */
    reverseStatus: (status: string) =>
      ({ queued: "排队中", running: "反推中", succeeded: "已完成", failed: "失败", saved: "已保存" })[status] ??
      "未知状态"
  },
  // 图片历史·统一模块 (HISTORY-UI-0001) — 独立页 /history，4 tab（归一 category）。
  historyImages: {
    navEntry: "图片历史",
    pageTitle: "图片历史",
    pageSubtitle: "查看历史生成的图片，重开整套并下载原图",
    tabImageGen: "图片生成/修改",
    tabEcomWhite: "电商·白底图",
    tabEcomModel: "电商·模特图",
    tabEcomDetail: "电商·详情图",
    empty: "该分类暂无历史记录",
    emptyHint: "生成的图片会在这里归档，可随时重开整套、下载原图",
    loading: "加载中…",
    error: "加载失败，请重试",
    retry: "重试",
    loadMore: "加载更多",
    itemCount: (n: number) => `${n} 张`,
    // ── 删除 / 清空（HISTORY-CHAT-DELETE-UI-0001）──────────────────────────────
    // 🔴 文案纪律（本项目栽过两次，见 history.deleteConfirmNoUndo 的长注释）：只讲**用户看得见的后果**。
    // 复用 copy.history 的 deleteItem / deleteConfirmTitle / **deleteConfirmNoUndo** / deleteConfirmBtn /
    // clearConfirmBtn / deleteFailed / clearFailed —— 值逐字相同就共用一个 key，不造同义 key（会各改各的）。
    // 这里只补**图片历史专属**的两句：清空是「当前分类」（不是全部），且同样无恢复入口。
    clearCategory: "清空当前分类",
    clearCategoryConfirmTitle: "清空当前分类？",
    clearCategoryConfirmMsg: "将从历史移除当前分类的全部记录，无法撤销；其它分类不受影响。",
    // 状态徽标（归一 completed/partial_failed/failed/ready）
    statusCompleted: "已完成",
    statusPartial: "部分失败",
    statusFailed: "失败",
    statusReady: "已就绪",
    // 重开整套弹窗
    setTitle: "整套图片",
    setLoading: "加载整套图片中…",
    setError: "整套加载失败，请重试",
    setEmpty: "该记录暂无可展示的图片",
    setPartialHint: "本套部分图片生成失败，仅展示成功生成的图片",
    setPageNo: (n: number) => `第 ${n} 张`,
    previewAlt: (n: number) => `历史图片第 ${n} 张`,
    // 原图红线（下载原始 bytes、显示原始尺寸、缺失禁用）
    sizeLabel: (dims: string) => `原始尺寸：${dims}（下载为原图，未裁剪）`,
    sizeUnknown: "原图尺寸以下载文件为准（未裁剪）",
    download: "下载原图",
    close: "关闭",
    // HISTORY-IMAGE-TAB-UI-0001：并进工作台图片 tab 的 6 分类 chip（顺序：全部图片 / 图片生成·修改 / 白底 / 模特 / 详情 / 封面）
    catAll: "全部图片",
    catCover: "封面",
    // 交互：点图 → 大图弹窗（纯图片）；点「查看详情」→ 详情弹窗（整套 + 信息并集）；删除（硬删）
    viewDetail: "查看详情",
    openLarge: "查看大图",
    lightboxTitle: "查看大图",
    lightboxAlt: (title: string) => `${title}（大图预览）`,
    // 详情弹窗信息并集（HISTORY-UI 原弹窗没显；归一 API 列表项带进来）
    detailCreatedLabel: "生成时间",
    detailStatusLabel: "状态",
    detailCategoryLabel: "分类",
    detailCountLabel: (n: number) => `共 ${n} 张`
  },
  detail: { notFound: "视频不存在或无权访问", back: "返回", download: "下载 MP4", downloadImage: "下载图片" },
  status: { queued: "排队中", failed: "失败", done: "已完成", cancelled: "已取消" },
  // 封面制作（口播视频产物附属 ORAL-PROD-UI-0001）
  cover: {
    entry: "做封面",
    title: "制作封面",
    subtitle: "从口播视频抽帧叠标题，或用 AI 生成封面",
    tabFrame: "截帧",
    tabAi: "AI 封面",
    frameLoading: "加载候选帧…",
    frameError: "候选帧加载失败，请重试",
    frameEmpty: "暂无候选帧",
    frameLabel: "选择封面帧",
    titleLabel: "封面标题（可选）",
    titlePlaceholder: "输入封面标题文字，可留空＝纯截帧",
    titleFontSizeLabel: "标题字号",
    titleColorLabel: "标题颜色",
    titlePositionLabel: "标题位置",
    titleFontSizeRange: "字号需在 24–120",
    generateFrame: "生成封面",
    generatingFrame: "生成中…",
    aiPromptLabel: "封面描述",
    aiPromptPlaceholder: "描述想要的封面画面，如：暖光下的咖啡杯特写，高级质感",
    generateAi: "AI 生成封面",
    generatingAi: "生成中…",
    aiPending: "封面生成中，完成后自动展示并存入图片历史…",
    aiDoneNoUrl: "已生成，请到图片历史查看",
    resultAlt: "封面预览",
    download: "下载封面",
    frameSaved: "封面已生成，可下载",
    savedToHistory: "已存入图片历史",
    retry: "重试"
  },
  // 品牌音色 / 声音克隆 (BRAND-VOICE-UI-0001)
  brandVoice: {
    entry: "我的品牌音色",
    pageTitle: "品牌音色",
    pageSubtitle: "录制或上传一段音频，克隆成你的专属口播音色",
    // 创建
    createTitle: "新建品牌音色",
    createSubtitle: "录一段或上传音频 → 试听 → 授权 → 创建",
    recordPrompt: "请用自然的语速朗读：「大家好，欢迎来到我的直播间，今天给大家带来一款超值好物。」",
    recordHint: "在安静环境录制，时长至少 5 秒，吐字清晰效果更好",
    recordStart: "开始录音",
    recordStop: "停止录音",
    recording: "录音中…",
    recordAgain: "重新录制",
    recordPermissionDenied: "麦克风权限被拒绝，请在浏览器允许后重试，或改用上传",
    recordUnsupported: "当前浏览器不支持录音，请改用上传音频",
    orUpload: "或上传音频",
    uploadAudio: "上传音频（WAV / MP3 / M4A，≤20MB）",
    audioReady: "音频已就绪，可试听",
    previewAria: "试听待克隆音频",
    removeAudio: "移除音频",
    nameLabel: "音色名称",
    namePlaceholder: "给你的品牌音色起个名字，如：我的主播音",
    consentLabel: "我已获得被克隆人的授权，并知悉生成内容为 AI 深度合成，将合法合规使用",
    consentRequired: "请先勾选授权声明后再创建",
    complianceHint: "未经授权克隆他人声音可能违法；请确保已获授权，生成内容为 AI 深度合成",
    create: "创建品牌音色",
    creating: "创建中…",
    createNeedAudio: "请先录制或上传一段音频",
    createNeedName: "请填写音色名称",
    // 克隆通路选择（BRAND-VOICE-PICKER-UI-0001 范围4，文案用户定稿）
    providerSectionLabel: "克隆通路",
    providerCosyTitle: "免费开通私人专属音色",
    providerCosyDesc: "CosyVoice-v3.5-plus · 免费",
    providerDoubaoTitle: "升级版 VIP 永久高端定制音色",
    providerDoubaoDesc: "豆包 · 300 元（30000 积分）",
    // VIP 门禁（ADMIN-VIP-GATE-UI-0001 §二之二）：非 huading（且非 admin）→ doubao 卡置灰 + 此提示（与「仅 huading plan 用户可查看」同口径）
    providerVipLocked: "开通 huading plan 后可创建",
    // 「选我的音色」picker：doubao 音色无权限 → 置灰 + 此提示（区别于「暂无可用音色槽位」= 有权限池空）
    pickerVipLocked: "开通 huading plan 后可用",
    chargeConfirmTitle: "确认开通高端定制音色？",
    chargeConfirmMessage: (credits: number) =>
      `将消耗 ${credits} 积分（约 300 元）开通豆包 VIP 永久高端定制音色，确认后立即扣费，克隆结果生成后不可退。`,
    chargeConfirmBtn: "确认扣费开通",
    // 列表
    listTitle: "我的品牌音色",
    listLoading: "加载中…",
    listError: "加载失败，请重试",
    listEmpty: "还没有品牌音色，录制或上传一段音频来创建",
    statusProcessing: "处理中",
    statusReady: "可用",
    statusFailed: "失败",
    processingHint: "声音克隆处理中，完成后即可在口播选用",
    delete: "删除",
    deleteConfirmTitle: "删除该品牌音色？",
    deleteConfirmMessage: "将永久删除，不可恢复；已生成的视频不受影响。",
    deleteConfirmBtn: "确认删除",
    // 口播 picker 分组
    pickerBrandGroup: "我的品牌音色",
    pickerStandardGroup: "系统音色",
    // 「选我的音色」picker（BRAND-VOICE-PICKER-UI-0001）
    pickerBrandLoading: "加载我的音色…",
    pickerBrandEmpty: "还没有品牌音色",
    pickerBrandCreate: "去创建",
    pickerCloning: "复刻中",
    providerDoubao: "豆包",
    providerCosyvoice: "CosyVoice"
  },
  // 深度合成标识设置 (LABEL-UI-0001)
  label: {
    entry: "标识设置",
    pageTitle: "深度合成标识",
    pageSubtitle: "配置显式标识的位置与文案（在生成时开启标识的任务上生效）",
    settingsTitle: "标识水印",
    positionLabel: "标识位置",
    posBr: "右下",
    posBl: "左下",
    posTr: "右上",
    posTl: "左上",
    posBc: "底部居中",
    textLabel: "标识文案",
    textPlaceholder: "如：AI 生成（≤20 字，必填）",
    textRequired: "标识文案不能为空",
    previewLabel: "实时预览",
    // 与任务级开关语义统一(LABEL-TOGGLE-UI-0001)：此页配样式/文案，是否应用由每次生成开关决定
    applyNoticeTitle: "显式标识如何生效",
    applyNoticeHint: "此处仅配置显式标识的位置与文案；是否应用显式标识由每次生成时的「AI 生成标识」开关决定（默认关闭）。",
    save: "保存",
    saving: "保存中…",
    saved: "已保存",
    // 产物处知情提示
    productNotice: "已含 AI 生成标识",
    // AI 生成标识开关（LABEL-TOGGLE-UI-0001）：六大面板统一，默认关；不弹窗不阻断
    toggleLabel: "AI 生成标识",
    toggleHint: "关闭后，发布到抖音/快手等平台请自行完成 AI 内容声明"
  },
  // 发布中心 (PUBLISH-UI-0001)
  publish: {
    entry: "发布",
    pageTitle: "发布中心",
    pageSubtitle: "选平台生成各平台文案，一键复制 / 下载 / 去发布",
    platformLabel: "选择发布平台（可多选）",
    noSourceHint: "从「历史」或成片详情点「发布」进入，选择要发布的产物",
    generateDrafts: "生成各平台草稿",
    generating: "生成中…",
    selectAtLeastOne: "请至少选择一个平台",
    // 草稿卡
    cardTitleLabel: "标题",
    cardTextLabel: "文案",
    cardTopicsLabel: "话题（空格分隔）",
    coverAlt: "封面预览",
    copyText: "一键复制文案",
    copied: "已复制",
    copyFailed: "复制失败，请手动选择文案",
    download: "下载成片",
    goPublish: "去发布",
    goPublishAt: (name: string) => `去${name}发布`,
    markPublished: "标记已发布",
    marking: "标记中…",
    marked: "已发布",
    markFailed: "标记失败，请重试",
    // 记录
    recordsTitle: "发布记录",
    recordsLoading: "加载中…",
    recordsError: "加载失败，请重试",
    recordsEmpty: "暂无发布记录",
    statusDraft: "草稿",
    statusCopied: "已复制",
    statusPublished: "已发布",
    sourceVideo: "视频",
    sourceImage: "图片",
    deleteRecord: "删除",
    deleteConfirmTitle: "删除该发布记录？",
    deleteConfirmMessage: "将从发布记录移除，不影响已发布的内容。",
    deleteConfirmBtn: "确认删除",
    // 平台名（与后端 platforms 接口一致；此处为兜底展示）
    platformDouyin: "抖音",
    platformKuaishou: "快手",
    platformWechat: "视频号",
    platformXiaohongshu: "小红书",
    platformBilibili: "B站"
  },
  // 批量生产中心 (BATCH-PROD-UI-0001)
  batch: {
    pageTitle: "批量生产",
    pageSubtitle: "上传商品表或提示词组，一次批量出片（单批最多 30 条）",
    tabEcom: "商品表",
    tabPrompt: "提示词组",
    // 商品表（入口 A）
    ecomUploadLabel: "上传商品表（Excel / CSV）",
    ecomUploadBtn: "选择文件",
    ecomUploadHint: "列：商品名 / 卖点 / 商品图（URL 或留空后拖入本地图）；每行一条电商带货视频",
    ecomParseError: "表格解析失败，请检查文件格式",
    ecomColProduct: "商品名",
    ecomColSelling: "卖点",
    ecomColImage: "商品图",
    ecomColStatus: "校验",
    ecomRowOk: "就绪",
    ecomRowError: "缺必填",
    ecomErrProductRequired: "商品名必填",
    ecomErrSellingRequired: "卖点必填",
    ecomErrImageRequired: "商品图必填（URL 或上传）",
    ecomEmpty: "请上传商品表以预览",
    ecomHasInvalid: "部分行缺必填项（商品名 / 卖点 / 图），请补全后再生成",
    ecomRowImageUpload: "上传本地图",
    // 提示词组（入口 B）
    promptLabel: "提示词（每行一条，最多 30 条）",
    promptPlaceholder: "每行一条提示词，如：\n赛博城市夜景，霓虹运镜\n春日樱花街道，暖光",
    promptCount: (n: number) => `${n} 条`,
    promptEmpty: "请至少输入 1 条提示词",
    // 逐行配对参考图（BATCH-PROD-UI-0002）
    pairToggleLabel: "逐行配对参考图",
    pairToggleHint: "开启后按顺序为每条提示词各配一张参考图（第 N 行 ↔ 第 N 张）；关闭则参考图整体共用。",
    pairSharedNote: "参考图将应用于本批每一条。",
    pairPreviewTitle: "配对预览（请核对顺序）",
    pairCountMismatch: (prompts: number, images: number) =>
      `参考图数量需与提示词行数一致：当前提示词 ${prompts} 行、参考图 ${images} 张。`,
    pairRowLabel: (n: number) => `第 ${n} 行`,
    pairMissingImage: "缺图",
    // 公共参数
    commonTitle: "公共参数（应用到本批每条）",
    // 超限（真拦截：禁用 + 显式提示，不裁剪）
    overLimitN: (n: number) => `单批最多 30 条，当前 ${n} 条，请删减后再生成`,
    ecomTooLarge: "文件过大（最多 5MB），请精简后重传",
    ecomTooManyRows: "表格行数过多（最多 500 行），请拆分后重传",
    // 预估确认
    estimateTitle: "确认批量生产",
    estimateRows: (n: number) => `共 ${n} 条`,
    estimatePerRow: "单条积分",
    estimateTotal: "预计总积分",
    estimateBalance: "当前余额",
    estimateVoice: "配音音色",
    estimateInsufficient: "余额不足，无法提交本批",
    estimateQueueHint: "1080P 当前高峰可能排队较久",
    estimateConfirm: "确认生成",
    estimateCancel: "取消",
    estimating: "预估中…",
    submitting: "提交中…",
    createFailed: "批次创建失败",
    // 批次列表 + 详情
    listTitle: "批次记录",
    listEmpty: "暂无批次",
    listLoading: "加载中…",
    listError: "加载失败，请重试",
    progressLabel: (done: number, total: number) => `${done}/${total}`,
    statusRunning: "生成中",
    statusCompleted: "已完成",
    statusPartial: "部分失败",
    statusFailed: "失败",
    statusCancelled: "已取消",
    detailTitle: "批次详情",
    rowIndex: (i: number) => `第 ${i + 1} 条`,
    taskRetry: "重试",
    taskRetrying: "重试中…",
    cancelBatch: "取消批次",
    cancelling: "取消中…",
    cancelHint: "仅取消未开始的条目，已在生成的不中断",
    openBatch: "查看进度",
    backToList: "返回列表",
    kindEcom: "商品表",
    kindPrompt: "提示词组"
  },
  // 落地页 (LANDING-ENTRY-UI-0001) —— 文案一字不差取需求冻结 §三（v3 定稿）
  landing: {
    skipToMain: "跳到主要内容",
    brand: "华鼎AI · 短视频引擎",
    navFeatures: "功能",
    navSamples: "样片",
    navSteps: "教学",
    navAria: "落地页导航",
    // 入口两态（右上角）
    login: "登录",
    register: "立即注册",
    avatarAria: "账户菜单",
    menuConsole: "进控制台",
    menuLogout: "退出登录",
    // Hero
    heroBadge: "AI 驱动 · 全自动短视频生产",
    heroTitle: "企业级 AI 短视频工厂",
    heroSub:
      "输入主题、商品或脚本，批量产出符合品牌规范、可直接分发到各平台的成片。数字人口播、电商图、文案、视频，一站式智能生产。",
    // ADMIN-VIP-GATE-UI-0001：新注册余额=0 → 去掉「免费」暗示（不承诺免费额度）
    ctaRegister: "立即注册",
    ctaLogin: "登录控制台",
    // 数据条（冻结 §三※：不用不实的「500+」）
    statModules: "7 大模块",
    statAuto: "全自动生产",
    statDistribute: "多平台分发",
    // 七大生产模块
    modulesTitle: "七大生产模块",
    modulesSub: "一个工作台，覆盖短视频生产全链路",
    modAvatar: "数字人口播",
    modAvatarDesc: "照片或本人出镜视频，一键生成真人口播",
    modVideoGen: "视频生成",
    modVideoGenDesc: "文生视频·图生视频，主题即成片",
    modEcomImage: "电商图",
    modEcomImageDesc: "白底图·模特图·详情图复刻",
    modPhoto: "图片生成·修改",
    modPhotoDesc: "AI 出图，多种画面比例自由选",
    modCopywriting: "文案仿写",
    modCopywritingDesc: "输入爆款，仿写你的品牌话术",
    modEcomVideo: "电商带货",
    modEcomVideoDesc: "商品图一键生成带货短视频",
    modReverse: "提示词反推",
    modReverseDesc: "图片·视频反推可复用提示词",
    modMore: "更多能力持续上线",
    // 效果样片（占位）
    samplesTitle: "效果样片",
    samplesSub: "真实由平台生成（占位，待替换你的样片）",
    sampleAvatar: "口播样片",
    sampleEcomImage: "电商图",
    sampleEcomVideo: "带货视频",
    sampleAiImage: "AI 图",
    samplePlaceholder: "样片占位",
    // 五步上手
    stepsTitle: "五步上手",
    // ADMIN-VIP-GATE-UI-0001：新注册余额=0，注册后需开通额度才能生成 → 第 1 步点明，避免「注册即可生成」假暗示
    step1: "注册并登录控制台（开通额度后即可生成）",
    step2: "在工作台选择模块（数字人口播/电商图/文案…）",
    step3: "输入主题或上传素材（商品图、脚本、原视频）",
    step4: "一键生成，实时查看进度",
    step5: "下载原片或直接发布到各平台",
    // 注册 CTA 区（ADMIN-VIP-GATE-UI-0001：新注册余额=0，不承诺免费体验 → 引导联系开通额度）
    ctaTitle: "现在开始，把视频生产变成流水线",
    ctaSub: "注册后联系我们开通额度",
    // 页脚
    footerCopyright: "© 华鼎 · 企业级 AI 短视频引擎",
    footerIcp: "备案号：占位",
    footerAbout: "关于",
    footerContact: "联系",
    footerTerms: "服务条款"
  },
  // 联系我们（LANDING-CONTACT-UI-0001）——转化链路的断点：落地页/CTA 一直在说「联系我们开通额度」，
  // 却没给联系方式。微信二维码 + 双端引导（PC 扫屏上的码；移动端保存图→微信扫一扫选相册——
  // ⚠️ u.wechat.com 链接实测在微信外**任何**浏览器都 301 到 wechat.com 官网、且无 Universal Links
  // 配置 → 「点链接唤起微信」不成立，故不放链接，见 contact-qr.tsx 注释）。
  contact: {
    title: "联系我们，开通生成额度",
    sub: "新注册账号需开通额度后才能开始生成。添加客服微信，当天开通。",
    // 二维码 alt：读屏用户扫不了码 → alt 说清「这是什么 + 用来干什么」，不是一句空的「二维码」。
    qrAlt: "客服微信二维码：用微信「扫一扫」扫描本图，即可添加客服开通生成额度",
    hintDesktop: "打开手机微信「扫一扫」，扫描左侧二维码",
    hintMobile: "保存二维码图片，打开微信「扫一扫」，从相册选取识别",
    saveQr: "保存二维码",
    // iOS 兜底（FIX1 · P2-1）：iPhone Safari 的 `download` 存进「文件」App、不进「照片」，而微信从相册
    // 扫一扫要的是**照片库**里的图 → 「保存」按钮在 iOS 上不可靠。长按二维码「存储到照片」才是 iOS 的可靠路径。
    // （Android/Chromium 的 `download` 直接进可被相册访问的目录，故保存按钮对它有效。）
    hintSaveIos: "iPhone 可长按二维码 →「存储到照片」",
    // 注册成功横幅（工作台）：不一闪而过（常显直到关闭）/ 不阻断（横幅非弹窗）/ 能再次找到（顶栏常驻入口）
    welcomeTitle: "注册成功，欢迎加入华鼎！",
    welcomeBody: "新账号需要开通生成额度——添加客服微信，当天开通，即可开始生成。",
    welcomeAction: "查看微信二维码",
    welcomeDismiss: "我知道了",
    // 工作台常驻入口（横幅关掉后仍能找到联系方式的地方；对所有 0 余额账号可见，不只新注册）
    consoleEntry: "开通额度",
    dialogTitle: "添加客服微信",
    dialogDesc: "扫码添加客服微信，开通生成额度"
  },
  // 管理员后台（ADMIN-CONSOLE-UI-0001）——独立 /admin 区域，仅平台租户（admin_console entitlement）。
  admin: {
    consoleTitle: "管理后台",
    consoleEntry: "管理后台",
    backToWorkbench: "返回控制台",
    // 门禁友好页（无 admin_console；BE 会真 403 PLATFORM_ADMIN_REQUIRED）
    gateTitle: "仅平台管理员可访问",
    gateDesc: "管理后台为华鼎平台内部运营工具。如你认为应当拥有访问权限，请联系平台管理员。",
    gateBack: "返回工作台",
    // 左导航
    navTenants: "租户管理",
    navVoiceSlots: "音色槽位",
    navUsage: "用量明细",
    navTasks: "任务监控",
    navAudit: "审计日志",
    // 通用表格态
    loading: "加载中…",
    error: "加载失败，请重试",
    retry: "重试",
    empty: "暂无数据",
    pageRange: (from: number, to: number, total: number) => `第 ${from}–${to} / 共 ${total}`,
    prevPage: "上一页",
    nextPage: "下一页",
    // 租户管理
    tenantsTitle: "租户管理",
    tenantSearchPlaceholder: "搜索 slug / 名称 / owner 邮箱",
    planFilterAll: "全部套餐",
    statusFilterAll: "全部状态",
    planFree: "free",
    planBasic: "basic",
    planHuading: "huading",
    // 排序（冻结文档 §4.1：注册时间 / 余额 / 消耗）
    sortLabel: "排序",
    sortCreatedDesc: "注册时间 新→旧",
    sortCreatedAsc: "注册时间 旧→新",
    sortRemainingDesc: "剩余余额 高→低",
    sortRemainingAsc: "剩余余额 低→高",
    sortUsedDesc: "已用消耗 高→低",
    sortUsedAsc: "已用消耗 低→高",
    statusActive: "启用",
    statusDisabled: "停用",
    statusClosed: "已关闭",
    colTenant: "租户",
    colOwner: "owner 邮箱",
    colPlan: "套餐",
    colBalance: "余额（总 / 已用 / 预留 / 剩余）",
    colStatus: "状态",
    colCreated: "注册时间",
    colTasks: "任务数",
    colTaskFamily: "任务类型", // 任务监控「任务类型」筛选器的字段名标签（ADMIN-TASK-FAMILY-COPY-0001）——用户拍板「任务类型」
    colActions: "操作",
    viewDetail: "详情",
    detailTitle: "租户详情",
    detailRecentTasks: "最近任务",
    detailRecentUsage: "最近用量",
    detailVoiceSlots: "已挂音色槽位",
    detailNoSlots: "暂无专属槽位",
    // 余额调整（资金操作：二次确认 + 恰调一次）
    creditsAdjust: "余额调整",
    creditsDelta: "调整额度（正=充值，负=扣减）",
    creditsDeltaPlaceholder: "如 5000 或 -2000",
    creditsReason: "理由（必填）",
    creditsReasonPlaceholder: "如：线下打款充值 / 误操作回收",
    creditsDeltaRequired: "请输入非 0 的整数额度",
    creditsReasonRequired: "请填写调整理由",
    creditsReasonTooLong: "理由不能超过 500 字",
    creditsConfirmTitle: "确认余额调整",
    creditsConfirmCharge: (delta: number) => `+ 充值 ${delta.toLocaleString("zh-CN")} 积分`,
    creditsConfirmDeduct: (delta: number) => `− 扣减 ${Math.abs(delta).toLocaleString("zh-CN")} 积分`,
    creditsBeforeAfter: (before: number, after: number) =>
      `当前余额 ${before.toLocaleString("zh-CN")} → 调整后 ${after.toLocaleString("zh-CN")}`,
    creditsConfirmBtn: "确认调整",
    creditsDone: "余额已调整",
    // 改套餐
    planChange: "改套餐",
    planChangeTitle: "确认套餐变更",
    planChangeTo: (plan: string) => `切换到 ${plan} 套餐`,
    planChangeHuadingNote: "切到 huading 后该用户可查看自己的数据看板、可创建升级版 VIP 音色；切回 free 会立即失去这些能力（刷新即生效）。",
    planChangeBtn: "确认变更",
    planChangeDone: "套餐已变更",
    // 启用/停用
    statusEnable: "启用",
    statusDisable: "停用",
    statusDisableTitle: "确认停用账号",
    statusEnableTitle: "确认启用账号",
    statusDisableWarn: "停用后该租户全部用户将无法登录与调用接口。",
    statusEnableNote: "启用后该租户恢复登录与调用。",
    statusPlatformProtected: "平台租户不可停用",
    statusChangeBtn: "确认",
    statusChangeDone: "状态已变更",
    // 音色槽位
    voiceSlotsTitle: "音色槽位",
    poolTitle: "平台槽位池",
    tenantSlotsTitle: "各租户专属槽位",
    colSpeakerId: "speaker_id",
    colOccupiedBy: "占用租户",
    colVoiceName: "音色",
    slotFree: "空闲",
    // remaining = 全部槽位中未占用数（BE sum(not occupied)，含租户专属），非「平台池剩余」。
    poolRemaining: (n: number) => `未占用槽位 ${n} 个`,
    assignTitle: "分配专属槽位",
    assignTenantLabel: "租户",
    assignSpeakerLabel: "speaker_id（S_ 开头）",
    assignSpeakerPlaceholder: "S_xxxxxxx",
    assignSpeakerInvalid: "speaker_id 格式不正确（S_ 开头，1–157 位字母/数字/_/-）",
    assignSubmit: "分配",
    assignDone: "已分配（重复分配幂等，不会重复占用）",
    // 用量明细
    usageTitle: "用量明细",
    usageTenantAll: "全部租户",
    usageCapabilityAll: "全部 capability",
    usageProviderAll: "全部 provider",
    usageStatusAll: "全部状态",
    usageFrom: "起",
    usageTo: "止",
    colTime: "时间",
    colCapability: "capability",
    colProviderModel: "provider / model",
    colQuantity: "数量",
    colCredits: "扣减积分",
    colCost: "平台成本",
    colUsageStatus: "状态",
    colTask: "关联任务",
    exportCsv: "导出 CSV",
    exportDone: "已导出",
    // 任务监控（真契约筛选 = 任务类型 + 状态 + 租户 + 时间区间；字段名 task_family 不变，仅显示串改「任务类型」）
    tasksTitle: "任务监控",
    taskStatusAll: "全部状态",
    taskFamilyAll: "全部任务类型",
    taskFamilyVideo: "视频任务",
    taskFamilyReverse: "视频反推",
    taskFamilyEcom: "详情图复刻",
    colTaskId: "任务 id",
    colMode: "模式",
    colProgress: "进度",
    colError: "错误码 / 错误信息",
    colTimes: "创建 / 开始 / 结束",
    colDuration: "耗时",
    retryTask: "重跑",
    retryTitle: "确认重跑失败任务",
    // FIX1（BE FIX3 冻结）：披露字段只在**重试回执**里（确认前拿不到每任务组合）→ 弹窗只做如实的通用口径
    // 说明（不对不可知的事下承诺——上轮静默扣费教训），三态精确披露在结果横幅（retryDisclosure*）。
    retryConfirmNote:
      "任务将回到排队中重新执行。是否重新计费以重试回执为准：失败时已扣费的任务不重复扣费；已释放的重新计费（按量任务按实际成片时长结算），确认后立即在结果中明示。",
    retryBtn: "确认重跑",
    retryDone: "已重新排队",
    // 结果披露三态（🔴 一律不许静默扣费；BE FIX3 最终契约仅 charged/credits/is_estimate，无 estimate_basis）。
    retryDisclosureFree: "不会重复扣费",
    retryDisclosureFixed: (n: number) => `将扣费 ${n.toLocaleString("zh-CN")} 积分`,
    retryDisclosureEstimate: (n: number) => `预计扣费约 ${n.toLocaleString("zh-CN")} 积分，最终按实际成片时长结算`,
    // 审计日志
    auditTitle: "审计日志",
    auditActionAll: "全部动作",
    actionCreditsAdjust: "余额调整",
    actionPlanChange: "套餐变更",
    actionStatusChange: "状态变更",
    actionVoiceSlotAssign: "音色槽位分配",
    actionTaskRetry: "任务重跑",
    colActor: "操作者",
    colAction: "动作",
    colTargetTenant: "被操作租户",
    colBeforeAfter: "变更前 → 变更后",
    colReason: "理由",
    beforeAfterArrow: " → ",
    // 审计 diff 值格式化（ADMIN-AUDIT-DIFF-RENDER-FIX-0001）：null/空 → 破折号；布尔中文化；
    // 以下三个 sr-only 串给屏幕阅读器读通「变更前/后」语义（不只靠颜色/箭头），破折号读「无」而非噪音。
    auditEmpty: "—",
    auditBoolTrue: "是",
    auditBoolFalse: "否",
    auditSrBefore: "变更前",
    auditSrAfter: "变更后",
    auditSrNone: "无",
    // 信噪比（ADMIN-AUDIT-DIFF-NOISE-0001）：未变键折叠标题。信息只折叠不删——点开可逐键复核未变字段。
    auditUnchangedFold: (n: number) => `另有 ${n} 项未变化`,
    auditAllUnchanged: "本次无字段变化"
  },

  // 华鼎AI智脑 LLM 聊天（AIBRAIN-UI-0001，mock 先行）——**只追加，不重排**（避让电商线 §七）。
  aibrain: {
    navLabel: "华鼎AI智脑",
    title: "华鼎AI智脑",
    // 会话
    newChat: "新建对话",
    conversationsTitle: "对话",
    conversationsEmpty: "还没有对话，点「新建对话」开始。",
    untitled: "新对话",
    // ── 删除 / 清空（HISTORY-CHAT-DELETE-UI-0001，E2 只删整会话）─────────────────
    // 同上文案纪律：讲用户可观察后果，**不提 BE 软删**。会话不是"历史记录"故不复用 historyImages 的措辞，
    // 但同样是「删了就没了、没有恢复入口」；「不影响推理积分与账单」是用户真正关心且可核查的事实
    // （冻结 §5.2：只写 conversations.deleted_at，不碰 chat_messages / 账本 / 钱包）。
    deleteChat: "删除对话",
    deleteChatConfirmTitle: "删除这个对话？",
    deleteChatConfirmMsg: "对话及其消息将从列表移除，无法撤销；不影响推理积分余额与账单。",
    clearChats: "清空全部对话",
    clearChatsConfirmTitle: "清空全部对话？",
    clearChatsConfirmMsg: "全部对话将从列表移除，无法撤销；不影响推理积分余额与账单。",
    deleteChatFailed: "删除失败，请重试",
    clearChatsFailed: "清空失败，请重试",
    // 空 / 加载 / 错误态
    emptyTitle: "开始和华鼎AI智脑对话",
    emptyHint: "选一个智能强度，输入问题，或上传图片 / 文档。",
    loading: "加载中…",
    loadError: "对话加载失败",
    retry: "重试",
    // 消息
    you: "你",
    assistant: "华鼎AI智脑",
    copy: "复制",
    copied: "已复制",
    thinking: "正在思考…",
    imageAttachment: "图片",
    // FIX5：改收**已格式化的字符串**（FIX6 起是 `formatCreditsExact` —— 实扣是"已经发生的事"，如实展示）。此前裸插值 `${n}`，
    // 一次典型对话的实扣是 0.24192 这种长尾，直接摊在气泡上既难读、也与弹窗里的口径不一致。
    costLabel: (credits: string) => `本次消耗 ${credits} 积分`,
    // 输入框
    inputPlaceholder: "输入问题…（Enter 发送，Shift+Enter 换行）",
    send: "发送",
    sending: "发送中…",
    // 智能强度
    intensityLabel: "智能强度",
    intensityLow: "低",
    intensityMid: "中",
    intensityHigh: "高",
    // 🔴 PRICING-UI-0001 §二：「约 N 积分/次」是**估算**，不是价目。它此前作为裸整数出现（6/15/30），
    //    没人知道那是「500 输入 + 500 输出 token」的估值，于是 BE 降价 35% 之后 UI 静默错价了三个月。
    //    现在这个数由费率推导（`typicalCredits`），且**必须与 `intensityRateHint` 同屏出现**——
    //    口径行是常驻文本而非 hover tooltip，因为触屏上 hover 不可达（等于没有说明）。
    intensityCost: (n: number) => `约 ${n} 积分/次`,
    intensityAria: (label: string, cost: number) => `智能强度 ${label}，约 ${cost} 积分每次（按典型对话估算）`,
    /**
     * 常驻口径行：真实费率 + 「约 N 积分」是怎么估出来的。
     * 🔴 PRICING-UI-0002：费率现在有**两个区间**（≤272K / >272K）。常驻行**只写低区间**——
     *    那是绝大多数会话的实际口径，一行里塞四个数没人看得下去；高区间放进下面的展开说明。
     *    但**必须点明"还有另一档"**（`intensityRateTierNote`），否则用户会以为只有一个费率，
     *    真跑进长上下文之后就是「显示 1.12、实扣 2.24」——与 A1 那次「显示 30 实扣 100」同形态。
     */
    intensityRateHint: (input: string, output: string, promptTokens: number, completionTokens: number) =>
      `按输入 ${input} / 输出 ${output} 积分每千 token 计费；「约 N 积分/次」是按 ${promptTokens} 输入 + ${completionTokens} 输出 token 的典型对话估算，实际以本次用量结算。`,
    /** 常驻的一句提示：还有一档更贵的，点开看具体数。 */
    intensityRateTierNote: (thresholdWan: number) => `超长上下文（输入超过 ${thresholdWan} 万 token）另有更高费率。`,
    /** 展开后的高区间明细——点开才看到，不占常驻行。 */
    intensityRateTierToggle: "查看超长上下文费率",
    // ⚠️ 参数名是 thresholdWan（**万**）而不是 thresholdK（千）：传进来的是 27.2，不是 272。
    //    叫 K 会让下一个人按「千」去传，那就成了「输入超过 272 万 token」—— 错一个数量级，
    //    而这是**价格档位的判据**，错了用户就不知道自己什么时候会跳到高费率。
    intensityRateTierDetail: (thresholdWan: number, input: string, output: string) =>
      `输入超过 ${thresholdWan} 万 token 的部分，本档按输入 ${input} / 输出 ${output} 积分每千 token 计费。`,
    // ── 402 余额不足（§三）────────────────────────────────────────────────
    // 此前 402 只是**默默弹开充值窗**，用户看不到任何解释；而新预留逻辑会锁住一个远大于实际花费的数
    //（高速档光 completion 就 137.6256），不解释清楚会被当成「一次对话要花 137 积分」。
    insufficientTitle: "推理积分不足，本次没有发送",
    /** 🔴 §三 第 4 条 —— 这条路径最要紧的一句话：预留 ≠ 扣费。 */
    insufficientReserveNote:
      "这是「临时预留」，不是实际扣费：发送时按「最长回答」先锁住一笔积分，对话结束立即按实际用量结算，差额当场退回余额。",
    // 🔴 FIX1：BE `e2bc2c02` 起 402 带结构化 detail → 能给**精确值**，措辞里的「至少」随之去掉。
    //    回退措辞（`…MinRequired`）保留给 detail 缺失的情形——把下界说成精确值等于告诉用户
    //    「充这么多就够」，而实际还要加提示词那一段，充完照样发不出去。
    insufficientRequired: (credits: string) => `本次需临时预留 ${credits} 积分`,
    insufficientMinRequired: (credits: string) => `本次至少需临时预留 ${credits} 积分`,
    insufficientAvailable: (credits: string) => `当前可用 ${credits} 积分`,
    insufficientShortfallExact: (credits: string) => `还差 ${credits} 积分`,
    insufficientShortfall: (credits: string) => `至少还差 ${credits} 积分`,
    /** 回退态下余额 ≥ 下界却仍被拒：缺口来自提示词那一段（前端算不出精确值，故换一句话说清方向）。 */
    insufficientContextHint:
      "余额高于这个下限仍被拒，通常是本次对话的上下文较长或带了图片——提示词也要计入预留。可新建对话或精简内容后重试。",
    // ── 402 之二：欠费 AIBRAIN_OUTSTANDING_BALANCE（§三 · FIX1）────────────────────────────
    // 🔴 与「预留不足」是两回事，话术相反，别混用：
    //    预留不足 = 这笔钱只是临时锁住、结束会退回；
    //    欠费     = 上一次对话**已经答完并交付**，实际用量超出了当时的预留，差额记成了欠款。
    //    所以这里**绝不能**出现「会退回」三个字——那笔钱是真花掉了。
    outstandingTitle: "有未结清的推理积分，暂时无法继续",
    outstandingNote:
      "上一次对话已经答完并交付，实际用量超出了当时的预留，差额记成了欠款。补齐后即可继续对话。",
    outstandingAmount: (credits: string) => `需补齐 ${credits} 积分`,
    outstandingBalance: (credits: string) => `当前余额 ${credits} 积分`,
    /** detail 与钱包都拿不到数时只给定性说明——**不编数字**。 */
    outstandingUnknown: "补齐欠款后即可继续；具体金额请在充值后查看余额。",
    // ── 402 之三：在途敞口打满 AIBRAIN_INFLIGHT_EXPOSURE_LIMIT（FIX2）────────────────────────
    // 🔴🔴 本组文案的**硬要求**：说清这不是余额问题、充值不解决。
    //    上限是 config 常量（单请求最大敞口 × 2），钱包余额不在那个式子里 —— 用户充了钱照样发不出去，
    //    那比说错「欠费」更糟：他花了钱还是解决不了。故这里**不许**出现「余额」「充值」「积分不足」
    //    任何字样，也不许弹充值窗（分流在 aibrain-chat.tsx，门在 aibrain-chat.402.test.tsx）。
    // 🔴 「不是余额问题」这句是**主动澄清**，不是废话：用户刚被一个 402 拦下，默认联想就是没钱，
    //    不说破他就会去充值。
    inflightExposureTitle: "同时进行的对话太多，本次没有发送",
    inflightExposureNote: "这不是余额问题，充值不会解决——请等前面的对话答完再发。",
    /** 有 `in_flight_request_count` 时给出条数（用户据此知道要等几条）。 */
    inflightExposureCount: (n: number) => `当前有 ${n} 条对话正在进行中。`,
    /** `retryable` 为真（BE 目前恒真）→ 明确告诉用户重试就行，不必做别的。 */
    inflightExposureRetry: "稍后重试即可。",
    // ── 422：提示词超本地硬上限 AIBRAIN_PROMPT_LIMIT_EXCEEDED（FIX2）────────────────────────
    // 🔴 **故意不展示 detail 里的两个 token 数**（`prompt_token_upper_bound` / `max_prompt_tokens`）：
    //    前者是 BE 按 UTF-8 **字节数**算的保守上界（`_prompt_token_upper_bound` 的 docstring 自陈
    //    "conservative"），比真实 token 数大不少；把「你用了 95 万 token / 上限 92.2 万」摆给用户，
    //    既看不懂也据此行动不了，还是个虚高的数。只讲**能做的三件事**。
    promptLimitExceeded: "本次输入太长，没有发送。可以新建对话（历史消息也计入长度）、缩短输入内容，或减少图片后重试。",
    // ── 502：上游用量不可信 AIBRAIN_PROVIDER_USAGE_INVALID（FIX3 定稿）──────────────────────
    // 🔴 **「未扣费」现在可以说了**，依据是源码而不是回执：本码与 `AIBRAIN_USAGE_MISSING`、
    //    `AIBRAIN_PROVIDER_FAILED` 三者**共用** `_fail_chat_message`（aibrain.py:487/:511/:534/:558/:633），
    //    该函数 `entry_type="release"` 释放**全额**预留，并落 `UsageRecord(credits=Decimal("0"),
    //    status="released")` —— 对用户**零扣费**是代码写死的事实，不是承诺。
    //    FIX2 时我按 §六.3 保持中性（当时 CB 未判定，写错任何一边都是拿钱说假话）；现在判定有了。
    // ⚠️ FIX2 的 `AIBRAIN_PROVIDER_USAGE_LIMIT_EXCEEDED` 已在 `fbe8420d` 删除：「合法但超上限」
    //    改成封顶扣费 + 正常交付，不再是错误路径。所以这句话的适用范围也窄了——只剩"上报不可信"。
    providerUsageInvalid: "本次生成未能完成，未扣费，请重试；如果反复出现，请联系我们。",
    // ── 503：用量异常冷却 AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN（FIX3 第五个码 · FIX4 接真值）──
    // 🔴 这是**冷却**，既不是余额问题也不是并发太多 —— 三者的处置完全不同，文案不许串味：
    //    绝不出现「充值」「余额不足」「同时进行的对话太多」。
    // 🔴 FIX4：BE `b91e2188` 补上了 `detail.retry_after_seconds`（正整数），**「约一分钟」的硬编码
    //    换成真值**。回退那一支保留：字段缺失/非法时仍说「约一分钟」（来源是 config `default=60`，
    //    范围 [1,300] 可覆写）—— BE 哪天不发，UI 会**静默**退化成什么都不说，回退与它的门都不能省。
    // ⚠️ BE 仍**没有**发 `Retry-After` 头（全仓 grep 无），秒数只在 detail 里。
    // ⚠️ **秒数直接说秒，不换算成分钟**：范围只有 1–300，"85 秒后"完全可读；换算要处理取整
    //    （89 秒说"约 1 分钟"会让用户早退回来再撞一次）而没有任何实际收益。
    // ⚠️ **不说「这是用户级/租户级的」**：FIX3 时它是租户级，我判断不说（用户既不知道是谁触发、
    //    也无法据此行动）；FIX4 把它改成了**用户级**（BE 新表 `AIBrainUserCooldown` 按 user_id 唯一），
    //    "别人触发"这件事**更加不成立**了 —— 现在冷却就是他自己那次异常造成的，归因说明毫无必要。
    usageAnomalyCooldownRetryIn: (seconds: number) =>
      `AI 服务的用量统计暂时异常，已暂停新对话以免计费出错。请 ${seconds} 秒后重试。`,
    /** 回退：拿不到 `retry_after_seconds` 时按 config 默认值说个「约」。 */
    usageAnomalyCooldown: "AI 服务的用量统计暂时异常，已暂停新对话以免计费出错。请约一分钟后重试。",
    /**
     * 🔴 PRICING-UI-0003 · **冷却预告**（200 成功响应带 `cooldown_retry_after_seconds` 时）。
     *
     * 与上面那条 503 是**同一件事的两个时刻**，措辞必须能看出先后，别让用户以为出了两次问题：
     *   本条（预告）：回答**已经拿到了**，只是提醒「下一条要等一会儿」→ 语气平和、不是错误
     *   503（已撞上）：这次**没发出去**，告诉他「还要等 N 秒」
     * 所以本条开头是「本次回答已完成」而不是「异常/失败」，且**不出现「暂停」「出错」**那类词
     *   —— 那是 503 的措辞，用在这里会让用户以为刚拿到的回答有问题。
     * ⚠️ 秒数直接说秒（与 503 一致，不换算分钟）。
     */
    cooldownAhead: (seconds: number) =>
      `本次回答已完成。因这次用量较大，${seconds} 秒后才能发下一条。`,
    // ── 502 之三：REPLAY_GUARD AIBRAIN_PROVIDER_REPLAY_GUARD（FIX4 · 第六个码）─────────────────
    // 🔴 对用户来说它和 `PROVIDER_FAILED` **看起来是同一件事**（都没生成出来、都零扣费），
    //    但**后果不同**：本码在 BE 的 `_USER_COOLDOWN_ERROR_CODES` 里 → **一定会开用户冷却**。
    //    所以文案里**不许说「请重试」**——那是明知会失败还引导用户去做：他立刻重试必撞 503，
    //    体验就是连着两次失败。改说「稍等片刻再发送」。
    // ⚠️ 不说具体等多久：本码的 502 **没有 detail**（BE 只给 code+message），秒数拿不到。
    //    真去重试撞上 503 时才有精确秒数——那时 `usageAnomalyCooldownRetryIn` 会说清楚。
    //    这样两条文案是自洽的：这里说"稍等片刻"，撞上了说"还要 N 秒"。
    // ⚠️ 「未扣费」照说：本码同样走 `_fail_chat_message` → release 全额预留 + `UsageRecord.credits=0`。
    providerReplayGuard: "本次生成未能完成，未扣费。为避免重复计费，请稍等片刻再发送。",
    /**
     * 🔴 未知 402 的**中性**兜底（FIX2 · 方向从「引导充值」翻转）。
     * 上一轮的兜底是「按预留不足展示」，理由是「把没欠费的人说成欠费更糟」——那在只有两个码时成立。
     * 敞口码出现后不成立了：它是「充值无效」，把用户往充值上引 = 让他花了钱还解决不了。
     * 代价不对称是关键：猜错方向让用户白花钱（不可逆），而中性最多让他多点一次顶部的充值入口
     * （那个入口一直都在，从未消失）。理由与判断写进了回执。
     */
    unknownPaymentIssue: "本次未能发送，请稍后重试。",
    /** 409：答完要追加预留时，这条消息已不在等待中（并发发送 / 超时回收）。 */
    requestExpired: "这条消息已超时或被其他操作打断，未计费。请重新发送。",
    // 附件（一期只图片；文档解析是 BE 增量 3，本期不提供入口）
    attachImage: "上传图片",
    attachRemove: "移除附件",
    imageOnly: "仅支持 JPG / PNG / WebP 图片",
    uploadFailed: "上传失败，请重试",
    // 语音
    voiceStart: "语音输入",
    voiceStop: "停止录音",
    voiceUnsupported: "当前浏览器不支持语音输入（建议用 Chrome / Edge）",
    voiceListening: "正在聆听…",
    // 钱包 / 充值
    balanceLabel: "推理积分",
    balanceLow: "余额偏低",
    recharge: "充值",
    rechargeTitle: "充值推理积分",
    rechargeDesc: "余额按 1:1 充值为推理积分。",
    rechargeIrreversible: "🔴 推理积分单向不可退：充值后不可退回余额。",
    rechargeAmount: (n: number) => `${n} 积分`,
    rechargeConfirm: "确认充值",
    rechargeFailed: "充值失败，请重试",
    // 409：同一幂等键用于了不同金额（正常流程不该触发——改档位会换新键——但触发了要看得懂）。
    idempotencyReuse: "充值请求状态异常，请关闭弹窗后重新发起。",
    // 发送错误分流（对齐 BE status/code）
    reqLimit: "本次问答超过单次上限或余额不足以作答，请精简内容或充值后重试。",
    // FIX3：同样补上「未扣费」—— `AIBRAIN_PROVIDER_FAILED` 与另外两个 502 走的是同一个
    // `_fail_chat_message`（release 全额预留 + `UsageRecord.credits=0`），零扣费是源码事实。
    // 任务包 §一 把「PROVIDER_FAILED 扣不扣费」列为待 CB 判定的三条追问之一，源码里答案是明确的。
    providerFailed: "AI 服务暂时不可用，未扣费，请稍后重试。",
    attachmentRejected: "附件无效或已失效，请移除后重新上传。",
    // 通用错误
    error: "出错了，请重试"
  }
} as const;
