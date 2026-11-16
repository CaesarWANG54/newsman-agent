import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NewsMan Agent",
  description: "Daily AI news intelligence dashboard"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
