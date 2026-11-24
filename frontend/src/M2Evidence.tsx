import { useEffect, useState } from 'react';
import { api } from './api';
import { VersionFiles, type Version } from './Versions';

export const m2States: Record<string, string> = {
  selection_ready: '联合读图完成 · 可选型号', needs_confirmation: '待确认', basis_pending: '等待建模作业',
  cad_queued: '等待隔离建模', modeling: '正在建模或验收', building: '准备交付', failed: '处理失败',
  partial: '部分完成', published: '已发布', geometry_rejected: '几何验收未通过', rejected: '几何验收未通过',
  retry_wait: '等待渠道重试',
};

type Frame = { number: number; rotation: number; width: number; height: number; image_key: string };
type Region = { page: number; view: string; pdf_rect: number[]; pixel_rect: number[] };
type Finding = { label: string; value: string; kind: string; regions: string[] };
type Candidate = { part_number: string; part_example?: string; pin_count: number | null; total_length_mm: string | null; regions: string[]; findings: Finding[];
  questions: { key: string; expected?: string }[] };
type Confirmation = { id: number; question_key: string; question: string; evidence: { key: string }[];
  answer: { value: string } | null; operator: string | null; answered_at: string | null };
type Call = { phase: string; actual_model?: string; request_id?: string; elapsed_ms?: number; usage_ceiling_cny?: string;
  usage?: { input_tokens?: number; output_tokens?: number } };
type Detail = { state: string; file_task_id: number; specification: Candidate | null; shared_read_sha256: string | null;
  evidence: { pages: Frame[]; regions: Record<string, Region>; products?: Candidate[]; reading?: { summary: string } } | null;
  confirmations: Confirmation[]; version: { id: number; state: string } | null;
  calls: Call[]; cad_calls: Call[];
  stages: { stage_name: string; state: string; attempts: number; error_text: string | null }[] };

