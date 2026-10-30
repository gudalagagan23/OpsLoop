import type { Metadata } from "next";
import { Departments } from "@/components/Departments";

export const metadata: Metadata = { title: "Departments · OpsLoop" };

export default function Page() {
  return <Departments />;
}
