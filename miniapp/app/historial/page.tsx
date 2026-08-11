import { DailyTrackRecord, TrackRecord } from "@/components/track-record";

export default function HistorialPage() {
  return (
    <div className="stack">
      <DailyTrackRecord />
      <TrackRecord />
    </div>
  );
}
