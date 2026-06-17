import type { Metadata } from "next";
import "./globals.css";

import { AuthProvider } from "@/lib/auth/auth-context";
import { QueryProvider } from "@/lib/query/query-provider";
import { VideoTasksProvider } from "@/lib/videos/tasks-context";

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
      <body>
        <QueryProvider>
          <AuthProvider>
            <VideoTasksProvider>{children}</VideoTasksProvider>
          </AuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
