import { LiveDetail } from "@/components/live-detail";

type Props = {
  params: Promise<{ fixtureId: string }>;
  searchParams: Promise<{ league?: string }>;
};

export default async function LiveFixturePage({ params, searchParams }: Props) {
  const [{ fixtureId }, query] = await Promise.all([params, searchParams]);
  return <LiveDetail fixtureId={fixtureId} league={query.league ?? ""} />;
}
