import { RosterDetail } from "@/components/roster-detail";

type Props = { params: Promise<{ teamId: string }>; searchParams: Promise<{ league?: string }> };

export default async function TeamPage({ params, searchParams }: Props) {
  const [{ teamId }, query] = await Promise.all([params, searchParams]);
  return <RosterDetail teamId={teamId} league={query.league ?? ""} />;
}
