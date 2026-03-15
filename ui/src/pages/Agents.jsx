import React, { useEffect, useState } from "react";
import { api } from "../api";

export default function Agents() {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", display_name: "", model: "claude-sonnet-4-20250514", batch_size: 10 });
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    api.getAgents()
      .then(setAgents)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  async function handleCreate(e) {
    e.preventDefault();
    setCreating(true);
    try {
      const agent = await api.createAgent(form);
      setAgents((prev) => [...prev, agent]);
      setShowCreate(false);
      setForm({ name: "", display_name: "", model: "claude-sonnet-4-20250514", batch_size: 10 });
    } catch (e) {
      alert(e.message);
    } finally {
      setCreating(false);
    }
  }

  if (loading) return <p className="text-gray-400 text-sm">Loading…</p>;
  if (error) return <p className="text-red-600 text-sm">{error}</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Agents</h1>
          <p className="text-sm text-gray-500 mt-1">{agents.length} configured</p>
        </div>
        <button className="btn-primary" onClick={() => setShowCreate(true)}>
          Add agent
        </button>
      </div>

      {showCreate && (
        <div className="card border-2 border-black">
          <h2 className="text-base font-semibold text-gray-900 mb-4">New Agent</h2>
          <form onSubmit={handleCreate} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Slug (lowercase)</label>
                <input
                  className="input"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="my_agent"
                  required
                />
              </div>
              <div>
                <label className="label">Display name</label>
                <input
                  className="input"
                  value={form.display_name}
                  onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
                  placeholder="My Agent"
                  required
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Model</label>
                <input
                  className="input"
                  value={form.model}
                  onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
                />
              </div>
              <div>
                <label className="label">Batch size</label>
                <input
                  type="number"
                  className="input"
                  value={form.batch_size}
                  onChange={(e) => setForm((f) => ({ ...f, batch_size: Number(e.target.value) }))}
                />
              </div>
            </div>
            <div className="flex gap-2">
              <button type="submit" className="btn-primary" disabled={creating}>
                {creating ? "Creating…" : "Create"}
              </button>
              <button type="button" className="btn-secondary" onClick={() => setShowCreate(false)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {agents.map((agent) => (
          <a
            key={agent.name}
            href={`#/agents/${agent.name}`}
            className="card hover:border-gray-300 transition-colors block"
          >
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm font-semibold text-gray-900">{agent.display_name}</p>
                <p className="text-xs text-gray-400 mt-0.5 font-mono">{agent.name}</p>
              </div>
              <span className={`badge ${agent.enabled ? "badge-green" : "badge-gray"}`}>
                {agent.enabled ? "Active" : "Disabled"}
              </span>
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <span className="badge-gray">{agent.model}</span>
              <span className="badge-gray">batch: {agent.batch_size}</span>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
