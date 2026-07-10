import type { Metadata } from "next";
import type { ReactNode } from "react";

// 营销公开路由组 (LANDING-ENTRY-UI-0001)：无鉴权门，访客可直达；独立 metadata（区别于控制台）。
export const metadata: Metadata = {
  title: "华鼎AI · 短视频引擎",
  description: "企业级 AI 短视频工厂——数字人口播、电商图、文案、视频，一站式智能生产。"
};

export default function MarketingLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
