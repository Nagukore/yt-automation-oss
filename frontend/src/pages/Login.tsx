import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "../api";

export default function Login() {
  const [email, setEmail] = useState("admin@example.com");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await login(email, password);
      navigate("/");
    } catch {
      setError("Invalid email or password");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center">
      <form onSubmit={submit} className="w-80 bg-slate-900 border border-slate-800 rounded-xl p-6 space-y-4">
        <h1 className="text-xl font-bold text-red-500">▶ YT Automation</h1>
        <input
          className="w-full bg-slate-800 rounded px-3 py-2 text-sm"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          className="w-full bg-slate-800 rounded px-3 py-2 text-sm"
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="text-rose-400 text-sm">{error}</p>}
        <button className="w-full bg-red-600 hover:bg-red-500 rounded py-2 text-sm font-semibold">
          Sign in
        </button>
      </form>
    </div>
  );
}
