import { HistoricalMatchDetail } from "@/components/historical-match-detail";

type Props = {
  params: Promise<{ fixtureId: string }>;
  searchParams: Promise<{ league?: string; competition?: string; home?: string; away?: string }>;
};

export default async function HistoricalMatchPage({ params, searchParams }: Props) {
  const [{ fixtureId }, query] = await Promise.all([params, searchParams]);
  return <HistoricalMatchDetail fixtureId={fixtureId} competitionId={query.competition ?? ""} league={query.league ?? ""} home={query.home ?? ""} away={query.away ?? ""} />;
}
