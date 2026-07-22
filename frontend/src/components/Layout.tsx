import { NavLink, Outlet, useNavigate } from "react-router-dom";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `px-4 py-2 rounded-lg text-sm font-medium transition ${
    isActive ? "bg-red-600 text-white" : "text-slate-300 hover:bg-slate-800"
  }`;

export default function Layout() {
  const navigate = useNavigate();
  const logout = () => {
    localStorage.removeItem("token");
    navigate("/login");
  };
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-900/60 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto flex items-center gap-2 px-4 py-3">
          <span className="text-red-500 font-bold text-lg mr-4">▶ YT Automation</span>
          <NavLink to="/" end className={linkClass}>Dashboard</NavLink>
          <NavLink to="/projects" className={linkClass}>Projects</NavLink>
          <NavLink to="/approvals" className={linkClass}>Approvals</NavLink>
          <button onClick={logout} className="ml-auto text-sm text-slate-400 hover:text-white">
            Log out
          </button>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
