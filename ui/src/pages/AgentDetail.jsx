import React, { useEffect, useState } from "react";
import { api } from "../api";

const MODELS = [
  { value: "claude-sonnet-4-20250514", label: "Claude Sonnet 4" },
  { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
  { value: "claude-opus-4-20250514", label: "Claude Opus 4" },
];

function FileTab({ agentName, file, onSaved }) {
  const [content, setContent] = useState(file.content);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const dirty = content !== file.content;

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await api.updateAgentFile(agentName, file.filename, content);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      onSaved(file.filename, content);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500 font-mono">{file.filename}</span>
        <div className="flex items-center gap-2">
          {error && <span className="text-xs text-red-600">{error}</span>}
          <button
            onClick={save}
            disabled={saving || !dirty}
            className="btn-primary text-xs disabled:opacity-40"
          >
            {saving ? "Saving…" : saved ? "Saved" : "Save file"}
          </button>
        </div>
      </div>
      <textarea
        className="input font-mono text-xs leading-relaxed resize-none w-full"
        rows={28}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        spellCheck={false}
      />
      <p className="text-xs text-gray-400">{content.length.toLocaleString()} chars</p>
    </div>
  );
}

export default function AgentDetail({ name }) {
  const [agent, setAgent] = useState(null);
  const [form, setForm] = useState(null);
  const [files, setFiles] = useState([]);
  const [activeFile, setActiveFile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [triggerResult, setTriggerResult] = useState(null);

  useEffect(() => {
    Promise.all([
      api.getAgent(name),
      api.getAgentFiles(name).catch(() => []),
    ])
      .then(([a, f]) => {
        setAgent(a);
        setForm({ ...a });
        setFiles(f);
        if (f.length > 0) setActiveFile(f[0].filename);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [name]);

  const save = async () => {
    setSaving(true);
    setSaved(false);
    setError("");
    try {
      const updated = await api.updateAgent(name, {
        display_name: form.display_name,
        system_prompt: form.system_prompt,
        model: form.model,
        batch_size: form.batch_size,
        enabled: form.enabled,
      });
      setAgent(updated);
      setForm({ ...updated });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const trigger = async () => {
    setTriggering(true);
    setTriggerResult(null);
    try {
      const result = await api.triggerHeartbeat(name);
      setTriggerResult({
        ok: true,
        msg: result.result?.summary || result.status || "Triggered",
      });
    } catch (err) {
      setTriggerResult({ ok: false, msg: err.message });
    } finally {
      setTriggering(false);
    }
  };

  const handleFileSaved = (filename, content) => {
    setFiles((prev) =>
      prev.map((f) => (f.filename === filename ? { ...f, content } : f))
    );
  };

  const deleteAgent = async () => {
    if (
      !window.confirm(
        `Delete agent "${form.display_name || name}"? This cannot be undone.`
      )
    )
      return;
    try {
      await api.deleteAgent(name);
      window.location.hash = "#/agents";
    } catch (err) {
      setError(err.message);
    }
  };

  if (loading) return <p className="text-gray-400 text-sm">Loading…</p>;
  if (!form)
    return (
      <p className="text-red-600 text-sm">
        Error: {error || "Agent not found"}
      </p>
    );

  const dirty = JSON.stringify(form) !== JSON.stringify(agent);
  const currentFile = files.find((f) => f.filename === activeFile);

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <a
            href="#/agents"
            className="text-xs text-gray-400 hover:text-gray-600"
          >
            ← Agents
          </a>
          <h1 className="text-2xl font-semibold text-gray-900 mt-1">
            {form.display_name || name}
          </h1>
          <p className="text-xs text-gray-400 font-mono mt-0.5">{name}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={deleteAgent}
            className="btn-secondary text-xs text-red-600 hover:text-red-700 border-red-200 hover:border-red-300"
          >
            Delete
          </button>
          <button
            onClick={trigger}
            disabled={triggering}
            className="btn-secondary text-xs disabled:opacity-40"
          >
            {triggering ? "Running…" : "Trigger"}
          </button>
          <button
            onClick={save}
            disabled={saving || !dirty}
            className="btn-primary text-xs disabled:opacity-40"
          >
            {saving ? "Saving…" : saved ? "Saved" : "Save changes"}
          </button>
        </div>
      </div>

      {error && (
        <p className="text-red-600 text-sm bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      {triggerResult && (
        <p
          className={`text-sm px-3 py-2 rounded-lg border ${
            triggerResult.ok
              ? "text-green-700 bg-green-50 border-green-200"
              : "text-red-600 bg-red-50 border-red-200"
          }`}
        >
          {triggerResult.msg}
        </p>
      )}

      {/* Config */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold text-gray-700">Configuration</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Display name</label>
            <input
              className="input"
              value={form.display_name || ""}
              onChange={(e) =>
                setForm({ ...form, display_name: e.target.value })
              }
              placeholder={name}
            />
          </div>

          <div>
            <label className="label">Model</label>
            <select
              className="input"
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
            >
              {MODELS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
              {!MODELS.find((m) => m.value === form.model) && (
                <option value={form.model}>{form.model}</option>
              )}
            </select>
          </div>

          <div>
            <label className="label">Batch size</label>
            <input
              type="number"
              min={1}
              max={100}
              className="input"
              value={form.batch_size}
              onChange={(e) =>
                setForm({ ...form, batch_size: parseInt(e.target.value, 10) })
              }
            />
          </div>

          <div className="flex items-end pb-1">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setForm({ ...form, enabled: !form.enabled })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${
                  form.enabled ? "bg-gray-900" : "bg-gray-200"
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    form.enabled ? "translate-x-6" : "translate-x-1"
                  }`}
                />
              </button>
              <span className="text-sm text-gray-700">
                {form.enabled ? "Enabled" : "Disabled"}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Prompt Files */}
      <div className="card space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-700">Prompt Files</h2>
        </div>

        {files.length === 0 ? (
          <p className="text-sm text-gray-400">
            No prompt files found for this agent.
          </p>
        ) : (
          <>
            {/* File tabs */}
            <div className="flex gap-1 flex-wrap border-b border-gray-200">
              {files.map((f) => (
                <button
                  key={f.filename}
                  onClick={() => setActiveFile(f.filename)}
                  className={`px-3 py-1.5 text-xs rounded-t font-mono transition-colors ${
                    activeFile === f.filename
                      ? "text-gray-900 bg-white border-t border-l border-r border-gray-200 -mb-px"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {f.filename}
                </button>
              ))}
            </div>

            {currentFile && (
              <FileTab
                key={activeFile}
                agentName={name}
                file={currentFile}
                onSaved={handleFileSaved}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
