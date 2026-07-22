import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { pendingApproval, decide } from "../api";

export default function Approvals() {
  const qc = useQueryClient();
  const { data: items } = useQuery({ queryKey: ["pending"], queryFn: pendingApproval });

  const decideM = useMutation({
    mutationFn: ({ id, approve }: { id: number; approve: boolean }) => decide(id, approve),
    onSuccess: () => qc.invalidateQueries(),
  });

  if (!items?.length)
    return <p className="text-slate-500">Nothing waiting for approval. 🎉</p>;

  return (
    <div className="space-y-4">
      {items.map((p) => (
        <div key={p.id} className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-center gap-4">
          <div className="flex-1">
            <Link to={`/projects/${p.id}`} className="font-semibold text-red-400 hover:underline">
              {p.title}
            </Link>
            <p className="text-slate-500 text-sm">{p.video_format} · review before publishing</p>
          </div>
          <button
            onClick={() => decideM.mutate({ id: p.id, approve: true })}
            className="bg-emerald-600 hover:bg-emerald-500 rounded px-4 py-2 text-sm font-semibold"
          >
            Approve &amp; publish
          </button>
          <button
            onClick={() => decideM.mutate({ id: p.id, approve: false })}
            className="bg-rose-700 hover:bg-rose-600 rounded px-4 py-2 text-sm font-semibold"
          >
            Reject
          </button>
        </div>
      ))}
    </div>
  );
}
