import { useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getProject, decide, assetUrl } from "../api";
import StatusBadge from "../components/StatusBadge";

export default function ProjectDetailPage() {
  const { id } = useParams();
  const pid = Number(id);
  const qc = useQueryClient();
  const { data: p } = useQuery({ queryKey: ["project", pid], queryFn: () => getProject(pid) });

  const decideM = useMutation({
    mutationFn: (approve: boolean) => decide(pid, approve),
    onSuccess: () => qc.invalidateQueries(),
  });

  if (!p) return <p className="text-slate-500">Loading…</p>;

  const images = p.assets.filter((a) => a.asset_type === "image" || a.asset_type === "thumbnail");
  const video = p.assets.find((a) => a.asset_type === "video");
  const audio = p.assets.find((a) => a.asset_type === "audio");

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="text-xl font-bold">{p.title}</h1>
          <div className="mt-1 flex items-center gap-2">
            <StatusBadge status={p.status} />
            <span className="text-slate-500 text-sm">{p.video_format}</span>
            {p.youtube_video_id && (
              <a
                className="text-red-400 text-sm hover:underline"
                href={`https://youtu.be/${p.youtube_video_id}`}
                target="_blank"
              >
                View on YouTube ↗
              </a>
            )}
          </div>
        </div>
        {p.status === "pending_approval" && (
          <div className="flex gap-2">
            <button
              onClick={() => decideM.mutate(true)}
              className="bg-emerald-600 hover:bg-emerald-500 rounded px-4 py-2 text-sm font-semibold"
            >
              Approve &amp; publish
            </button>
            <button
              onClick={() => decideM.mutate(false)}
              className="bg-rose-700 hover:bg-rose-600 rounded px-4 py-2 text-sm font-semibold"
            >
              Reject
            </button>
          </div>
        )}
      </div>

      {p.error && (
        <div className="bg-rose-950 border border-rose-800 rounded-lg p-3 text-rose-300 text-sm">
          {p.error}
        </div>
      )}

      {video && (
        <div>
          <h2 className="font-semibold mb-2">Final video</h2>
          <video src={assetUrl(pid, video.id)} controls className="max-h-[70vh] rounded-lg bg-black" />
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-6">
        <section className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <h2 className="font-semibold mb-2">Script</h2>
          <p className="text-sm text-slate-300 whitespace-pre-wrap">{p.script ?? "—"}</p>
        </section>
        <section className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
          <div>
            <h2 className="font-semibold mb-1">SEO description</h2>
            <p className="text-sm text-slate-300 whitespace-pre-wrap">{p.description ?? "—"}</p>
          </div>
          <div>
            <h2 className="font-semibold mb-1">Hashtags</h2>
            <div className="flex flex-wrap gap-1">
              {p.hashtags?.map((h) => (
                <span key={h} className="text-xs bg-slate-800 rounded px-2 py-0.5">#{h}</span>
              ))}
            </div>
          </div>
          {audio && (
            <div>
              <h2 className="font-semibold mb-1">Voiceover</h2>
              <audio src={assetUrl(pid, audio.id)} controls className="w-full" />
            </div>
          )}
        </section>
      </div>

      {images.length > 0 && (
        <section>
          <h2 className="font-semibold mb-2">Generated images</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {images.map((a) => (
              <img
                key={a.id}
                src={assetUrl(pid, a.id)}
                className="rounded-lg border border-slate-800 aspect-square object-cover"
              />
            ))}
          </div>
        </section>
      )}

      <section className="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <h2 className="font-semibold mb-2">Pipeline log</h2>
        <ul className="text-xs text-slate-400 space-y-1 max-h-60 overflow-auto font-mono">
          {p.logs.map((l) => (
            <li key={l.id}>
              <span className="text-slate-600">{new Date(l.created_at).toLocaleTimeString()}</span>{" "}
              <span className={l.level === "error" ? "text-rose-400" : "text-slate-300"}>
                [{l.stage}]
              </span>{" "}
              {l.message}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
