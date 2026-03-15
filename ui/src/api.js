const BASE = "";

function getToken() {
  return localStorage.getItem("quota_token");
}

function authHeaders() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });

  if (res.status === 401) {
    localStorage.removeItem("quota_token");
    window.location.hash = "#/login";
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }

  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

export const api = {
  // Auth
  login: (password) =>
    request("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  // Dashboard
  getDashboard: () => request("/api/dashboard"),
  getPipelineSummary: () => request("/api/pipeline/summary"),

  // Agents
  getAgents: () => request("/api/agents"),
  getAgent: (name) => request(`/api/agents/${name}`),
  createAgent: (data) =>
    request("/api/agents", { method: "POST", body: JSON.stringify(data) }),
  updateAgent: (name, data) =>
    request(`/api/agents/${name}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteAgent: (name) =>
    request(`/api/agents/${name}`, { method: "DELETE" }),

  // Agent files
  getAgentFiles: (name) => request(`/api/agents/${name}/files`),
  updateAgentFile: (name, filename, content) =>
    request(`/api/agents/${name}/files/${filename}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),

  // Heartbeats
  triggerHeartbeat: (name, focus = null) =>
    request(`/agents/${name}/heartbeat`, {
      method: "POST",
      body: JSON.stringify({ focus }),
    }),

  // Objectives
  getObjectives: () => request("/api/objectives"),
  createObjective: (data) =>
    request("/api/objectives", { method: "POST", body: JSON.stringify(data) }),
  updateObjective: (id, data) =>
    request(`/api/objectives/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteObjective: (id) =>
    request(`/api/objectives/${id}`, { method: "DELETE" }),

  // Key Results
  createKR: (data) =>
    request("/api/key-results", { method: "POST", body: JSON.stringify(data) }),
  updateKR: (id, data) =>
    request(`/api/key-results/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteKR: (id) =>
    request(`/api/key-results/${id}`, { method: "DELETE" }),

  // Runs
  getRuns: (agent = null, limit = 50) => {
    const params = new URLSearchParams({ limit });
    if (agent) params.set("agent", agent);
    return request(`/api/runs?${params}`);
  },
};
