import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects } from "../api";
import StatusBadge from "../components/StatusBadge";

export default function Projects() {
  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: () => listProjects(),
  });

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-slate-800/60 text-slate-400">
          <tr>
            <th className="text-left px-4 py-2">#</th>
            <th className="text-left px-4 py-2">Title</th>
            <th className="text-left px-4 py-2">Format</th>
            <th className="text-left px-4 py-2">Status</th>
            <th className="text-left px-4 py-2">Updated</th>
          </tr>
        </thead>
        <tbody>
          {projects?.map((p) => (
            <tr key={p.id} className="border-t border-slate-800 hover:bg-slate-800/40">
              <td className="px-4 py-2 text-slate-500">{p.id}</td>
              <td className="px-4 py-2">
                <Link to={`/projects/${p.id}`} className="text-red-400 hover:underline">
                  {p.title}
                </Link>
              </td>
              <td className="px-4 py-2">{p.video_format}</td>
              <td className="px-4 py-2"><StatusBadge status={p.status} /></td>
              <td className="px-4 py-2 text-slate-500">
                {new Date(p.updated_at).toLocaleString()}
              </td>
            </tr>
          ))}
          {!projects?.length && (
            <tr>
              <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                No projects yet — generate one from the Dashboard.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
