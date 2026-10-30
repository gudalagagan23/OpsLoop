import type { Metadata } from "next";
import { Releases } from "@/components/Releases";

export const metadata: Metadata = { title: "Releases & Judge · OpsLoop" };

export default function Page() {
  return <Releases />;
}
