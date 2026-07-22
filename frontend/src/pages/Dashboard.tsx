import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { discover, createProject, getStats } from "../api";

function Stat({ label, value, accent }: { label: string; value: number; accent?: string }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <div className={`text-3xl font-bold ${accent ?? ""}`}>{value}</div>
      <div className="text-slate-400 text-sm mt-1">{label}</div>
    </div>
  );
}

export default function Dashboard() {
  const qc = useQueryClient();
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: getStats });
  const [topic, setTopic] = useState("");

  const discoverM = useMutation({
    mutationFn: () => discover(10, true),
    onSuccess: () => qc.invalidateQueries(),
  });
  const createM = useMutation({
    mutationFn: () => createProject(topic),
    onSuccess: () => {
      setTopic("");
      qc.invalidateQueries();
    },
  });

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <Stat label="Projects" value={stats?.total_projects ?? 0} />
        <Stat label="Topics" value={stats?.total_topics ?? 0} />
        <Stat label="Pending approval" value={stats?.pending_approval ?? 0} accent="text-amber-400" />
        <Stat label="Published" value={stats?.published ?? 0} accent="text-emerald-400" />
        <Stat label="Failed" value={stats?.failed ?? 0} accent="text-rose-400" />
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
        <h2 className="font-semibold">Create content</h2>
        <div className="flex gap-2">
          <input
            className="flex-1 bg-slate-800 rounded px-3 py-2 text-sm"
            placeholder="Enter a topic to generate a video…"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
          />
          <button
            disabled={!topic || createM.isPending}
            onClick={() => createM.mutate()}
            className="bg-red-600 hover:bg-red-500 disabled:opacity-40 rounded px-4 text-sm font-semibold"
          >
            Generate
          </button>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => discoverM.mutate()}
            disabled={discoverM.isPending}
            className="bg-slate-700 hover:bg-slate-600 rounded px-4 py-2 text-sm"
          >
            {discoverM.isPending ? "Discovering…" : "Discover trends + auto-generate"}
          </button>
          <span className="text-slate-500 text-sm">
            Fetches trending topics and starts the pipeline for the top ones.
          </span>
        </div>
      </div>
    </div>
  );
}
