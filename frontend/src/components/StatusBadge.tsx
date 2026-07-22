import type { ProjectStatus } from "../api";

const COLORS: Record<ProjectStatus, string> = {
  discovered: "bg-slate-700",
  researching: "bg-blue-700",
  scripting: "bg-blue-700",
  generating_media: "bg-indigo-700",
  rendering: "bg-purple-700",
  pending_approval: "bg-amber-600",
  approved: "bg-emerald-700",
  rejected: "bg-rose-800",
  publishing: "bg-cyan-700",
  published: "bg-emerald-600",
  failed: "bg-red-800",
};

export default function StatusBadge({ status }: { status: ProjectStatus }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${COLORS[status]}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}
