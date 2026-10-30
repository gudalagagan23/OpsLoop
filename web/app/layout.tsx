import type { Metadata, Viewport } from "next";
import { AppShell } from "@/components/AppShell";
import { AuthProvider } from "@/components/Auth";
import { LiveProvider } from "@/components/Live";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpsLoop · Command Center",
  description: "Autonomous investigation and fix proposal, human-gated release (simulated).",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f5f7fb" },
    { media: "(prefers-color-scheme: dark)", color: "#0b1018" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <LiveProvider>
            <AppShell>{children}</AppShell>
          </LiveProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
