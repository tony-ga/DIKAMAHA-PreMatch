import { PredictionDetail } from "@/components/prediction-detail";

type Props = {
  params: Promise<{ fixtureId: string }>;
  searchParams: Promise<{ league?: string; home?: string; away?: string; homeName?: string; awayName?: string; kickoff?: string }>;
};

export default async function PredictionPage({ params, searchParams }: Props) {
  const [{ fixtureId }, query] = await Promise.all([params, searchParams]);
  return <PredictionDetail fixtureId={fixtureId} league={query.league ?? ""} home={query.home ?? ""} away={query.away ?? ""} homeName={query.homeName ?? ""} awayName={query.awayName ?? ""} kickoff={query.kickoff ?? ""} />;
}
