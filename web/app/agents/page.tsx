import type { Metadata } from "next";
import { Agents } from "@/components/Agents";

export const metadata: Metadata = { title: "Agents & Pipeline · OpsLoop" };

export default function Page() {
  return <Agents />;
}
