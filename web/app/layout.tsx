import type { Metadata } from "next";
import Link from "next/link";
import { LiveProvider } from "@/components/Live";
import { StatusBar } from "@/components/StatusBar";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpsLoop",
  description: "Autonomous investigation and fix proposal, human-gated release (simulated).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <LiveProvider>
          <nav className="topbar">
            <Link href="/" className="brand">
              OpsLoop
            </Link>
            <span className="pill sim" title="TaskFlow, its tenants, traffic and faults are all simulated">
              Simulated environment
            </span>
            <StatusBar />
          </nav>
          <main>{children}</main>
        </LiveProvider>
      </body>
    </html>
  );
}
