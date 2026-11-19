"use client";
import { useEffect, useRef, useState, FormEvent } from "react";
import { api } from "./connector-api";
import DataBrowser from "./data-browser";
type ObjectConfig = { name: string; catalog: string; schema_name: string; table_name: string; preview_columns: string[]; object_kind?: "TABLE" | "VIEW" };
type Setup = { connection_id: string; version: number; public_key: string; fingerprint: string; sql: string; diagnostic_sql: string };
type Platform = "databricks" | "snowflake";
type Profile = { id: string; version: number; access_scope?: { catalog: string; schema_name: string } | null; name: string; platform?: Platform; workspace_url?: string; warehouse_id?: string; client_id?: string; account?: string; warehouse?: string; username?: string; role?: string; objects: ObjectConfig[]; status: string; checked_at: string | null; error_code: string | null };
type Result = { platform: string; columns: { name: string; type_name: string }[]; rows: (string | null)[][]; statement_id: string };
type FormObject = Omit<ObjectConfig, "preview_columns"> & { columns: string };
type Form = { platform: Platform; name: string; workspace_url: string; warehouse_id: string; client_id: string; client_secret: string; account: string; warehouse: string; username: string; role: string; scope_catalog: string; scope_schema: string; objects: FormObject[] };
const emptyObject = (): FormObject => ({ name: "", catalog: "", schema_name: "", table_name: "", columns: "" });
const emptyForm = (): Form => ({ platform: "databricks", name: "", workspace_url: "", warehouse_id: "", client_id: "", client_secret: "", account: "", warehouse: "", username: "", role: "", scope_catalog: "", scope_schema: "", objects: [emptyObject()] });
function testFailure(profile: Profile): string {
  if (profile.error_code === "SNOWFLAKE_KEY_AUTHENTICATION_FAILED") return "Snowflake rejected the key-pair login. Check that the service user exists in this account and that RSA_PUBLIC_KEY_FP or RSA_PUBLIC_KEY_2_FP matches the public key fingerprint shown in this setup. Also check the account identifier, LOGIN_NAME, and your computer’s clock. Keep this connection’s saved key.";
  if (profile.error_code === "SNOWFLAKE_AUTHENTICATION_FAILED") return "Snowflake rejected the login. Check the service user, account identifier, role assignment, and account authentication policies.";
  if (profile.error_code === "SNOWFLAKE_OBJECT_ACCESS_FAILED" || profile.error_code === "OBJECT_NOT_VISIBLE") return "The configured object is missing or unavailable to the service role. Check exact database/schema/table names and the generated USAGE and SELECT grants.";
  return `Connection test failed (${profile.error_code || "unknown error"}). Check the identity, permissions, account details, and network access.`;
}
function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    plug: <><path d="m8 3 0 5m8-5v5M6 8h12v4a6 6 0 0 1-12 0V8Zm6 10v4"/></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/></>,
    arrow: <path d="M5 12h14m-6-6 6 6-6 6"/>,
    plus: <path d="M12 5v14M5 12h14"/>,
    x: <path d="m6 6 12 12M6 18 18 6"/>,
    book: <><path d="M12 5v15M3 4c4-1 6 0 9 1 3-1 5-2 9-1v15c-4-1-6 0-9 1-3-1-5-2-9-1V4Z"/></>,
    layers: <><path d="m12 3 10 5-10 5L2 8l10-5Zm-10 9 10 5 10-5M2 16l10 5 10-5"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.database}</svg>;
}
export default function Connections() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [setup, setSetup] = useState<{ profile: Profile; details: Setup } | null>(null);
  const [browsing, setBrowsing] = useState<Profile | null>(null);
  const [useSchemaScope, setUseSchemaScope] = useState(true);
  const [previewProfile, setPreviewProfile] = useState<Profile | null>(null);
  const [liveObject, setLiveObject] = useState("");
  const [copied, setCopied] = useState(false);
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<Form>(emptyForm);
  const [busy, setBusy] = useState("");
  const [sampleObjects, setSampleObjects] = useState<string[]>([]);
  const [sample, setSample] = useState("finance_lines");
  const [result, setResult] = useState<Result | null>(null);
  const [exploring, setExploring] = useState(false);
  const [deleting, setDeleting] = useState<Profile | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  async function refresh() {
    try { setProfiles((await api<{ connections: Profile[] }>("connections")).connections); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    if (!modal) return;
    dialog.current?.querySelector<HTMLInputElement>("input")?.focus();
    const onEscape = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) { setModal(false); setForm(emptyForm()); } };
    document.addEventListener("keydown", onEscape);
    return () => document.removeEventListener("keydown", onEscape);
  }, [modal, busy]);
  function open(profile?: Profile) {
    setError(""); setMessage(""); setEditing(profile?.id || null); setUseSchemaScope(profile ? !!profile.access_scope : true);
    setForm(profile ? { ...emptyForm(), platform: profile.platform || "databricks", name: profile.name, workspace_url: profile.workspace_url || "", warehouse_id: profile.warehouse_id || "", client_id: profile.client_id || "", account: profile.account || "", warehouse: profile.warehouse || "", username: profile.username || "", role: profile.role || "", scope_catalog: profile.access_scope?.catalog || "", scope_schema: profile.access_scope?.schema_name || "", objects: profile.objects.map(o => ({ ...o, columns: o.preview_columns.join(", ") })) } : emptyForm());
    setModal(true);
  }
  async function showSetup(profile: Profile) {
    setBusy(profile.id); setError(""); setCopied(false);
    try { setSetup({ profile, details: await api<Setup>(`connections/${profile.id}/setup`) }); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  async function test(profile: Profile) {
    setBusy(profile.id); setError(""); setMessage("");
    try {
      const checked = await api<Profile>(`connections/${profile.id}/test`, "POST");
      setProfiles(previous => previous.map(p => p.id === checked.id ? checked : p));
      setSetup(previous => previous?.profile.id === checked.id ? { ...previous, profile: checked } : previous);
      if (checked.status === "verified" || checked.status === "connected") { setSetup(null); setMessage(checked.status === "connected" ? `${checked.name}: login verified. Browse and select your data next.` : `${checked.name}: warehouse and approved object access verified.`); if (checked.status === "connected") setBrowsing(checked); }
      else setError(testFailure(checked));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy("save"); setError("");
    const intent = ((event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null)?.value;
    const shared = { platform: form.platform, name: form.name, objects: form.platform === "snowflake" && useSchemaScope && !editing ? [] : form.objects.map(({ columns, ...o }) => ({ ...o, preview_columns: columns.split(",").map(v => v.trim()).filter(Boolean) })) };
    const body = form.platform === "snowflake" ? { ...shared, account: form.account, warehouse: form.warehouse, username: form.username, role: form.role, access_scope: useSchemaScope ? { catalog: form.scope_catalog, schema_name: form.scope_schema } : null } : { ...shared, workspace_url: form.workspace_url, warehouse_id: form.warehouse_id, client_id: form.client_id, client_secret: form.client_secret || undefined };
    try {
      const profile = await api<Profile>(editing ? `connections/${editing}` : "connections", editing ? "PUT" : "POST", body);
      setForm(emptyForm()); setModal(false); await refresh();
      setMessage(`${profile.name} saved. ${form.platform === "snowflake" ? "Complete the Snowflake administrator setup, then test access." : intent === "test" ? "Testing access…" : "Run a connection test when you are ready."}`);
      if (form.platform === "snowflake") await showSetup(profile);
      else if (intent === "test") await test(profile);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  async function explore() {
    setExploring(true); setPreviewProfile(null); setBusy("sample"); setError("");
    try {
      setSampleObjects((await api<{ objects: string[] }>("objects")).objects);
      setResult(await api<Result>(`objects/${sample}/preview?limit=5`));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  async function preview(profile: Profile, objectName?: string) {
    setBusy(profile.id); setError("");
    try {
      const object = profile.objects.find(o => o.preview_columns.length && (!objectName || o.name === objectName));
      if (!object) throw new Error("Add approved preview columns in Edit connection first.");
      setPreviewProfile(profile); setLiveObject(object.name);
      setResult(await api<Result>(`connections/${profile.id}/objects/${encodeURIComponent(object.name)}/preview?limit=5`));
      setExploring(true);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  async function remove() {
    if (!deleting) return;
    setBusy("delete");
    try { await api(`connections/${deleting.id}`, "DELETE"); setDeleting(null); await refresh(); setMessage("Connection removed from this computer. Your source data was not changed."); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  function field(key: keyof Omit<Form, "objects">, value: string) { setForm(previous => ({ ...previous, [key]: value })); }
  function objectField(index: number, key: keyof FormObject, value: string) { setForm(previous => ({ ...previous, objects: previous.objects.map((o, i) => i === index ? { ...o, [key]: value } : o) })); }
  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="/"><span className="brand-mark">c<span>·</span></span>context<span className="beta">LOCAL</span></a>
      <div className="workspace"><span className="workspace-avatar">P</span><div>Personal workspace<small>Development environment</small></div></div>
      <span className="nav-label">WORKSPACE</span>
      <nav><span className="nav-item active"><Icon name="plug"/>Connections<span className="nav-count">{profiles.length}</span></span><span className="nav-item unavailable"><Icon name="layers"/>Semantic models<small>Soon</small></span><span className="nav-item unavailable"><Icon name="grid"/>Insights<small>Soon</small></span></nav>
      <div className="sidebar-bottom"><Icon name="shield"/><strong>Your data stays yours</strong><p>Connect with read-only access. Keep control of the source.</p><span className="local-dot"/>Running on this computer</div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><span>Workspace <span className="slash">/</span> <strong>Connections</strong></span><span className="profile-avatar">P</span></header>
      <main>
        <div className="page-heading"><div><div className="eyebrow">YOUR DATA FOUNDATION</div><h1>Connections</h1><p>Bring your business data into one shared context.</p></div><button className="primary" onClick={() => open()}><Icon name="plus" size={17}/>Add connection</button></div>
        <div className="local-notice"><span className="local-dot"/><span><strong>Local development workspace.</strong> Profiles belong to this Windows user. Team sign-in and customer isolation are part of the hosted version.</span></div>
        {error && <div className="notice error" role="alert">{error}<button aria-label="Dismiss error" onClick={() => setError("")}><Icon name="x" size={16}/></button></div>}
        {message && <div className="notice success" role="status">{message}<button aria-label="Dismiss message" onClick={() => setMessage("")}><Icon name="x" size={16}/></button></div>}
        <section className="demo-card"><div className="demo-art"><Icon name="database" size={32}/><span className="art-dot one"/><span className="art-dot two"/></div><div className="demo-copy"><span className="tag">NO CLOUD ACCOUNT NEEDED</span><h2>Take a look around with sample data</h2><p>Explore 9 fictional ERP tables across finance, inventory, and orders.<br/>A working local demo, ready when you are.</p></div><button className="secondary" onClick={explore} disabled={!!busy}>{busy === "sample" ? "Loading…" : "Explore sample data"}<Icon name="arrow" size={17}/></button></section>
        <div className="section-heading"><h2>Your connections <span>{profiles.length}</span></h2><span>Databricks & Snowflake</span></div>
        {loading ? <div className="empty-state">Loading connections…</div> : profiles.length === 0 ? <section className="empty-state"><div className="empty-icon"><Icon name="plug" size={27}/></div><h3>A home for your first connection</h3><p>Add your workspace and service identity.<br/>We’ll check access before you start exploring.</p><button className="text-button" onClick={() => open()}>Connect a platform <Icon name="arrow" size={17}/></button></section> : <div className="connection-grid">{profiles.map(profile => <article className="connection-card" key={profile.id}><div className="connection-top"><div className="db-logo"><Icon name="layers" size={25}/></div><span className={`status ${profile.status}`}>{profile.status === "verified" ? "Verified" : profile.status === "failed" ? "Needs attention" : profile.status === "connected" ? "Connected · choose data" : "Saved · not tested"}</span></div><h3>{profile.name}</h3><span className="platform-label">{profile.platform === "snowflake" ? "Snowflake · Key-pair authentication" : "Databricks · Google Cloud"}</span><p className="host">{profile.platform === "snowflake" ? `${profile.account} · ${profile.warehouse}` : profile.workspace_url?.replace("https://", "")}</p><div className="connection-meta"><span>{profile.objects.length} approved object{profile.objects.length !== 1 ? "s" : ""}</span><span>Read-only setup</span></div>{profile.checked_at && <small className="tested-at">Last checked {new Date(profile.checked_at).toLocaleString()}</small>}<div className="card-actions"><button className="secondary small" disabled={!!busy} onClick={() => test(profile)}>{busy === profile.id ? "Working…" : "Test connection"}</button>{profile.platform === "snowflake" && <><button className="text-button" onClick={() => setBrowsing(profile)} disabled={!!busy}>Browse data</button><button className="text-button" onClick={() => showSetup(profile)} disabled={!!busy}>Setup SQL</button></>}<button className="text-button" onClick={() => open(profile)} disabled={!!busy}>Edit</button><button className="text-button" onClick={() => preview(profile)} disabled={!!busy}>Preview</button><button className="delete-button" aria-label={`Remove ${profile.name}`} onClick={() => setDeleting(profile)} disabled={!!busy}><Icon name="x" size={16}/></button></div></article>)}</div>}
        {exploring && <section className="results-panel"><div className="section-heading"><div><h2>Data preview</h2><p>{!result ? "Loading preview…" : result.platform === "local-duckdb-demo" ? "Synthetic data · local database · first 5 rows" : "Live source · approved columns · first 5 rows"}</p></div><button className="text-button" onClick={() => { setExploring(false); setResult(null); }}>Close <Icon name="x" size={16}/></button></div>{result?.platform === "local-duckdb-demo" && <div className="sample-controls"><label htmlFor="sample-object">Sample object</label><select id="sample-object" value={sample} onChange={e => setSample(e.target.value)}>{sampleObjects.map(name => <option key={name}>{name}</option>)}</select><button className="secondary small" onClick={explore} disabled={!!busy}>Load preview</button></div>}{previewProfile && <div className="sample-controls"><label htmlFor="live-object">Approved object</label><select id="live-object" value={liveObject} disabled={!!busy} onChange={e => void preview(previewProfile, e.target.value)}>{previewProfile.objects.filter(o => o.preview_columns.length).map(o => <option key={o.name} value={o.name}>{o.catalog}.{o.schema_name}.{o.table_name}</option>)}</select></div>}<div className="table-wrap">{result && <table><thead><tr>{result.columns.map((c, i) => <th key={i}>{c.name}<small>{c.type_name}</small></th>)}</tr></thead><tbody>{result.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell === null ? <span className="null">null</span> : cell}</td>)}</tr>)}</tbody></table>}</div>{result?.rows.length === 0 && <p>No rows returned.</p>}</section>}
        <section className="steps"><div><span>01</span><h3>Connect your platform</h3><p>Your workspace. Your credentials.<br/>A connection you control.</p></div><div><span>02</span><h3>Verify access</h3><p>Check the warehouse and approved<br/>objects with your service identity.</p></div><div><span>03</span><h3>Build shared understanding</h3><p>Use the connection as the foundation<br/>for your business semantics.</p></div></section>
        <footer><span><Icon name="shield" size={15}/>Secrets encrypted for your Windows user</span><span>Redshift and SAP connectors are next.</span></footer>
      </main>
    </div>
    {modal && <div className="overlay"><div className="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title" ref={dialog} onKeyDown={event => {
      if (event.key !== "Tab") return;
      const nodes = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input, textarea, select, a[href]') || []);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}><div className="dialog-head"><div><span className="eyebrow">{form.platform === "snowflake" ? "SNOWFLAKE" : "GOOGLE CLOUD DATABRICKS"}</span><h2 id="dialog-title">{editing ? "Edit connection" : "Add a connection"}</h2><p>Configure your own workspace and approved data.</p></div><button aria-label="Close connection form" disabled={!!busy} onClick={() => { setModal(false); setForm(emptyForm()); }}><Icon name="x"/></button></div>
      <form onSubmit={save}><div className="dialog-body">
        {error && <div className="notice error" role="alert">{error}</div>}
        <label>Data platform<select disabled={!!editing} value={form.platform} onChange={e => setForm(previous => ({ ...emptyForm(), platform: e.target.value as Platform, name: previous.name, warehouse: "ERP_API_WH", username: "SVC_ERP_CONNECTOR", role: "ERP_CONNECTOR_READER", objects: previous.objects }))}><option value="databricks">Databricks on Google Cloud</option><option value="snowflake">Snowflake</option></select><small>{editing ? "Create a separate profile to use another platform." : "Choose where your data is stored."}</small></label>
        <label>Connection name<input required maxLength={100} value={form.name} onChange={e => field("name", e.target.value)} placeholder="e.g. Finance development"/></label>
        {form.platform === "databricks" ? <>
        <div className="form-section"><span>1</span><h3>Workspace details</h3></div>
        <label>Workspace URL<input required type="url" value={form.workspace_url} onChange={e => field("workspace_url", e.target.value)} placeholder="https://123456789.7.gcp.databricks.com"/><small>Copy the URL from your Google Cloud Databricks workspace.</small></label>
        <label>SQL warehouse ID<input required pattern="[a-fA-F0-9]{8,64}" value={form.warehouse_id} onChange={e => field("warehouse_id", e.target.value)} placeholder="e.g. a1b2c3d4e5f67890"/><small>Find this under SQL Warehouses → Connection details.</small></label>
        <div className="form-section"><span>2</span><h3>Service identity</h3></div>
        <label>Service principal client ID<input required value={form.client_id} onChange={e => field("client_id", e.target.value)} placeholder="Application ID (UUID)"/></label>
        <label>OAuth client secret<input required={!editing} type="password" autoComplete="new-password" value={form.client_secret} minLength={8} maxLength={4096} onChange={e => field("client_secret", e.target.value)} placeholder={editing ? "Leave blank to keep the saved secret" : "Enter your Databricks OAuth secret"}/><small>Encrypted on the backend. Never stored in browser storage or returned by the API.</small></label>
        </> : <>
          <div className="form-section"><span>1</span><h3>Account and warehouse</h3></div>
          <label>Account identifier<input required value={form.account} maxLength={255} onChange={e => field("account", e.target.value)} placeholder="organization-account"/><small>Use the account identifier from Snowflake connection details, without https:// or .snowflakecomputing.com.</small></label>
          <div className="form-grid"><label>Warehouse<input required value={form.warehouse} onChange={e => field("warehouse", e.target.value)} placeholder="ERP_API_WH"/></label><label>Reader role<input required value={form.role} onChange={e => field("role", e.target.value)} placeholder="ERP_CONNECTOR_READER"/></label></div>
          <div className="form-section"><span>2</span><h3>Service identity · RSA key pair</h3></div>
          <label>Service user name<input required value={form.username} onChange={e => field("username", e.target.value)} placeholder="SVC_ERP_CONNECTOR"/><small>Use a dedicated Snowflake user with TYPE = SERVICE.</small></label>
          <div className="permission-note"><Icon name="shield"/><p>The app generates a dedicated RSA key on the backend. Next, copy the setup SQL into Snowflake to register its public key and grant read access. Choose a new service user and role for this connection. Existing connections keep their saved key.</p></div>
        </>}
        {form.platform === "snowflake" && <><label className="scope-choice"><input type="checkbox" checked={useSchemaScope} onChange={e => setUseSchemaScope(e.target.checked)}/>Discover data from an administrator-approved schema</label>{useSchemaScope && <><div className="form-grid"><label>Approved database<input required value={form.scope_catalog} onChange={e => field("scope_catalog", e.target.value)} placeholder="ERP_DEMO"/></label><label>Approved schema<input required value={form.scope_schema} onChange={e => field("scope_schema", e.target.value)} placeholder="ERP_CONNECTOR_DEMO_V1"/></label></div><div className="permission-note"><Icon name="shield"/><p>Your administrator reviews SQL granting this service role SELECT on <strong>all current tables and views in this schema</strong>. Future objects are excluded. After connection, choose tables and columns in Browse data. For narrower source permissions, turn this off and configure individual objects.</p></div></>}</>}
        {(form.platform !== "snowflake" || !useSchemaScope) && <>
        <div className="form-section"><span>3</span><h3>Approved data objects</h3></div>
        <p className="form-hint">Choose the tables or views this connection may inspect. Data access is enforced by your source platform permissions.</p>
        {form.objects.map((object, i) => <fieldset key={i}><legend>Object {i + 1}</legend><div className="form-grid"><label>Object label<input required pattern="[a-zA-Z0-9_-]+" value={object.name} onChange={e => objectField(i, "name", e.target.value)} placeholder="finance_lines"/></label><label>{form.platform === "snowflake" ? "Database" : "Catalog"}<input required value={object.catalog} onChange={e => objectField(i, "catalog", e.target.value)} placeholder={form.platform === "snowflake" ? "ERP_DEMO" : "your_catalog"}/></label><label>Schema<input required value={object.schema_name} onChange={e => objectField(i, "schema_name", e.target.value)} placeholder={form.platform === "snowflake" ? "ERP_CONNECTOR_DEMO_V1" : "approved"}/></label><label>Table or view<input required value={object.table_name} onChange={e => objectField(i, "table_name", e.target.value)} placeholder="finance_lines"/></label></div>{form.platform === "snowflake" && <label>Object type<select value={object.object_kind || "TABLE"} onChange={e => objectField(i, "object_kind", e.target.value)}><option value="TABLE">Table</option><option value="VIEW">View</option></select></label>}<label>Preview columns <span className="optional">optional</span><input value={object.columns} onChange={e => objectField(i, "columns", e.target.value)} placeholder="company_code, amount, currency"/><small>Comma-separated. Leave empty to disable row previews.</small></label>{form.objects.length > 1 && <button className="text-button" type="button" onClick={() => setForm(previous => ({ ...previous, objects: previous.objects.filter((_, index) => index !== i) }))}>Remove object</button>}</fieldset>)}
        <button type="button" className="text-button" disabled={form.objects.length >= 30} onClick={() => setForm(previous => ({ ...previous, objects: [...previous.objects, emptyObject()] }))}><Icon name="plus" size={16}/>Add another object</button>
        </>}
        <div className="permission-note"><Icon name="shield"/><p>{form.platform === "snowflake" ? <>Your service user’s role needs <strong>USAGE</strong> on the warehouse, database and schema, and <strong>SELECT</strong> on these objects. Enter names with their exact stored case, usually uppercase.</> : <>Your service principal needs <strong>CAN USE</strong> on the warehouse, parent usage privileges, and <strong>SELECT</strong> on these objects.</>} Saving a connection does not grant permissions.</p></div>
      </div><div className="dialog-actions">{form.platform === "databricks" && <button type="submit" value="save" className="secondary" disabled={!!busy}>Save for later</button>}<button type="submit" value="test" className="primary" disabled={!!busy}>{busy ? "Saving…" : form.platform === "snowflake" ? "Continue to setup SQL" : "Save & test connection"}<Icon name="arrow" size={17}/></button></div></form>
    </div></div>}
    {setup && <div className="overlay"><div className="dialog" role="dialog" aria-modal="true" aria-labelledby="setup-title" ref={dialog} onKeyDown={event => {
      if (event.key === "Escape" && !busy) setSetup(null);
      if (event.key !== "Tab") return;
      const nodes = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), textarea') || []);
      if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === nodes.at(-1)) { event.preventDefault(); nodes[0]?.focus(); }
    }}><div className="dialog-head"><div><span className="eyebrow">SNOWFLAKE · ADMINISTRATOR SETUP</span><h2 id="setup-title">Authorize your connection</h2><p>{setup.profile.name}</p></div><button autoFocus aria-label="Close setup" disabled={!!busy} onClick={() => setSetup(null)}><Icon name="x"/></button></div>
    <div className="dialog-body">
      <p>Your data is already loaded. Run the SQL below once in a Snowflake SQL worksheet as an administrator with permission to create users, roles, and grant access. For your personal trial, use ACCOUNTADMIN.</p>
      <p>This creates a new service user and reader role. Use fresh names; stop if either already exists. The SQL grants the approved schema or listed objects, plus usage on their parents and warehouse. Schema scope includes all current tables/views; it does not grant future objects. It does not reload data. After editing an existing connection, ask your administrator to apply only the necessary grant changes.</p>
      <label>Setup SQL<textarea className="setup-sql" readOnly spellCheck={false} value={setup.details.sql}/></label>
      <button className="secondary" onClick={async () => { try { await navigator.clipboard.writeText(setup.details.sql); setCopied(true); } catch { setError("Select and copy the SQL from the text box."); } }}>{copied ? "Copied" : "Copy SQL"}</button>
      <p><strong>Public key fingerprint</strong><br/><code>{setup.details.fingerprint}</code></p>
      <label>Check the registered service identity<textarea readOnly rows={2} value={setup.details.diagnostic_sql}/><small>Run this read-only command in Snowflake as your administrator. Compare RSA_PUBLIC_KEY_FP (or RSA_PUBLIC_KEY_2_FP) with the fingerprint above, and check LOGIN_NAME against the configured service user. Do not replace a key until you confirm this dedicated identity belongs to this connection.</small></label>
      <div className="permission-note"><Icon name="shield"/><p>Only the public key appears here. The private key stays encrypted for your Windows user on this computer. Reopening this step keeps the same key. Warehouse queries may use Snowflake credits.</p></div>
    </div><div className="setup-test-feedback" aria-busy={!!busy}>
      {busy ? <p role="status">Testing your Snowflake login, warehouse, and approved objects. Please wait…</p> : error ? <div className="notice error" role="alert"><strong>Connection test needs attention.</strong> {error}</div> : <p>After all setup statements succeed, test access here.</p>}
    </div><div className="dialog-actions"><button className="secondary" disabled={!!busy} onClick={() => setSetup(null)}>Finish later</button><button className="primary" disabled={!!busy} onClick={() => test(setup.profile)}>{busy ? "Testing…" : "I ran the SQL · Test connection"}</button></div></div></div>}
    {browsing && <DataBrowser key={`${browsing.id}:${browsing.version}`} id={browsing.id} version={browsing.version} initial={browsing.objects} onClose={() => setBrowsing(null)} onSaved={async () => { setBrowsing(null); await refresh(); setMessage("Data selection saved. Test the connection to verify access, then choose Preview."); }}/>}
    {deleting && <div className="overlay"><div className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title"><h2 id="delete-title">Remove {deleting.name}?</h2><p>This removes the saved profile and encrypted secret from this computer. Your source data stays in place.</p><div className="dialog-actions"><button className="secondary" onClick={() => setDeleting(null)}>Keep connection</button><button className="danger" disabled={!!busy} onClick={remove}>Remove connection</button></div></div></div>}
  </div>;
}
