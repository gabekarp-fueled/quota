import React, { useEffect, useState } from "react";
import { api } from "../api";

function KeyResultRow({ kr, onUpdate, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ ...kr });
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await onUpdate(kr.id, {
        title: form.title,
        metric: form.metric,
        target_value: form.target_value ? parseFloat(form.target_value) : null,
        current_value: parseFloat(form.current_value || 0),
      });
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  if (editing) {
    return (
      <div className="bg-gray-50 rounded-lg p-3 space-y-2">
        <input
          className="input text-xs"
          value={form.title}
          onChange={(e) => setForm({ ...form, title: e.target.value })}
          placeholder="Key result title"
        />
        <div className="flex gap-2">
          <input
            className="input text-xs"
            value={form.metric}
            onChange={(e) => setForm({ ...form, metric: e.target.value })}
            placeholder="Metric (e.g. meetings/week)"
          />
          <input
            className="input text-xs w-24"
            type="number"
            value={form.current_value}
            onChange={(e) =>
              setForm({ ...form, current_value: e.target.value })
            }
            placeholder="Current"
          />
          <input
            className="input text-xs w-24"
            type="number"
            value={form.target_value || ""}
            onChange={(e) =>
              setForm({ ...form, target_value: e.target.value })
            }
            placeholder="Target"
          />
        </div>
        <div className="flex gap-2">
          <button
            onClick={save}
            disabled={saving}
            className="btn-primary text-xs py-1 px-3"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          <button
            onClick={() => setEditing(false)}
            className="btn-secondary text-xs py-1 px-3"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  const pct =
    kr.target_value
      ? Math.min(100, Math.round((kr.current_value / kr.target_value) * 100))
      : null;

  return (
    <div className="flex items-center gap-3 py-2 group">
      <div className="flex-1 min-w-0">
        <p className="text-sm text-gray-700">{kr.title}</p>
        {kr.metric && (
          <div className="flex items-center gap-3 mt-1">
            <span className="text-xs text-gray-400">{kr.metric}</span>
            {kr.target_value != null && (
              <>
                <div className="flex-1 max-w-32 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gray-800 rounded-full"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="text-xs text-gray-500">
                  {kr.current_value}/{kr.target_value}
                </span>
              </>
            )}
          </div>
        )}
      </div>
      <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={() => setEditing(true)}
          className="text-xs text-gray-400 hover:text-gray-700 px-2 py-1"
        >
          Edit
        </button>
        <button
          onClick={() => onDelete(kr.id)}
          className="text-xs text-red-400 hover:text-red-600 px-2 py-1"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

function ObjectiveCard({ obj, onRefresh }) {
  const [addingKR, setAddingKR] = useState(false);
  const [newKR, setNewKR] = useState({
    title: "",
    metric: "",
    target_value: "",
    current_value: "0",
  });
  const [savingKR, setSavingKR] = useState(false);
  const [toggling, setToggling] = useState(false);

  const addKR = async () => {
    if (!newKR.title) return;
    setSavingKR(true);
    try {
      await api.createKR({
        objective_id: obj.id,
        title: newKR.title,
        metric: newKR.metric,
        target_value: newKR.target_value ? parseFloat(newKR.target_value) : null,
        current_value: parseFloat(newKR.current_value || 0),
      });
      setNewKR({ title: "", metric: "", target_value: "", current_value: "0" });
      setAddingKR(false);
      onRefresh();
    } finally {
      setSavingKR(false);
    }
  };

  const updateKR = async (id, data) => {
    await api.updateKR(id, data);
    onRefresh();
  };

  const deleteKR = async (id) => {
    await api.deleteKR(id);
    onRefresh();
  };

  const toggleActive = async () => {
    setToggling(true);
    try {
      await api.updateObjective(obj.id, { active: !obj.active });
      onRefresh();
    } finally {
      setToggling(false);
    }
  };

  const deleteObj = async () => {
    if (!window.confirm(`Delete objective "${obj.title}"?`)) return;
    await api.deleteObjective(obj.id);
    onRefresh();
  };

  return (
    <div className={`card space-y-3 ${!obj.active ? "opacity-50" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className={`badge ${obj.active ? "badge-green" : "badge-gray"}`}>
              {obj.active ? "Active" : "Archived"}
            </span>
            {obj.target_date && (
              <span className="text-xs text-gray-400">Due {obj.target_date}</span>
            )}
          </div>
          <h3 className="font-semibold text-gray-900 mt-1">{obj.title}</h3>
          {obj.description && (
            <p className="text-xs text-gray-500 mt-0.5">{obj.description}</p>
          )}
        </div>
        <div className="flex gap-1 flex-shrink-0">
          <button
            onClick={toggleActive}
            disabled={toggling}
            className="text-xs text-gray-400 hover:text-gray-700 px-2 py-1"
          >
            {obj.active ? "Archive" : "Activate"}
          </button>
          <button
            onClick={deleteObj}
            className="text-xs text-red-400 hover:text-red-600 px-2 py-1"
          >
            ✕
          </button>
        </div>
      </div>

      {obj.key_results?.length > 0 && (
        <div className="border-t border-gray-100 pt-3 divide-y divide-gray-100">
          {obj.key_results.map((kr) => (
            <KeyResultRow
              key={kr.id}
              kr={kr}
              onUpdate={updateKR}
              onDelete={deleteKR}
            />
          ))}
        </div>
      )}

      {addingKR ? (
        <div className="bg-gray-50 rounded-lg p-3 space-y-2">
          <input
            className="input text-xs"
            value={newKR.title}
            onChange={(e) => setNewKR({ ...newKR, title: e.target.value })}
            placeholder="Key result title"
            autoFocus
          />
          <div className="flex gap-2">
            <input
              className="input text-xs"
              value={newKR.metric}
              onChange={(e) => setNewKR({ ...newKR, metric: e.target.value })}
              placeholder="Metric"
            />
            <input
              className="input text-xs w-24"
              type="number"
              value={newKR.current_value}
              onChange={(e) =>
                setNewKR({ ...newKR, current_value: e.target.value })
              }
              placeholder="Current"
            />
            <input
              className="input text-xs w-24"
              type="number"
              value={newKR.target_value}
              onChange={(e) =>
                setNewKR({ ...newKR, target_value: e.target.value })
              }
              placeholder="Target"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={addKR}
              disabled={savingKR || !newKR.title}
              className="btn-primary text-xs py-1 px-3"
            >
              {savingKR ? "Adding…" : "Add KR"}
            </button>
            <button
              onClick={() => setAddingKR(false)}
              className="btn-secondary text-xs py-1 px-3"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setAddingKR(true)}
          className="text-xs text-gray-400 hover:text-gray-700 transition-colors"
        >
          + Add key result
        </button>
      )}
    </div>
  );
}

export default function Objectives() {
  const [objectives, setObjectives] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [newObj, setNewObj] = useState({
    title: "",
    description: "",
    target_date: "",
    active: true,
  });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const data = await api.getObjectives();
      setObjectives(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const createObj = async () => {
    if (!newObj.title) return;
    setSaving(true);
    try {
      await api.createObjective({
        title: newObj.title,
        description: newObj.description,
        target_date: newObj.target_date || null,
        active: true,
      });
      setNewObj({ title: "", description: "", target_date: "", active: true });
      setAdding(false);
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <p className="text-gray-400 text-sm">Loading…</p>;
  if (error) return <p className="text-red-600 text-sm">{error}</p>;

  const active = objectives.filter((o) => o.active);
  const archived = objectives.filter((o) => !o.active);

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">OKRs</h1>
          <p className="text-sm text-gray-500 mt-1">
            Objectives injected into CRO at each daily review
          </p>
        </div>
        <button onClick={() => setAdding(true)} className="btn-primary">
          Add objective
        </button>
      </div>

      {adding && (
        <div className="card border-2 border-black space-y-3">
          <h2 className="text-base font-semibold text-gray-900">
            New Objective
          </h2>
          <input
            className="input"
            value={newObj.title}
            onChange={(e) => setNewObj({ ...newObj, title: e.target.value })}
            placeholder="Objective title"
            autoFocus
          />
          <textarea
            className="input resize-none"
            rows={2}
            value={newObj.description}
            onChange={(e) =>
              setNewObj({ ...newObj, description: e.target.value })
            }
            placeholder="Description (optional)"
          />
          <input
            className="input"
            type="date"
            value={newObj.target_date}
            onChange={(e) =>
              setNewObj({ ...newObj, target_date: e.target.value })
            }
          />
          <div className="flex gap-2">
            <button
              onClick={createObj}
              disabled={saving || !newObj.title}
              className="btn-primary disabled:opacity-40"
            >
              {saving ? "Creating…" : "Create"}
            </button>
            <button
              onClick={() => setAdding(false)}
              className="btn-secondary"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {active.length === 0 && !adding && (
        <div className="card text-center py-10">
          <p className="text-gray-400">No active objectives</p>
          <p className="text-gray-300 text-sm mt-1">
            Create one to start guiding CRO's daily reviews
          </p>
        </div>
      )}

      <div className="space-y-4">
        {active.map((obj) => (
          <ObjectiveCard key={obj.id} obj={obj} onRefresh={load} />
        ))}
      </div>

      {archived.length > 0 && (
        <div>
          <p className="text-xs text-gray-400 font-semibold uppercase tracking-wider mb-3">
            Archived
          </p>
          <div className="space-y-3">
            {archived.map((obj) => (
              <ObjectiveCard key={obj.id} obj={obj} onRefresh={load} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
