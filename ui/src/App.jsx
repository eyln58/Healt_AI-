import { useEffect, useState, useMemo } from 'react';
import './styles.css';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const WIZARD = [
  { key: 'intake',   label: 'Patient Intake' },
  { key: 'pipeline', label: 'AI Analysis'    },
  { key: 'review',   label: 'Physician Review' },
  { key: 'report',   label: 'Signed Note'   },
];

const PIPE_NODES = [
  { key: 'condition_extractor',  label: 'Diagnosis\nIdentification', icon: '🔬' },
  { key: 'medication_extractor', label: 'Medication\nReview',        icon: '💊' },
  { key: 'condition_coder',      label: 'ICD-10\nCoding',           icon: '🏷️' },
  { key: 'medication_coder',     label: 'RxNorm\nCoding',           icon: '📦' },
  { key: 'soap_drafter',         label: 'Clinical\nNote Draft',     icon: '📝' },
];

const PARALLEL_AGENTS = [
  { key:'condition_extractor',  icon:'🔬', label:'Diagnosis Identification', color:'purple', delay:0    },
  { key:'medication_extractor', icon:'💊', label:'Medication Review',        color:'cyan',   delay:0.2  },
  { key:'condition_coder',      icon:'🏷️', label:'ICD-10 Coding',           color:'blue',   delay:0.4  },
  { key:'medication_coder',     icon:'📦', label:'RxNorm Coding',            color:'teal',   delay:0.6  },
  { key:'soap_drafter',         icon:'📝', label:'Clinical Note Synthesis',  color:'amber',  delay:0.8  },
];

const RESULT_TABS = [
  { key: 'structured', label: 'Clinical Findings' },
  { key: 'coding',     label: 'Medical Codes'    },
  { key: 'audit',      label: 'Processing Log'   },
];

const fetchJson = async (url, init) => {
  const res = await fetch(url, init);
  if (!res.ok) { const t = await res.text(); throw new Error(`${res.status}: ${t}`); }
  return res.json();
};

const diffSoap = (a, b) => {
  const before = (a || '').split('\n').filter(Boolean);
  const after  = (b || '').split('\n').filter(Boolean);
  const added   = after.filter(l => !before.includes(l)).length;
  const removed = before.filter(l => !after.includes(l)).length;
  const changed = a === b ? 0 : Math.abs(after.length - before.length) + added + removed;
  return { added, removed, changed };
};

