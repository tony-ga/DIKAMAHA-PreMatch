import { PlayerDetail } from "@/components/player-detail";

type Props = { params: Promise<{ playerId: string }>; searchParams: Promise<{ league?: string; team?: string }> };

export default async function PlayerPage({ params, searchParams }: Props) {
  const [{ playerId }, query] = await Promise.all([params, searchParams]);
  return <PlayerDetail playerId={playerId} teamId={query.team ?? ""} league={query.league ?? ""} />;
}
