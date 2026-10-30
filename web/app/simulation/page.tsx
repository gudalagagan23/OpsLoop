import type { Metadata } from "next";
import { Simulation } from "@/components/Simulation";

export const metadata: Metadata = { title: "Simulation Lab · OpsLoop" };

export default function Page() {
  return <Simulation />;
}
