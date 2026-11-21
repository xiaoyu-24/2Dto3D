import { useEffect, useState } from 'react';
import { api } from './api';

type Artifact = { id: string; filename: string; sha256: string; byte_count: number };
export type Version = { id: number; batch_id: number; part_number: string; version: string; geometry_revision: string; state: string;
  geometry_sha256: string | null; files: Record<string, Artifact> };
const labels: Record<string, string> = { html: 'HTML', step: 'STEP', report: '验收报告' };
const states: Record<string, string> = { published: '已发布', partial: '部分完成', building: '等待/进行中', failed: '处理失败', rejected: '验收未通过' };

export function VersionFiles({ version }: { version: Version }) {
  return <div className="m2-actions">{Object.entries(version.files).map(([kind, file]) =>
    <a key={file.id} href={`/api/artifacts/${file.id}/download`} title={`${file.byte_count} 字节 · SHA-256 ${file.sha256}`}>
      下载 {labels[kind]}{version.state === 'partial' && '（部分完成）'}</a>)}</div>;
}

export function Versions({ projectId, batchId }: { projectId: number; batchId: number }) {
  const [filters, setFilters] = useState('');
  const [versions, setVersions] = useState<Version[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const path = `/projects/${projectId}`;
  useEffect(() => {
    let active = true;
    const refresh = () => api<Version[]>(`${path}/versions?${filters}`).then((items) => { if (active) setVersions(items); })
      .catch((failure: Error) => { if (active) setError(failure.message); });
    void refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [path, filters]);

  async function download() {
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api${path}/zip`, { method: 'POST', headers: {
        'Content-Type': 'application/json', 'X-Requested-With': 'drawing-web',
      }, body: JSON.stringify({ version_ids: selected }) });
      if (!response.ok) throw new Error((await response.json()).detail || '无法下载 ZIP');
      const link = document.createElement('a');
      link.href = URL.createObjectURL(await response.blob());
      link.download = `project-${projectId}-deliveries.zip`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }

  return <section className="card" aria-label="历史交付">
    <div className="section-title"><h2>历史交付</h2><button className="secondary" disabled={!selected.length || busy} onClick={() => void download()}>下载选中 ZIP</button></div>
    <form className="m2-actions" onSubmit={(event) => {
      event.preventDefault();
      const params = new URLSearchParams();
      for (const [key, value] of new FormData(event.currentTarget)) if (String(value).trim()) params.set(key, String(value).trim());
      setError(''); setSelected([]); setFilters(params.toString());
    }}>
      <label>批次号<input name="batch_id" type="number" min={1} placeholder={batchId ? String(batchId) : ''} /></label>
      <label>料号<input name="part_number" maxLength={160} /></label>
      <label>版本<input name="version" maxLength={192} /></label><button type="submit">查询</button></form>
    {error && <p className="error" role="alert">{error}</p>}
    {versions.length ? <div className="table-scroll"><table><thead><tr><th>打包</th><th>料号 / 批次</th><th>版本</th><th>状态</th><th>下载</th></tr></thead>
      <tbody>{versions.map((item) => <tr key={item.id}><td><input type="checkbox" aria-label={`打包 ${item.part_number} ${item.version}`}
        disabled={!Object.keys(item.files).length} checked={selected.includes(item.id)} onChange={(event) =>
          setSelected(event.target.checked ? [...selected, item.id] : selected.filter((id) => id !== item.id))} /></td>
        <td><strong>{item.part_number}</strong><small>批次 #{item.batch_id}</small></td><td>{item.version} / {item.geometry_revision}</td>
        <td><span className={`status ${item.state}`}>{states[item.state] || item.state}</span></td><td><VersionFiles version={item} /></td></tr>)}</tbody></table></div>
      : <p className="empty">没有匹配的交付版本</p>}
  </section>;
}
