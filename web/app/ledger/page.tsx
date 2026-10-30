import type { Metadata } from "next";
import { Ledger } from "@/components/Ledger";

export const metadata: Metadata = { title: "Evidence Ledger · OpsLoop" };

export default function Page() {
  return <Ledger />;
}
