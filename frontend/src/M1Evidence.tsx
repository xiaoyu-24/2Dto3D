import { useEffect, useState } from 'react';
import { api } from './api';

export const m1Source = 'be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c';
export const m1States: Record<string, string> = {
  awaiting_read: '解析完成 · 待读图', read_queued: '等待真实读图', reading: '正在真实读图',
  read_pending: '真实读图待联调', needs_confirmation: '读图完成 · 建模依据待生成',
  basis_recheck: '读图完成 · 端面待重新校验', basis_frozen: '建模依据已冻结',
};

const kinds: Record<string, string> = { drawing: '图纸标注', derived: '推导', trial: '试建值' };
type Region = { view: string; pdf_rect: number[]; pixel_rect: number[] };
type Finding = { label: string; value: string; kind: string; regions: string[] };
type Trial = { label: string; summary: string; basis: string; kind: string; regions: string[] };
type Evidence = {
  source_sha256: string; checkpoint_sha256: string; part_number: string;
  page: { number: number; width: number; height: number };
  regions: Record<string, Region>;
  reading?: { drawing_no: string; revision: string; pin_count: number; total_length_mm: string; findings: Finding[] };
  reviewed?: Finding[];
  trials?: Record<string, Trial>;
};
type ReadingTask = {
  state: string; error: string | null; evidence: Evidence | null;
  calls: { phase: string; passed?: boolean; request_id?: string; actual_model?: string; elapsed_ms?: number;
    usage?: { input_tokens?: number; output_tokens?: number; cache_read_input_tokens?: number } }[];
  frozen: { version: string; basis_sha256: string; checklist_sha256: string } | null;
};

export function M1Evidence({ taskId, canWrite }: { taskId: number; canWrite: boolean }) {
  const path = `/tasks/${taskId}/m1`;
  const [result, setResult] = useState<ReadingTask>();
  const [selected, setSelected] = useState('section');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const state = result?.state;
  const active = state === 'read_queued' || state === 'reading';
  useEffect(() => {
    let mounted = true;
    const refresh = () => api<ReadingTask>(path).then((data) => { if (mounted) setResult(data); })
      .catch((e: Error) => { if (mounted) setError(e.message); });
    if (!state) void refresh();
    const timer = active ? window.setInterval(refresh, 1800) : undefined;
    return () => { mounted = false; window.clearInterval(timer); };
  }, [path, state, active]);

  async function start() {
    setBusy(true); setError('');
    try {
      await api(`${path}/start`, { method: 'POST' });
      setResult(await api<ReadingTask>(path));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const evidence = result?.evidence;
  const page = evidence?.page;
  const region = evidence?.regions[selected];
  const [x, y, w, h] = region?.pixel_rect ?? [];
  const showRegion = (key: string) => {
    setSelected(key);
    document.getElementById(`m1-page-${taskId}`)?.scrollIntoView({ block: 'nearest' });
  };
  const references = (regions: string[]) => regions.map((key) =>
    <button className="text-button" key={key} onClick={() => showRegion(key)}>{evidence?.regions[key]?.view || key}</button>);

  return <section className="m1">
    <div className="section-title"><div><h2>真实读图与证据</h2><p className="muted" role="status">{state ? m1States[state] : '正在读取任务…'}</p></div>
      {canWrite && (state === 'awaiting_read' || state === 'read_pending' || state === 'needs_confirmation' || state === 'basis_recheck') &&
        <button disabled={busy} onClick={() => void start()}>{state === 'read_pending' ? '重试读图'
          : state === 'basis_recheck' ? '重新校验端面' : state === 'needs_confirmation' ? '复用读图生成依据' : '开始真实读图'}</button>}
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    {result?.error && <p className="error">{result.error}</p>}
    {result?.calls.length ? <details className="m1-calls"><summary>模型调用记录</summary><div className="table-scroll"><table>
      <thead><tr><th>请求</th><th>模型 / 请求 ID</th><th>耗时</th><th>用量（输入 / 输出）</th></tr></thead>
      <tbody>{result.calls.map((call) => <tr key={call.request_id || call.phase}>
        <td>{call.phase === 'probe' ? `图像能力题${call.passed ? ' · 通过' : ''}` : '第 1 页读图'}</td>
        <td>{call.actual_model || '待返回'}<small>{call.request_id}</small></td>
        <td>{call.elapsed_ms === undefined ? '—' : `${(call.elapsed_ms / 1000).toFixed(2)} 秒`}</td>
        <td>{call.usage?.input_tokens ?? '—'} / {call.usage?.output_tokens ?? '—'}</td>
      </tr>)}</tbody>
    </table></div></details> : null}
    {evidence && page && region && <>
      <p className="muted m1-fingerprint">原图 SHA-256 <code>{evidence.source_sha256}</code> · 第 {page.number} 页</p>
      <div className="m1-regions" aria-label="证据页区">
        {Object.entries(evidence.regions).map(([key, entry]) => <button key={key} type="button" className="secondary"
          aria-pressed={selected === key} onClick={() => setSelected(key)}>{entry.view}</button>)}
      </div>
      <div className="m1-images" id={`m1-page-${taskId}`}>
        <figure><div className="m1-sheet"><img src={`/api${path}/images/page`} width={page.width} height={page.height} alt={`原图第 ${page.number} 页`} />
          <span className="m1-rect" aria-hidden="true" style={{ left: `${x / page.width * 100}%`, top: `${y / page.height * 100}%`, width: `${w / page.width * 100}%`, height: `${h / page.height * 100}%` }} /></div><figcaption>原图第 {page.number} 页</figcaption></figure>
        <figure><a href={`/api${path}/images/${selected}`} target="_blank" rel="noreferrer"><img src={`/api${path}/images/${selected}`} alt={`${region.view}局部图`} /></a>
          <figcaption>{region.view} · PDF 坐标（pt，左下原点）<br />{region.pdf_rect.map((n) => n.toFixed(1)).join(' / ')}</figcaption></figure>
      </div>
      {evidence.reading && <>
        <p><strong>{evidence.part_number}</strong> · {evidence.reading.drawing_no} {evidence.reading.revision} · {evidence.reading.pin_count} 针 · 总长 {evidence.reading.total_length_mm} mm</p>
        {evidence.reviewed && <div className="table-scroll"><table><thead><tr><th>建模项目</th><th>结论</th><th>来源</th><th>定位</th></tr></thead>
          <tbody>{evidence.reviewed.map((item) => <tr key={item.label}><td>{item.label}</td><td>{item.value}</td><td>{kinds[item.kind]}</td><td>{references(item.regions)}</td></tr>)}
            {Object.values(evidence.trials || {}).map((item) => <tr key={item.label}>
              <td>{item.label}</td><td>{item.summary}<small>{item.basis}</small></td><td>{kinds[item.kind]}</td><td>{references(item.regions)}</td>
            </tr>)}</tbody>
        </table></div>}
        <details><summary>模型原始读图</summary>
          <ul>{evidence.reading.findings.map((item, index) => <li key={`${item.label}-${index}`}><strong>{item.label}</strong> · {item.value} {references(item.regions)}</li>)}</ul>
        </details>
      </>}
    </>}
    {result?.frozen && <div className="m1-frozen"><p><strong>已冻结 {result.frozen.version}</strong></p>
      <p><a href={`/api${path}/frozen/basis`}>下载建模依据</a> <code>{result.frozen.basis_sha256}</code></p>
      <p><a href={`/api${path}/frozen/checklist`}>下载独立验收清单</a> <code>{result.frozen.checklist_sha256}</code></p>
    </div>}
  </section>;
}
