import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, ApiError } from './api';
import './style.css';

type User = { id: number; login: string };
type Project = { id: number; name: string; role: string };
type BatchRef = { id: number; created_at: string };
type TaskFile = { id: number; task_id: number; filename: string; sha256: string; state: string; error_text: string | null };
type Batch = { id: number; files: TaskFile[] };
type Task = { id: number; state: string; attempts: number; checkpoint: { page_count: number; pages: { number: number; rotation: number; text: string }[] } | null };
type Receipt = { task_id: number; reused: boolean; message: string };
const states: Record<string, string> = { queued: '等待处理', processing: '正在解析', awaiting_read: '解析完成 · 待读图', failed: '解析失败' };

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<User>('/me').then(setUser).catch((e) => {
      if (!(e instanceof ApiError && e.status === 401)) setError(e.message);
    }).finally(() => setLoading(false));
  }, []);
  async function login(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError('');
    try {
      setUser(await api<User>('/login', { method: 'POST', body: JSON.stringify(Object.fromEntries(form)) }));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  if (loading) return <main className="loading">正在加载工作台…</main>;
  if (!user) return <main className="login-shell">
    <section className="intro"><div className="wordmark">2D <span>→</span> 3D</div><div><p className="eyebrow">DRAWING WORKSPACE</p><h1>从一张图纸，<br />开始。</h1><p>上传原始 PDF，随时查看处理进度。<br />关闭浏览器后，后台任务仍会继续。</p></div><small>机械图纸 · 项目工作台</small></section>
    <section className="login-panel"><form onSubmit={login} className="login-form"><p className="eyebrow">欢迎回来</p><h2>登录工作台</h2><p className="muted">使用项目账号继续处理图纸。</p><label>账号<input name="login" autoComplete="username" required maxLength={160} /></label><label>密码<input name="password" type="password" autoComplete="current-password" required maxLength={1024} /></label>{error && <p role="alert" className="error">{error}</p>}<button disabled={busy}>{busy ? '正在登录…' : '登录'}</button></form></section>
  </main>;
  return <Workspace user={user} onLogout={() => setUser(null)} />;
}

function Workspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState(0);
  const [batches, setBatches] = useState<BatchRef[]>([]);
  const [batchId, setBatchId] = useState(0);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [detail, setDetail] = useState<Task | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [batchLoading, setBatchLoading] = useState(true);
  const [selected, setSelected] = useState<File[]>([]);
  const storageKey = `drawing-batch:${user.id}:${projectId}`;
  const project = projects.find((p) => p.id === projectId);
  function report(e: unknown) {
    if (e instanceof ApiError && e.status === 401) onLogout();
    else setError((e as Error).message);
  }
  useEffect(() => {
    api<Project[]>('/projects').then((items) => { setProjects(items); setProjectId(items[0]?.id || 0); }).catch(report);
  }, []);
  useEffect(() => {
    let active = true;
    setBatchId(0); setBatch(null); setDetail(null); setNotice(''); setError(''); setSelected([]);
    if (!projectId) return;
    setBatchLoading(true);
    api<BatchRef[]>(`/projects/${projectId}/batches`).then((items) => {
      if (!active) return;
      setBatches(items);
      const saved = Number(localStorage.getItem(storageKey));
      setBatchId(items.some((item) => item.id === saved) ? saved : items[0]?.id || 0);
    }).catch((e) => { if (active) report(e); }).finally(() => { if (active) setBatchLoading(false); });
    return () => { active = false; };
  }, [projectId]);
  useEffect(() => {
    let active = true;
    setBatch(null); setDetail(null);
    if (!batchId) return;
    localStorage.setItem(storageKey, String(batchId));
    const refresh = () => api<Batch>(`/batches/${batchId}`).then((data) => {
      if (active) setBatch(data);
    }).catch((e) => { if (active) report(e); });
    void refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [batchId, storageKey]);
  async function ensureBatch() {
    if (batchId) return batchId;
    const keyName = `${storageKey}:request`;
    const key = localStorage.getItem(keyName) || crypto.randomUUID();
    localStorage.setItem(keyName, key);
    const result = await api<{ id: number }>(`/projects/${projectId}/batches`, {
      method: 'POST', body: JSON.stringify({ request_key: key }),
    });
    localStorage.setItem(storageKey, String(result.id));
    localStorage.removeItem(keyName);
    setBatchId(result.id);
    setBatches(await api<BatchRef[]>(`/projects/${projectId}/batches`));
    return result.id;
  }
  async function upload(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('');
    try {
      const id = await ensureBatch();
      const receipts: string[] = [];
      const failures: string[] = [];
      for (const file of selected) {
        try {
          if (file.size > 32 * 1024 * 1024) throw new Error('PDF 不能超过 32 MiB');
          const hash = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
          const key = Array.from(new Uint8Array(hash), (n) => n.toString(16).padStart(2, '0')).join('');
          const data = new FormData(); data.append('client_file_key', key); data.append('file', file);
          const receipt = await api<Receipt>(`/batches/${id}/files`, { method: 'POST', body: data });
          receipts.push(`${file.name}：${receipt.message} · ${receipt.reused ? '复用' : '创建'}任务 #${receipt.task_id}`);
        } catch (e) {
          if (e instanceof ApiError && e.status === 401) { onLogout(); return; }
          failures.push(`${file.name}：${(e as Error).message}`);
        }
      }
      setNotice(receipts.join('\n')); setError(failures.join('\n'));
      setBatch(await api<Batch>(`/batches/${id}`));
    } catch (e) { report(e); }
    finally { setBusy(false); }
  }
  async function logout() {
    try { await api('/logout', { method: 'POST' }); onLogout(); } catch (e) { report(e); }
  }
  return <div className="workspace">
    <aside><div className="wordmark">2D <span>→</span> 3D</div><p className="eyebrow">工作空间</p><div className="nav-item">◈ &nbsp; 图纸任务</div><div className="account"><span>{user.login}</span><button className="text-button" onClick={logout}>退出登录</button></div></aside>
    <main className="content"><header><div><p className="eyebrow">PROJECT / DRAWINGS</p><h1>图纸任务</h1><p className="muted">原图已接收后，后台会持续处理。你可以放心离开。</p></div><label className="project-select">当前项目<select value={projectId} disabled={busy} onChange={(e) => setProjectId(Number(e.target.value))}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label></header>
      <section className="card upload-card"><div className="section-title"><div><h2>上传 PDF 图纸</h2><p className="muted">每份最大 32 MiB · 同批次重复上传会复用已有任务</p></div><span className="tag">PDF</span></div>
        <div className="batch-controls"><label>当前批次<select value={batchId} disabled={busy || batchLoading} onChange={(e) => { setBatchId(Number(e.target.value)); setNotice(''); }}><option value={0}>新批次</option>{batches.map((b) => <option key={b.id} value={b.id}>批次 #{b.id}</option>)}</select></label>{project?.role !== 'reader' && <button className="secondary" disabled={busy || batchLoading} onClick={() => { localStorage.removeItem(storageKey); setBatchId(0); setNotice(''); }}>新建批次</button>}</div>
        {project?.role === 'reader' ? <p className="muted">当前账号可查看图纸与任务进度。</p> : <form onSubmit={upload}><label className="file-picker"><span className="upload-symbol">↑</span><strong>选择原始 PDF 文件</strong><span className="muted">支持一次选择多份图纸，逐份接收</span><input type="file" accept="application/pdf,.pdf" multiple disabled={busy || batchLoading || !projectId} onChange={(e) => setSelected(Array.from(e.target.files || []))} /></label><div className="upload-footer"><span className="muted">{selected.length ? `已选择 ${selected.length} 份图纸` : '等待选择文件'}</span><button disabled={busy || batchLoading || !projectId || !selected.length}>{busy ? '正在上传，请等待回执…' : '上传并开始处理'}</button></div></form>}
      </section>
      {notice && <p role="status" className="notice">{notice}</p>}{error && <p role="alert" className="error">{error}</p>}
      <section className="card"><div className="section-title"><div><h2>处理进度</h2><p className="muted">{batchId ? `批次 #${batchId} · 自动更新` : '上传后查看接收结果和后台进度'}</p></div><span className="count">{batch?.files.length || 0} 份图纸</span></div>
        {!batch?.files.length ? <div className="empty">本批次还没有图纸</div> : <div className="table-scroll"><table><thead><tr><th>图纸 / 内容指纹</th><th>任务</th><th>状态</th><th></th></tr></thead><tbody>{batch.files.map((file) => <tr key={file.id}><td><strong>{file.filename}</strong><small title={file.sha256}>{file.sha256.slice(0, 16)}…</small></td><td>#{file.task_id}</td><td><span className={`status ${file.state}`}>{states[file.state] || file.state}</span>{file.error_text && <small className="error-text">{file.error_text}</small>}</td><td><button className="text-button" onClick={() => api<Task>(`/tasks/${file.task_id}`).then(setDetail).catch(report)}>查看详情</button></td></tr>)}</tbody></table></div>}
      </section>
      {detail && <section className="card"><div className="section-title"><h2>任务 #{detail.id}</h2><button className="text-button" onClick={() => setDetail(null)}>收起</button></div><p>{states[detail.state]} · 已领取 {detail.attempts} 次</p>{detail.checkpoint && <><p>已解析 {detail.checkpoint.page_count} 页，原图与解析结果已保存。下一步为图纸识读。</p>{detail.checkpoint.pages.map((page) => <details key={page.number}><summary>第 {page.number} 页 · 旋转 {page.rotation}° · 提取文本</summary><pre>{page.text || '此页没有可提取文字，后续需要图像识读。'}</pre></details>)}</>}</section>}
      <footer>当前阶段：接收原图与 PDF 解析。图纸识读和三维建模将在后续阶段执行。</footer>
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
