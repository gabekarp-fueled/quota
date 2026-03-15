import React, { useEffect, useState } from "react";
import { api } from "../api";

function fmt(n) {
  if (n === null || n === undefined) return "—";
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return String(n);
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusBadge(status) {
  if (!status) return <span className="badge-gray">—</span>;
  if (status === "ok") return <span className="badge-green">OK</span>;
  if (status === "error") return <span className="badge-red">Error</span>;
  if (status === "running") return <span className="badge-blue">Running</span>;
  return <span className="badge-gray">{status}</span>;
}

export default function Dashboard() {
  const [dash, setDash] = useState(null);
  const [pipeline, setPipeline] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.getDashboard(), api.getPipelineSummary()])
      .then(([d, p]) => {
        setDash(d);
        setPipeline(p);
      })
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="text-red-600 text-sm">{error}</p>;
  if (!dash) return <p className="text-gray-400 text-sm">Loading…</p>;

  const stats = dash.stats || {};

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Dashboard</h1>
        <p className="text-sm text-gray-500 mt-1">Agent activity and pipeline overview</p>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="stat-card">
          <div className="stat-value">{fmt(stats.runs_today)}</div>
          <div className="stat-label">Runs today</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{fmt(stats.errors_today)}</div>
          <div className="stat-label">Errors today</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{fmt(stats.tokens_today)}</div>
          <div className="stat-label">Tokens today</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{fmt(stats.active_objectives)}</div>
          <div className="stat-label">Active OKRs</div>
        </div>
      </div>

      {/* Pipeline summary */}
      {pipeline?.available && (
        <div className="card space-y-4">
          <h2 className="text-base font-semibold text-gray-900">Pipeline</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="stat-value">{fmt(pipeline.total)}</div>
              <div className="stat-label">Tracked accounts</div>
            </div>
            <div>
              <div className="stat-value">{fmt(pipeline.response_rate)}%</div>
              <div className="stat-label">Response rate</div>
            </div>
            <div>
              <div className="stat-value">{fmt(pipeline.meeting_booked)}</div>
              <div className="stat-label">Meetings booked</div>
            </div>
            <div>
              <div className="stat-value">{fmt(pipeline.need_attention)}</div>
              <div className="stat-label">Need attention</div>
            </div>
          </div>
          {pipeline.status_counts && (
            <div>
              <p className="label mb-2">By status</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(pipeline.status_counts).map(([k, v]) => (
                  <span key={k} className="badge-gray">
                    {k}: {v}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Agent cards */}
      <div>
        <h2 className="text-base font-semibold text-gray-900 mb-3">Agents</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {(dash.agents || []).map((agent) => (
            <a
              key={agent.name}
              href={`#/agents/${agent.name}`}
              className="card hover:border-gray-300 transition-colors block"
            >
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-900">{agent.display_name}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{agent.model}</p>
                </div>
                {agent.last_run && statusBadge(agent.last_run.status)}
              </div>
              {agent.last_run && (
                <p className="text-xs text-gray-400 mt-2">
                  Last run: {fmtDate(agent.last_run.completed_at)}
                </p>
              )}
            </a>
          ))}
        </div>
      </div>

      {/* Recent runs */}
      <div>
        <h2 className="text-base font-semibold text-gray-900 mb-3">Recent Runs</h2>
        <div className="card overflow-hidden p-0">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">Agent</th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">Status</th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">When</th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">Summary</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(dash.recent_runs || []).slice(0, 10).map((run) => (
                <tr key={run.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2.5 font-medium text-gray-700">{run.agent_name}</td>
                  <td className="px-4 py-2.5">{statusBadge(run.status)}</td>
                  <td className="px-4 py-2.5 text-gray-500">{fmtDate(run.completed_at)}</td>
                  <td className="px-4 py-2.5 text-gray-500 truncate max-w-xs">
                    {run.summary?.slice(0, 80) || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(dash.recent_runs || []).length === 0 && (
            <p className="text-sm text-gray-400 px-4 py-4">No runs yet.</p>
          )}
        </div>
      </div>
    </div>
  );
}
