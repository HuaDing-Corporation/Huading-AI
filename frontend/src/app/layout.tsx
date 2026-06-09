import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "华鼎 AI · 控制台",
  description: "华鼎 AI · VIDEO ENGINE 控制台"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
