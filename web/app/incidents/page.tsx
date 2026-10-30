import type { Metadata } from "next";
import { Board } from "@/components/Board";

export const metadata: Metadata = { title: "Incident Board · OpsLoop" };

export default function Page() {
  return <Board />;
}
