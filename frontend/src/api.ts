import axios from "axios";

export const api = axios.create({ baseURL: "/" });

// Attach JWT from localStorage to every request.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Kick back to login on 401.
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem("token");
      if (location.pathname !== "/login") location.href = "/login";
    }
    return Promise.reject(err);
  }
);

export async function login(email: string, password: string) {
  const body = new URLSearchParams({ username: email, password });
  const { data } = await api.post("/api/auth/token", body, {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  localStorage.setItem("token", data.access_token);
  return data;
}

// --- types ---
export type ProjectStatus =
  | "discovered" | "researching" | "scripting" | "generating_media"
  | "rendering" | "pending_approval" | "approved" | "rejected"
  | "publishing" | "published" | "failed";

export interface Project {
  id: number;
  title: string;
  video_format: "short" | "long";
  status: ProjectStatus;
  description?: string;
  hashtags?: string[];
  thumbnail_prompts?: string[];
  youtube_video_id?: string;
  error?: string;
  created_at: string;
  updated_at: string;
}

export interface Asset {
  id: number;
  asset_type: "image" | "audio" | "subtitle" | "video" | "thumbnail";
  path: string;
  order_index: number;
}

export interface ProjectDetail extends Project {
  research?: string;
  script?: string;
  assets: Asset[];
  logs: { id: number; stage: string; level: string; message: string; created_at: string }[];
}

export const getStats = () => api.get("/api/dashboard/stats").then((r) => r.data);
export const listProjects = (status?: string) =>
  api.get<Project[]>("/api/projects", { params: { status } }).then((r) => r.data);
export const getProject = (id: number) =>
  api.get<ProjectDetail>(`/api/projects/${id}`).then((r) => r.data);
export const pendingApproval = () =>
  api.get<Project[]>("/api/approval/pending").then((r) => r.data);
export const decide = (id: number, approve: boolean, publish_now = true) =>
  api.post(`/api/approval/${id}`, { approve, publish_now }).then((r) => r.data);
export const discover = (limit = 10, auto_generate = false) =>
  api.post("/api/topics/discover", { limit, auto_generate }).then((r) => r.data);
export const createProject = (topic: string, video_format = "short") =>
  api.post("/api/projects", { topic, video_format }).then((r) => r.data);
export const assetUrl = (projectId: number, assetId: number) =>
  `/api/projects/${projectId}/media/${assetId}?token=${localStorage.getItem("token") ?? ""}`;
