import type { Metadata } from "next";
import { Approvals } from "@/components/Approvals";

export const metadata: Metadata = { title: "Approvals · OpsLoop" };

export default function Page() {
  return <Approvals />;
}
