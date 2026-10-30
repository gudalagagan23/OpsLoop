import { IncidentView } from "@/components/IncidentView";

export default async function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <IncidentView id={Number(id)} />;
}