export function M2Evidence({ taskId, canWrite }: { taskId: number; canWrite: boolean }) {
  const path = `/tasks/${taskId}/m2`;
  const [detail, setDetail] = useState<Detail>();
  const [version, setVersion] = useState<Version>();
  const [selected, setSelected] = useState<string | null>(null);
  const [shownPage, setShownPage] = useState(1);
  const [parts, setParts] = useState<string[]>([]);
  const [variants, setVariants] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const data = await api<Detail>(path);
        if (active) setDetail(data);
        if (data.version) {
          const current = await api<Version>(`/versions/${data.version.id}`);
          if (active) setVersion(current);
        }
      } catch (failure) { if (active) setError((failure as Error).message); }
    }
    void refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [path]);

  async function write(endpoint: string, body: object) {
    setBusy(true); setNotice(''); setError('');
    try {
      const response = await api<{ products?: { task_id: number; reused: boolean }[]; resumed?: boolean; reused?: boolean }>(endpoint,
        { method: 'POST', body: JSON.stringify(body) });
      setNotice(response.products ? response.products.map((item) => `${item.reused ? '复用' : '创建'}产品 #${item.task_id}`).join('、')
        : response.reused ? '已复用确认记录' : response.resumed ? '已确认，任务已续办' : '已保存确认');
      setDetail(await api<Detail>(path));
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }

  const evidence = detail?.evidence;
  const currentRegion = selected ?? detail?.confirmations.find((item) => !item.answer)?.evidence[0]?.key ?? '';
  const region = evidence?.regions[currentRegion];
  const frame = evidence?.pages.find((page) => page.number === (region?.page || shownPage));
  const [left, top, width, height] = region?.pixel_rect || [0, 0, 0, 0];
  const candidates = detail?.specification ? [detail.specification] : (evidence?.products || []);
  function references(keys: string[]) {
    return [...new Set(keys)].map((key) => <button className="text-button" type="button" key={key} onClick={() => {
      setSelected(key);
      document.getElementById(`m2-page-${taskId}`)?.scrollIntoView({ block: 'nearest' });
    }}>{evidence?.regions[key]?.view || key}</button>);
  }

  return <section className="m1">
    <h2>{detail?.specification ? '产品依据与确认' : '多页读图与型号'}</h2>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p className="notice" role="status">{notice}</p>}
    {detail?.stages.at(-1)?.error_text && <p className="error">{detail.stages.at(-1)?.error_text}</p>}
    {evidence?.reading && <p>{evidence.reading.summary}</p>}
    {frame && <div id={`m2-page-${taskId}`}>
      <div className="m1-regions" aria-label="原始页号">{evidence?.pages.map((page) => <button className="secondary" key={page.number}
        aria-pressed={frame.number === page.number} onClick={() => { setShownPage(page.number); setSelected(''); }}>第 {page.number} 页 · {page.rotation}°</button>)}</div>
      <div className="m1-images"><figure><div className="m1-sheet"><img src={`/api${path}/images/${frame.image_key}`}
        width={frame.width} height={frame.height} alt={`原图第 ${frame.number} 页`} />
        {region && <span className="m1-rect" aria-label="证据高亮" style={{ left: `${left / frame.width * 100}%`, top: `${top / frame.height * 100}%`,
          width: `${width / frame.width * 100}%`, height: `${height / frame.height * 100}%` }} />}</div>
        <figcaption>原图第 {frame.number} 页 · 旋转 {frame.rotation}°</figcaption></figure>
        {region && <figure><a href={`/api${path}/images/${currentRegion}`} target="_blank" rel="noreferrer">
          <img src={`/api${path}/images/${currentRegion}`} alt={`${region.view}局部图`} /></a>
          <figcaption>{region.view} · 原页 {region.page} · PDF 用户坐标 <br />{region.pdf_rect.map((value) => value.toFixed(1)).join(' / ')}</figcaption></figure>}</div>
    </div>}
    {candidates.map((item) => <section className="m2-candidate" key={item.part_number}>
      <div className="m2-actions">
        {!detail?.specification && canWrite && <label className="m2-check"><input type="checkbox" checked={parts.includes(item.part_number)}
          onChange={(event) => setParts(event.target.checked ? [...parts, item.part_number] : parts.filter((part) => part !== item.part_number))} />选择 {item.part_number}</label>}
        {(detail?.specification || !canWrite) && <strong>{item.part_number}</strong>}
        <span>{item.pin_count ? `${item.pin_count} 针` : ''}{item.total_length_mm ? ` · 总长 ${item.total_length_mm} mm` : ''}</span>{references(item.regions)}
      </div>
      {item.part_example && !detail?.specification && canWrite && parts.includes(item.part_number) && <label>具体料号
        <input maxLength={160} placeholder={item.part_example} value={variants[item.part_number] || ''}
          onChange={(event) => setVariants({ ...variants, [item.part_number]: event.target.value })} /></label>}
      <details><summary>尺寸与视向证据</summary><ul>{item.findings.map((finding, index) => <li key={index}>
        <strong>{finding.label}</strong> · {finding.value} {references(finding.regions)}</li>)}</ul></details>
    </section>)}
    {!detail?.specification && candidates.length > 0 && canWrite && <button disabled={!parts.length || busy}
      onClick={() => void write(`/tasks/${taskId}/products`, { part_numbers: parts.map((part) => variants[part]?.trim().toUpperCase() || part) })}>派生选中型号</button>}
    {detail?.confirmations.map((confirmation) => {
      const expected = detail.specification?.questions.find((question) => question.key === confirmation.question_key)?.expected;
      return <section className="m2-candidate" key={confirmation.id}><h3>确认 · {confirmation.question}</h3>
        <div className="m2-actions">{references(confirmation.evidence.map((item) => item.key))}</div>
        {confirmation.answer ? <p>已确认 · {confirmation.answer.value}<br /><small>{confirmation.operator} · {new Date(confirmation.answered_at!).toLocaleString()}</small></p>
          : canWrite && <form className="m2-actions" onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            void write(`/confirmations/${confirmation.id}/answer`, { answer: form.get('answer') });
          }}><label>确认结论{expected ? <select name="answer" required defaultValue=""><option value="">对照原图后选择</option><option>{expected}</option></select>
            : <input name="answer" required maxLength={2000} />}</label><button disabled={busy}>提交确认</button></form>}
      </section>;
    })}
    {version && <section className="m2-candidate"><h3>{version.version} / {version.geometry_revision} · {m2States[version.state] || version.state}</h3><VersionFiles version={version} /></section>}
    {detail && <details><summary>阶段与模型调用记录</summary>
      <p className="muted m1-fingerprint">共享读图 SHA-256 <code>{detail.shared_read_sha256 || '—'}</code></p>
      <ul>{detail.stages.map((stage) => <li key={stage.stage_name}>{stage.stage_name} · {stage.state} · 领取 {stage.attempts} 次</li>)}</ul>
      <ul>{[...detail.calls, ...detail.cad_calls].map((call, index) => <li key={index}>
        {call.phase} · {call.actual_model || '待对账'} · {call.request_id || '—'} · {call.elapsed_ms ?? '—'} ms ·
        {call.usage?.input_tokens ?? '—'} / {call.usage?.output_tokens ?? '—'} token · 费用上界 CNY {call.usage_ceiling_cny ?? '待对账'}</li>)}</ul>
    </details>}
  </section>;
}
