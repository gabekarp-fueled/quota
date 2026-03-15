import React, { useEffect, useState } from "react";
import { api } from "../api";

const AGENTS = [
  "cro",
  "scout",
  "outreach",
  "enablement",
  "channels",
  "inbox",
  "digest",
  "followup",
];

function timeAgo(isoStr) {
  if (!isoStr) return "—";
  const diff = (Date.now() - new Date(isoStr)) / 1000;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

function duration(start, end) {
  if (!start || !end) return "—";
  const secs = Math.round((new Date(end) - new Date(start)) / 1000);
  if (secs < 60) return `${secs}s`;
  return `${Math.round(secs / 60)}m ${secs % 60}s`;
}

function StatusBadge({ status }) {
  if (status === "ok") return <span className="badge-green">ok</span>;
  if (status === "error") return <span className="badge-red">error</span>;
  if (status === "running") return <span className="badge-blue">running</span>;
  return <span className="badge-gray">{status || "—"}</span>;
}

function RunRow({ run }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr
        className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer transition-colors"
        onClick={() => setOpen(!open)}
      >
        <td className="px-4 py-3 font-medium text-gray-700 capitalize">
          {run.agent_name}
        </td>
        <td className="px-4 py-3">
          <StatusBadge status={run.status} />
        </td>
        <td className="px-4 py-3 text-gray-500 text-sm">
          {timeAgo(run.started_at)}
        </td>
        <td className="px-4 py-3 text-gray-500 text-sm hidden md:table-cell">
          {duration(run.started_at, run.completed_at)}
        </td>
        <td className="px-4 py-3 text-gray-500 text-sm hidden lg:table-cell">
          {run.turns ?? "—"}
        </td>
        <td className="px-4 py-3 text-gray-500 text-sm hidden lg:table-cell">
          {run.input_tokens || run.output_tokens
            ? (
                (run.input_tokens || 0) + (run.output_tokens || 0)
              ).toLocaleString()
            : "—"}
        </td>
        <td className="px-4 py-3 text-gray-300 text-xs">{open ? "▲" : "▼"}</td>
      </tr>
      {open && (
        <tr className="border-b border-gray-100 bg-gray-50">
          <td colSpan={7} className="px-4 py-3 space-y-3">
            {run.focus && (
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">
                  Focus
                </p>
                <p className="text-xs text-gray-600 font-mono bg-white border border-gray-200 rounded px-2 py-1">
                  {run.focus}
                </p>
              </div>
            )}
            {run.summary && (
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">
                  Summary
                </p>
                <p className="text-xs text-gray-700 leading-relaxed whitespace-pre-wrap">
                  {run.summary}
                </p>
              </div>
            )}
            {run.tools_used?.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">
                  Tools used ({run.tools_used.length})
                </p>
                <div className="flex flex-wrap gap-1">
                  {[...new Set(run.tools_used)].map((t, i) => (
                    <span
                      key={i}
                      className="text-xs bg-gray-100 text-gray-600 rounded px-2 py-0.5 font-mono"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function Runs() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const PAGE = 50;

  const load = async (off = 0, agent = agentFilter) => {
    setLoading(true);
    try {
      const data = await api.getRuns(agent || null, PAGE);
      setRuns(data);
      setOffset(off);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(0, agentFilter);
  }, [agentFilter]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Run History</h1>
          <p className="text-sm text-gray-500 mt-1">
            All agent heartbeat executions
          </p>
        </div>
        <select
          className="input w-40 text-sm"
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
        >
          <option value="">All agents</option>
          {AGENTS.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="text-red-600 text-sm">{error}</p>}

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="text-left px-4 py-3 text-xs font-medium text-gray-500">
                Agent
              </th>
              <th className="text-left px-4 py-3 text-xs font-medium text-gray-500">
                Status
              </th>
              <th className="text-left px-4 py-3 text-xs font-medium text-gray-500">
                Started
              </th>
              <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 hidden md:table-cell">
                Duration
              </th>
              <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 hidden lg:table-cell">
                Turns
              </th>
              <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 hidden lg:table-cell">
                Tokens
              </th>
              <th className="px-4 py-3 w-8" />
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-4 py-8 text-center text-gray-400 text-sm"
                >
                  Loading…
                </td>
              </tr>
            ) : runs.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-4 py-8 text-center text-gray-400 text-sm"
                >
                  No runs found
                </td>
              </tr>
            ) : (
              runs.map((run) => <RunRow key={run.id} run={run} />)
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={() => load(Math.max(0, offset - PAGE))}
          disabled={offset === 0 || loading}
          className="btn-secondary text-xs disabled:opacity-40"
        >
          ← Prev
        </button>
        <span className="text-xs text-gray-400">
          Showing {offset + 1}–{offset + runs.length}
        </span>
        <button
          onClick={() => load(offset + PAGE)}
          disabled={runs.length < PAGE || loading}
          className="btn-secondary text-xs disabled:opacity-40"
        >
          Next →
        </button>
      </div>
    </div>
  );
}