export default function App() {
  const [status,         setStatus]         = useState('idle');
  const [loading,        setLoading]        = useState(false);
  const [text,           setText]           = useState('');
  const [files,          setFiles]          = useState([]);
  const [runId,          setRunId]          = useState('');
  const [soapDraft,      setSoapDraft]      = useState('');
  const [serverSoap,     setServerSoap]     = useState('');
  const [finalNote,      setFinalNote]      = useState('');
  const [conditions,     setConditions]     = useState([]);
  const [medications,    setMedications]    = useState([]);
  const [conditionCodes, setConditionCodes] = useState([]);
  const [medicationCodes,setMedicationCodes]= useState([]);
  const [codedEntities,  setCodedEntities]  = useState([]);
  const [auditLog,       setAuditLog]       = useState([]);
  const [sourceFiles,    setSourceFiles]    = useState([]);
  const [reviewerName,   setReviewerName]   = useState('Clinician');
  const [reviewNotes,    setReviewNotes]    = useState('');
  const [health,         setHealth]         = useState({ model_name: 'groq/llama-3.1-8b-instant', llm_enabled: false });
  const [notice,         setNotice]         = useState('');
  const [error,          setError]          = useState('');
  const [activeTab,      setActiveTab]      = useState('structured');

  useEffect(() => {
    fetchJson(`${API_URL}/health`).then(setHealth).catch(() => {});
  }, []);

  const wizardStep = useMemo(() => {
    if (loading)                        return 'pipeline';
    if (status === 'awaiting_approval') return 'review';
    if (status === 'completed')         return 'report';
    return 'intake';
  }, [loading, status]);

  const wizardIdx = WIZARD.findIndex(s => s.key === wizardStep);

  const apply = (data) => {
    setRunId(data.run_id || '');
    setStatus(data.status || 'idle');
    setServerSoap(data.soap_draft || '');
    setSoapDraft(data.soap_draft || '');
    setFinalNote(data.final_note || '');
    setConditions(data.conditions || []);
    setMedications(data.medications || []);
    setConditionCodes(data.condition_codes || []);
    setMedicationCodes(data.medication_codes || []);
    setCodedEntities(data.coded_entities || []);
    setAuditLog(data.audit_log || []);
    setSourceFiles(data.source_files || []);
    setReviewerName(data.reviewer_name || 'Clinician');
    setReviewNotes(data.review_notes || '');
  };

  const clear = () => { setError(''); setNotice(''); };

  const onProcess = async () => {
    clear(); setLoading(true);
    try {
      const fd = new FormData();
      if (text.trim()) fd.append('text', text.trim());
      files.forEach(f => fd.append('files', f));
      const [data] = await Promise.all([
        fetchJson(`${API_URL}/process`, { method: 'POST', body: fd }),
        new Promise(r => setTimeout(r, 4000)), // min display time for animation
      ]);
      apply(data);
      setNotice('Analysis complete — please review the clinical note before signing.');
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const onUpload = async () => {
    clear();
    if (!files.length) { setError('Select at least one file.'); return; }
    setLoading(true);
    try {
      const fd = new FormData();
      files.forEach(f => fd.append('files', f));
      const data = await fetchJson(`${API_URL}/upload`, { method: 'POST', body: fd });
      setRunId(data.run_id); setStatus(data.status);
      setSourceFiles(data.stored_files || []);
      setNotice('Documents saved. Click "Analyse Saved Documents" when ready.');
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const onProcessStorage = async () => {
    clear();
    if (!runId) { setError('No stored run found.'); return; }
    setLoading(true);
    try {
      apply(await fetchJson(`${API_URL}/process-storage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: runId, text }),
      }));
      setNotice('Documents analysed — please review and sign the clinical note.');
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const submitReview = async (approve) => {
    clear();
    if (!runId || !soapDraft.trim()) { setError('A SOAP draft is required.'); return; }
    setLoading(true);
    try {
      apply(await fetchJson(`${API_URL}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: runId, edited_soap: soapDraft, approve, reviewer_name: reviewerName, review_notes: reviewNotes }),
      }));
      setNotice(approve ? 'Clinical note signed and finalized.' : 'Edits saved — note is pending physician sign-off.');
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const onNewCase = () => {
    setStatus('idle'); setRunId(''); setText(''); setFiles([]);
    setSoapDraft(''); setServerSoap(''); setFinalNote('');
    setConditions([]); setMedications([]); setConditionCodes([]);
    setMedicationCodes([]); setCodedEntities([]); setAuditLog([]);
    setSourceFiles([]); setReviewerName('Clinician'); setReviewNotes('');
    setError(''); setNotice('');
  };

  const diff          = diffSoap(serverSoap, soapDraft);
  const completedNodes = new Set(auditLog.map(e => e.node));

  /* ── STEP RENDERERS ── */

  const renderIntake = () => (
    <div>
      {status === 'stored' && (
        <div className="stored-notice">
          ⚠️ <span>Documents saved — Session <code>{runId.slice(0,8)}…</code>. Add any additional notes below, then start the AI analysis.</span>
        </div>
      )}
      <div className="card">
        <div className="card-title">📋 Patient Clinical Documents</div>
        <div className="card-sub">Upload patient records, discharge summaries, or lab reports (PDF, TXT, CSV). Add any additional clinical notes in the text field below.</div>

        <label className="field-label">Upload patient records</label>
        <div className={`upload-zone ${files.length ? 'has-files' : ''}`}>
          <input type="file" multiple accept=".txt,.csv,.pdf"
            onChange={e => setFiles(Array.from(e.target.files || []))} />
          <div className="upload-icon">📂</div>
          {files.length
            ? <><p><strong>{files.length} file{files.length > 1 ? 's' : ''} ready</strong></p>
                <div className="file-chips">
                  {files.map(f => <span key={f.name} className="file-tag">📄 {f.name}</span>)}
                </div></>
            : <p><strong>Click to browse</strong> or drag & drop — PDF, TXT, CSV supported</p>
          }
        </div>

        <label className="field-label" style={{ marginTop: 16 }}>Clinical notes & additional context</label>
        <textarea rows={6} value={text} onChange={e => setText(e.target.value)}
          placeholder="Enter patient symptoms, handoff notes, medication history, or any additional clinical details…" />

        <div className="btn-row">
          <button className="btn-primary" onClick={onProcess}
            disabled={loading || (!text.trim() && !files.length)}>
            🔍 Start AI Analysis
          </button>
          <button className="btn-secondary" onClick={onUpload} disabled={loading || !files.length}>
            💾 Save Documents
          </button>
          {status === 'stored' && (
            <button className="btn-ghost" onClick={onProcessStorage} disabled={loading}>
              ⚡ Analyse Saved Documents
            </button>
          )}
        </div>
      </div>
    </div>
  );

  const renderPipeline = () => (
    <div className="card orch-card">
      {/* Header */}
      <div className="orch-header-row">
        <div className="spinner-sm" />
        <div className="orch-titles">
          <div className="orch-main-title">⚡ AI Orchestration Engine</div>
          <div className="orch-main-sub">5 agents executing in parallel · LangGraph pipeline</div>
        </div>
        <div className="orch-live-badge"><span className="live-dot" />LIVE</div>
      </div>

      {/* Parallel agent lanes */}
      <div className="agent-grid">
        {PARALLEL_AGENTS.map((agent) => {
          const done = completedNodes.has(agent.key);
          return (
            <div key={agent.key}
              className={`agent-row agent-${agent.color} ${done ? 'agent-done' : 'agent-active'}`}
              style={{ animationDelay: `${agent.delay}s` }}>
              <div className="agent-row-icon">{done ? '✅' : agent.icon}</div>
              <div className="agent-row-body">
                <div className="agent-row-name">{agent.label}</div>
                <div className="agent-bar-wrap">
                  <div className={`agent-bar ${done ? 'bar-done' : ''}`}>
                    {!done && <div className="bar-scan" style={{ animationDelay:`${agent.delay}s` }} />}
                    {!done && <div className="bar-packet" style={{ animationDelay:`${agent.delay + 0.3}s` }} />}
                    {!done && <div className="bar-packet" style={{ animationDelay:`${agent.delay + 0.9}s` }} />}
                  </div>
                </div>
              </div>
              <div className={`agent-tag ${done ? 'tag-done' : 'tag-run'}`}>
                {done ? '✓ Done' : '⟳ Running'}
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer data flow */}
      <div className="orch-footer-row">
        <span className="orch-pulse-dot" />
        <span className="orch-footer-text">Data flowing through pipeline · Awaiting physician checkpoint</span>
        <div className="orch-beam-track"><div className="orch-beam" /></div>
      </div>
    </div>
  );

  const renderReview = () => (
    <>
      <div className="metrics-row">
        <div className="metric-card"><span>Session ID</span> <strong style={{fontSize:11,wordBreak:'break-all'}}>{runId.slice(0,8)}…</strong></div>
        <div className="metric-card"><span>Documents</span>   <strong>{sourceFiles.length}</strong></div>
        <div className="metric-card"><span>Diagnoses</span>   <strong>{conditions.length}</strong></div>
        <div className="metric-card"><span>Medications</span> <strong>{medications.length}</strong></div>
        <div className="metric-card"><span>Coded Terms</span> <strong>{conditionCodes.length + medicationCodes.length}</strong></div>
      </div>

      <div className="review-grid">
        {/* LEFT — Extracted results */}
        <div className="card">
          <div className="card-title">🔬 Clinical Findings</div>
          <div className="tabs-row">
            {RESULT_TABS.map(t => (
              <button key={t.key} className={`tab-btn ${activeTab === t.key ? 'active' : ''}`}
                onClick={() => setActiveTab(t.key)}>{t.label}</button>
            ))}
          </div>

          {activeTab === 'structured' && (
            <>
              <label className="field-label">Identified Diagnoses</label>
              <div className="pill-wrap" style={{ marginBottom: 16 }}>
                {conditions.length
                  ? conditions.map(c => <span key={c} className="pill">{c}</span>)
                  : <span className="empty">None extracted.</span>}
              </div>
              <label className="field-label">Current Medications</label>
              {medications.length
                ? <table className="data-table">
                    <thead><tr><th>Drug</th><th>Dosage</th><th>Route</th></tr></thead>
                    <tbody>{medications.map((m,i) => (
                      <tr key={i}>
                        <td>{m.drug}</td>
                        <td>{m.dosage}</td>
                        <td><span className="route-badge">{m.route}</span></td>
                      </tr>
                    ))}</tbody>
                  </table>
                : <span className="empty">None extracted.</span>}
            </>
          )}

          {activeTab === 'coding' && (
            <>
              <label className="field-label">ICD-10-CM — Diagnosis Codes</label>
              {conditionCodes.length
                ? <table className="data-table" style={{ marginBottom: 14 }}>
                    <thead><tr><th>Condition</th><th>Code</th></tr></thead>
                    <tbody>{conditionCodes.map((c,i) => (
                      <tr key={i}><td>{c.condition}</td><td><span className="code-badge">{c.icd10}</span></td></tr>
                    ))}</tbody>
                  </table>
                : <span className="empty">No codes assigned.</span>}
              <label className="field-label">RxNorm — Medication Codes</label>
              {medicationCodes.length
                ? <table className="data-table">
                    <thead><tr><th>Drug</th><th>Dosage</th><th>Route</th><th>Code</th></tr></thead>
                    <tbody>{medicationCodes.map((m,i) => (
                      <tr key={i}>
                        <td>{m.drug}</td><td>{m.dosage}</td>
                        <td><span className="route-badge">{m.route}</span></td>
                        <td><span className="code-badge">{m.rxnorm}</span></td>
                      </tr>
                    ))}</tbody>
                  </table>
                : <span className="empty">No codes assigned.</span>}
            </>
          )}

          {activeTab === 'audit' && (
            <div className="timeline">
              {auditLog.length
                ? auditLog.map((e,i) => (
                    <div key={i} className="timeline-item">
                      <div className={`tl-dot ${e.status === 'paused' ? 'paused' : ''}`} />
                      <div className="tl-content">
                        <strong>{e.node}</strong>
                        <p>{e.summary}</p>
                        <span>{e.timestamp}</span>
                      </div>
                    </div>
                  ))
                : <span className="empty">No audit events yet.</span>}
            </div>
          )}
        </div>

        {/* RIGHT — Clinician approval */}
        <div className="card">
          <div className="panel-top">
            <div className="card-title">✍️ Physician Review & Sign-off</div>
            <span className={`checkpoint-tag ${status === 'completed' ? 'locked' : ''}`}>
              {status === 'completed' ? '🔒 Signed' : '✏️ Open for Review'}
            </span>
          </div>
          <div className="card-sub">Review and edit the AI-generated clinical note. Once satisfied, sign off to produce the final, locked patient record.</div>

          <label className="field-label">Attending physician</label>
          <input value={reviewerName} onChange={e => setReviewerName(e.target.value)} style={{ marginBottom: 10 }} />

          <label className="field-label">Clinical remarks</label>
          <input value={reviewNotes} onChange={e => setReviewNotes(e.target.value)}
            style={{ marginBottom: 12 }} placeholder="Optional notes…" />

          <div className="diff-bar">
            <span className="diff-chip changed">Δ {diff.changed}</span>
            <span className="diff-chip added">+{diff.added}</span>
            <span className="diff-chip removed">−{diff.removed}</span>
          </div>

          <label className="field-label">AI-generated draft</label>
          <textarea value={serverSoap} rows={6} disabled style={{ marginBottom: 10, opacity: 0.55 }} />

          <label className="field-label">Your edited version</label>
          <textarea value={soapDraft} rows={6}
            onChange={e => setSoapDraft(e.target.value)}
            disabled={status === 'completed'}
            placeholder="Review and amend the clinical note as needed before signing off." />

          <div className="btn-row">
            <button className="btn-success" onClick={() => submitReview(true)}
              disabled={loading || status !== 'awaiting_approval' || !soapDraft.trim()}>
              ✓ Sign &amp; Approve Note
            </button>
            <button className="btn-secondary" onClick={() => submitReview(false)}
              disabled={loading || status !== 'awaiting_approval' || !soapDraft.trim()}>
              💾 Save Changes
            </button>
          </div>
        </div>
      </div>
    </>
  );

  const renderReport = () => (
    <div>
      <div className="report-card">
        <div className="report-header">
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: '#fff', marginBottom: 2 }}>
              📄 Signed Clinical Note
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-2)' }}>Physician-approved and permanently signed</div>
          </div>
          <span className="checkpoint-tag locked">🔒 Signed</span>
        </div>
        <div className="report-body">
          <div className="soap-display">{finalNote || '—'}</div>
          <div className="signoff-row">
            <div className="signoff-item">
              <span>Reviewer</span>
              <strong>{reviewerName || '—'}</strong>
            </div>
            {reviewNotes && (
              <div className="signoff-item">
                <span>Review Notes</span>
                <strong>{reviewNotes}</strong>
              </div>
            )}
            <div className="signoff-item">
              <span>Source Files</span>
              <strong>{sourceFiles.length > 0 ? sourceFiles.join(', ') : 'Text input'}</strong>
            </div>
          </div>
        </div>
      </div>
      <div className="btn-row" style={{ marginTop: 18, justifyContent: 'center' }}>
        <button className="btn-primary" onClick={onNewCase}>＋ New Patient Case</button>
      </div>
    </div>
  );

  return (
    <div className="app-shell">
      {/* Header */}
      <header className="app-header">
        <div className="brand">
          <div className="brand-icon">🏥</div>
          <div>
            <h1>ClinicalAI</h1>
            <p>AI-Powered Clinical Documentation Assistant</p>
          </div>
        </div>
        <div className="header-chips">
          <span className="chip">{health.model_name}</span>
          <span className={`chip dot ${health.llm_enabled ? 'live' : 'fallback'}`}>
            {health.llm_enabled ? 'Groq Live' : 'Fallback Mode'}
          </span>
        </div>
      </header>

      {/* Wizard bar */}
      <div className="wizard-bar">
        <div className="wizard-track">
          {WIZARD.map((step, i) => {
            const done   = i < wizardIdx;
            const active = i === wizardIdx;
            return (
              <div key={step.key} style={{ display: 'flex', flex: 1, alignItems: 'center' }}>
                <div className={`wizard-step ${done ? 'is-done' : ''} ${active ? 'is-active' : ''}`}>
                  <div className="step-circle">{done ? '✓' : i + 1}</div>
                  <div className="step-label">{step.label}</div>
                </div>
                {i < WIZARD.length - 1 && (
                  <div className={`step-line ${done ? 'is-done' : ''}`} />
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Page body */}
      <main className="page-body">
        {(notice || error) && (
          <div className="banners">
            {notice && <div className="banner success">✓ {notice}</div>}
            {error  && <div className="banner error">✕ {error}</div>}
          </div>
        )}

        {wizardStep === 'intake'   && renderIntake()}
        {wizardStep === 'pipeline' && renderPipeline()}
        {wizardStep === 'review'   && renderReview()}
        {wizardStep === 'report'   && renderReport()}
      </main>
    </div>
  );
}
