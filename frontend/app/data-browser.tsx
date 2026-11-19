"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "./connector-api";

export type SelectedObject = { name: string; catalog: string; schema_name: string; table_name: string; object_kind?: "TABLE" | "VIEW"; preview_columns: string[] };
type Item = { name: string; kind?: "TABLE" | "VIEW"; type?: string };
type Page = { items: Item[]; next_cursor: string | null };
type Location = { database: string; schema_name: string; table: string };
type Props = { id: string; version: number; initial: SelectedObject[]; onSaved: () => Promise<void>; onClose: () => void };
const keyOf = (o: SelectedObject) => JSON.stringify([o.catalog, o.schema_name, o.table_name]);
const newLabel = () => `object_${crypto.randomUUID().replaceAll("-", "")}`;

export default function DataBrowser({ id, version, initial, onSaved, onClose }: Props) {
  const [location, setLocation] = useState<Location>({ database: "", schema_name: "", table: "" });
  const [items, setItems] = useState<Item[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [prefix, setPrefix] = useState("");
  const [loadedPrefix, setLoadedPrefix] = useState("");
  const [selection, setSelection] = useState<SelectedObject[]>(initial);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const requestId = useRef(0);
  const root = useRef<HTMLDivElement>(null);
  const level = !location.database ? "databases" : !location.schema_name ? "schemas" : !location.table ? "objects" : "columns";
  const active = selection.find(o => o.catalog === location.database && o.schema_name === location.schema_name && o.table_name === location.table);

  async function load(target: Location, after = "", filter = "") {
    const request = ++requestId.current;
    setLoading(true); setError("");
    if (!after) { setItems([]); setNext(null); }
    try {
      const targetLevel = !target.database ? "databases" : !target.schema_name ? "schemas" : !target.table ? "objects" : "columns";
      const page = await api<Page>(`connections/${id}/browse`, "POST", { level: targetLevel, ...target, after, prefix: filter });
      if (request !== requestId.current) return;
      setItems(previous => after ? [...previous, ...page.items.filter(item => !previous.some(p => p.name === item.name))] : page.items);
      setNext(page.next_cursor); setLoadedPrefix(filter);
    } catch (e) { if (request === requestId.current) setError((e as Error).message); }
    finally { if (request === requestId.current) setLoading(false); }
  }
  useEffect(() => {
    void load({ database: "", schema_name: "", table: "" });
    root.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => { requestId.current += 1; };
    // A browser session is mounted once for one profile/version.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function navigate(target: Location) { setLocation(target); setPrefix(""); void load(target); }
  function tableObject(item: Item): SelectedObject {
    return { name: newLabel(), catalog: location.database, schema_name: location.schema_name, table_name: item.name, object_kind: item.kind || "TABLE", preview_columns: [] };
  }
  function toggleObject(item: Item) {
    const object = tableObject(item), exists = selection.some(o => keyOf(o) === keyOf(object));
    if (!exists && selection.length >= 30) { setError("This connector profile supports up to 30 selected objects. Use separate profiles for larger scopes."); return; }
    setSelection(previous => exists ? previous.filter(o => keyOf(o) !== keyOf(object)) : [...previous, object]);
    setDirty(true); setError("");
  }
  function toggleColumn(name: string) {
    if (!active) return;
    if (!active.preview_columns.includes(name) && active.preview_columns.length >= 30) { setError("Select up to 30 preview columns for each object."); return; }
    setSelection(previous => previous.map(o => keyOf(o) === keyOf(active) ? { ...o, preview_columns: o.preview_columns.includes(name) ? o.preview_columns.filter(c => c !== name) : [...o.preview_columns, name] } : o));
    setDirty(true); setError("");
  }
  function selectVisible() {
    if (level === "objects") {
      const additions = items.map(tableObject).filter(o => !selection.some(p => keyOf(p) === keyOf(o)));
      if (selection.length + additions.length > 30) { setError("Select up to 30 objects per profile. Narrow your search or choose individually."); return; }
      setSelection(previous => [...previous, ...additions]);
    } else if (level === "columns" && active) {
      const columns = Array.from(new Set([...active.preview_columns, ...items.map(i => i.name)]));
      if (columns.length > 30) { setError("Select up to 30 preview columns. Narrow your search or choose individually."); return; }
      setSelection(previous => previous.map(o => keyOf(o) === keyOf(active) ? { ...o, preview_columns: columns } : o));
    }
    setDirty(true); setError("");
  }
  async function save() {
    setSaving(true); setError("");
    try { await api(`connections/${id}/selection`, "POST", { version, objects: selection }); await onSaved(); }
    catch (e) { setError((e as Error).message); }
    finally { setSaving(false); }
  }
  function close() { if (!saving && (!dirty || window.confirm("Discard your unsaved data selection?"))) onClose(); }

  return <div className="overlay"><div ref={root} className="dialog data-browser" role="dialog" aria-modal="true" aria-labelledby="browser-title" onKeyDown={event => {
    if (event.key === "Escape") close();
    if (event.key !== "Tab") return;
    const nodes = Array.from(root.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)') || []);
    if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes.at(-1)?.focus(); }
    else if (!event.shiftKey && document.activeElement === nodes.at(-1)) { event.preventDefault(); nodes[0]?.focus(); }
  }}>
    <div className="dialog-head"><div><span className="eyebrow">SNOWFLAKE · DATA SELECTION</span><h2 id="browser-title">Browse your data</h2><p>Choose from objects visible to your service role.</p></div><button aria-label="Close data browser" disabled={saving} onClick={close}>✕</button></div>
    <div className="dialog-body">
      <div className="browser-path"><button className="text-button" disabled={saving} onClick={() => navigate({ database: "", schema_name: "", table: "" })}>Databases</button>{location.database && <><span>/</span><button className="text-button" disabled={saving} onClick={() => navigate({ database: location.database, schema_name: "", table: "" })}>{location.database}</button></>}{location.schema_name && <><span>/</span><button className="text-button" disabled={saving} onClick={() => navigate({ ...location, table: "" })}>{location.schema_name}</button></>}{location.table && <span>/ {location.table}</span>}</div>
      <form className="browser-search" onSubmit={event => { event.preventDefault(); void load(location, "", prefix); }}><label>Search {level}<input value={prefix} disabled={saving} maxLength={255} onChange={e => setPrefix(e.target.value)} placeholder="Name starts with…"/><small>Exact case, usually uppercase. Results load in pages of 100.</small></label><button className="secondary" disabled={loading || saving}>Search</button></form>
      <div className="browser-summary"><strong>{selection.length}/30 objects selected</strong>{(level === "objects" || level === "columns") && <button className="text-button" disabled={loading || saving || !items.length || (level === "columns" && !active)} onClick={selectVisible}>Select listed {level === "objects" ? "objects" : "columns"}</button>}</div>
      {error && <div role="alert" className="notice error">{error}</div>}
      {level === "columns" && <p>{active ? `${active.preview_columns.length}/30 preview columns selected. No columns means metadata access only in the app.` : "Select this object before choosing preview columns."}</p>}
      {loading && <p role="status">Loading accessible {level}…</p>}
      {!loading && !items.length && <div className="browser-empty">No matching {level} are visible. Check the search case, refresh metadata, or ask the client’s Snowflake administrator to grant access. The app cannot grant itself source permissions.</div>}
      <div className="browser-items">{items.map(item => <div className="browser-row" key={item.name}>
        {level === "databases" || level === "schemas" ? <button className="browser-name" disabled={saving} onClick={() => navigate(level === "databases" ? { database: item.name, schema_name: "", table: "" } : { ...location, schema_name: item.name, table: "" })}>{item.name}<span>Browse →</span></button>
        : level === "objects" ? <><label className="browser-check"><input type="checkbox" disabled={saving} checked={selection.some(o => o.catalog === location.database && o.schema_name === location.schema_name && o.table_name === item.name)} onChange={() => toggleObject(item)}/><span>{item.name}<small>{item.kind}</small></span></label><button className="text-button" disabled={saving} onClick={() => navigate({ ...location, table: item.name })}>Choose columns</button></>
        : <label className="browser-check"><input type="checkbox" disabled={saving || !active} checked={!!active?.preview_columns.includes(item.name)} onChange={() => toggleColumn(item.name)}/><span>{item.name}<small>{item.type}</small></span></label>}
      </div>)}</div>
      {next && <button className="secondary" disabled={loading || saving} onClick={() => void load(location, next, loadedPrefix)}>Load next 100</button>}
      <details className="browser-selected"><summary>Review selected objects ({selection.length})</summary>{selection.map(object => <div className="browser-row" key={keyOf(object)}><span>{object.catalog}.{object.schema_name}.{object.table_name}<small>{object.preview_columns.length} preview columns</small></span><button className="text-button" disabled={saving} onClick={() => { setSelection(previous => previous.filter(o => keyOf(o) !== keyOf(object))); setDirty(true); }}>Remove</button></div>)}</details>
      <p className="form-hint">Browsing reads metadata only. Saving checks object and column names and retains your existing key. Selection controls the app’s allowlist; Snowflake permissions remain under the client’s administrator. New tables are never selected automatically.</p>
    </div>
    <div className="dialog-actions"><button className="secondary" disabled={saving} onClick={close}>Cancel</button><button className="primary" disabled={saving || loading || !dirty} onClick={save}>{saving ? "Checking selection…" : "Save selection"}</button></div>
  </div></div>;
}
