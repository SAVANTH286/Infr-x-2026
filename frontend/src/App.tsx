import React, { useState, useEffect, useRef } from 'react';

interface DocInstance {
  id: string;
  type: string;
  label: string;
  start_page: number;
  end_page: number;
  page_count: number;
  metadata: Record<string, any>;
}

interface Summary {
  package_id: string;
  total_pages: number;
  health_score: number;
  health_breakdown: Record<string, number>;
  doc_instances: DocInstance[];
  truth_matrix: Record<string, Record<string, any>>;
}

interface Evidence {
  page_index: number;
  bbox: [number, number, number, number];
  doc_type: string;
}

interface ChatMessage {
  sender: 'user' | 'assistant';
  text: string;
  trace?: Array<{ agent: string; action: string }>;
  evidence?: Evidence[];
}

interface LazyPdfPageProps {
  pageIndex: number;
  activeHighlight: Evidence[] | null;
  onRef: (el: HTMLDivElement | null) => void;
}

function LazyPdfPage({ pageIndex, activeHighlight, onRef }: LazyPdfPageProps) {
  const [isVisible, setIsVisible] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: '600px' } // Load page image when it's within 600px of viewport
    );
    if (containerRef.current) {
      observer.observe(containerRef.current);
    }
    return () => observer.disconnect();
  }, []);

  // Sync ref to parent scroll-to tracker
  const setRefs = (el: HTMLDivElement | null) => {
    containerRef.current = el;
    onRef(el);
  };

  return (
    <div 
      ref={setRefs}
      className="pdf-render-wrapper"
      style={{ 
        position: 'relative', 
        minHeight: '792px', 
        width: '100%', 
        maxWidth: '612px', 
        background: '#1e293b', 
        border: '1px solid var(--border-color)', 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'center',
        margin: '0 auto'
      }}
    >
      <div style={{ position: 'absolute', top: '-24px', left: '0', fontSize: '11px', color: 'var(--text-secondary)' }}>
        Page {pageIndex + 1}
      </div>
      
      {isVisible ? (
        <>
          <img 
            src={`http://localhost:8000/api/page/${pageIndex}/render`} 
            alt={`Page ${pageIndex + 1}`}
            className="pdf-image"
            style={{ width: '100%', height: 'auto', display: 'block', background: 'white' }}
          />
          {/* Highlight coordinates overlay */}
          {activeHighlight && activeHighlight.map((hl, hIdx) => {
            if (hl.page_index !== pageIndex) return null;
            const [x0, y0, x1, y1] = hl.bbox;
            const left = `${(x0 / 612) * 100}%`;
            const top = `${(y0 / 792) * 100}%`;
            const width = `${((x1 - x0) / 612) * 100}%`;
            const height = `${((y1 - y0) / 792) * 100}%`;
            
            return (
              <div 
                key={hIdx}
                className="pdf-highlight source"
                style={{ left, top, width, height }}
              />
            );
          })}
        </>
      ) : (
        <div style={{ color: 'var(--text-secondary)', fontSize: '14px', fontStyle: 'italic' }}>
          Loading Page {pageIndex + 1}...
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [apiKey, setApiKey] = useState<string>(() => localStorage.getItem('grok_api_key') || '');
  const [file, setFile] = useState<File | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [activeHighlight, setActiveHighlight] = useState<Evidence[] | null>(null);
  const [activeDocInstance, setActiveDocInstance] = useState<string | null>(null);
  
  // QA Chat state
  const [question, setQuestion] = useState('');
  const [chatLog, setChatLog] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  
  // References for page elements in scrollable container
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const viewerContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    localStorage.setItem('grok_api_key', apiKey);
  }, [apiKey]);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const uploadedFile = e.target.files?.[0];
    if (!uploadedFile) return;
    setFile(uploadedFile);
    setIsUploading(true);
    setSummary(null);
    setChatLog([]);
    setActiveHighlight(null);

    const formData = new FormData();
    formData.append('file', uploadedFile);

    try {
      const res = await fetch('http://localhost:8000/api/upload', {
        method: 'POST',
        headers: {
          'x-api-key': apiKey
        },
        body: formData
      });
      if (!res.ok) throw new Error('Upload failed');
      const data = await res.json();
      setSummary(data);
      if (data.doc_instances.length > 0) {
        setActiveDocInstance(data.doc_instances[0].id);
      }
    } catch (err) {
      console.error(err);
      alert('Failed to upload/analyze the PDF. Make sure backend is running.');
    } finally {
      setIsUploading(false);
    }
  };

  const handleQuerySubmit = async (e?: React.FormEvent, customQ?: string) => {
    if (e) e.preventDefault();
    const queryText = customQ || question;
    if (!queryText.trim() || !file) return;

    const userMsg: ChatMessage = { sender: 'user', text: queryText };
    setChatLog(prev => [...prev, userMsg]);
    setIsLoading(true);
    if (!customQ) setQuestion('');

    try {
      const res = await fetch('http://localhost:8000/api/query', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': apiKey
        },
        body: JSON.stringify({ question: queryText })
      });
      const data = await res.json();
      
      const assistantMsg: ChatMessage = {
        sender: 'assistant',
        text: data.answer,
        trace: data.trace,
        evidence: data.evidence
      };
      
      setChatLog(prev => [...prev, assistantMsg]);
      
      // Auto-scroll to first evidence source
      if (data.evidence && data.evidence.length > 0) {
        scrollToPage(data.evidence[0].page_index);
        setActiveHighlight(data.evidence);
      }
    } catch (err) {
      console.error('Query error:', err);
    } finally {
      setIsLoading(false);
    }
  };

  const scrollToPage = (pageIdx: number) => {
    const pageEl = pageRefs.current[pageIdx];
    if (pageEl && viewerContainerRef.current) {
      pageEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  const handleDocInstanceClick = (doc: DocInstance) => {
    setActiveDocInstance(doc.id);
    scrollToPage(doc.start_page);
    setActiveHighlight(null);
  };

  const handleEvidenceClick = (ev: Evidence) => {
    scrollToPage(ev.page_index);
    setActiveHighlight([ev]);
  };

  return (
    <div className="app-container">
      {/* Header bar */}
      <header className="app-header glass-panel">
        <div className="brand">
          <div>
            <h1>PageVerdict</h1>
            <div className="brand-subtitle">Generic PDF Analyzer & Evidence Locator</div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
          <input 
            type="password" 
            placeholder="Grok API Key (Optional / Free agent completions)" 
            className="qa-input"
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            style={{ width: '320px', fontSize: '11px', height: '34px' }}
          />
          {file && (
            <button className="btn-back" style={{ height: '34px' }} onClick={() => { setFile(null); setSummary(null); }}>
              Upload New PDF
            </button>
          )}
        </div>
      </header>

      {/* Dynamic workspace area */}
      {!file ? (
        // FILE UPLOADER VIEW
        <div style={{ display: 'flex', flexGrow: 1, alignItems: 'center', justifyContent: 'center' }}>
          <div className="glass-panel" style={{ padding: '60px', width: '500px', textAlign: 'center', display: 'flex', flexDirection: 'column', gap: '20px' }}>
            <div style={{ fontSize: '64px' }}>📄</div>
            <h2>Upload a PDF Document</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
              Drag and drop any PDF file. The Generic Analyzer will classify logical spans, extract metadata properties, and reconcile variables dynamically using Grok.
            </p>
            <input 
              type="file" 
              accept=".pdf" 
              onChange={handleFileUpload} 
              id="pdf-upload-input" 
              style={{ display: 'none' }}
            />
            <label 
              htmlFor="pdf-upload-input" 
              className="qa-send-btn" 
              style={{ padding: '12px 24px', cursor: 'pointer', display: 'inline-block' }}
            >
              {isUploading ? 'Analyzing Document structure...' : 'Select PDF File'}
            </label>
          </div>
        </div>
      ) : (
        // DUAL PANEL CORE INTERFACE
        <div className="workspace-layout">
          {/* LEFT PANEL: Dynamic Classification & Metadata Tree */}
          <div className="sidebar-panel">
            {summary && (
              <div className="document-tree glass-panel">
                <div className="tree-header">Logical Pagination Spans</div>
                <ul className="tree-list">
                  {summary.doc_instances.map(doc => (
                    <li 
                      key={doc.id}
                      className={`tree-item ${activeDocInstance === doc.id ? 'active' : ''}`}
                      onClick={() => handleDocInstanceClick(doc)}
                    >
                      <span>{doc.label}</span>
                      <span className="tree-badge">p.{doc.start_page + 1}-{doc.end_page + 1}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            
            {summary && (
              <div className="glass-panel" style={{ padding: '16px', overflowY: 'auto', flexGrow: 1 }}>
                <div className="tree-header" style={{ marginBottom: '10px' }}>Extracted File Metadata</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  {summary.doc_instances.map(doc => (
                    Object.keys(doc.metadata).length > 0 && (
                      <div key={doc.id} style={{ borderBottom: '1px solid var(--border-color)', paddingBottom: '8px' }}>
                        <div style={{ fontSize: '11px', color: 'var(--accent-cyan)', fontWeight: 'bold' }}>{doc.label}</div>
                        {Object.entries(doc.metadata).map(([k, v]) => (
                          <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', marginTop: '4px' }}>
                            <span style={{ color: 'var(--text-secondary)' }}>{k.replace('_', ' ')}:</span>
                            <span style={{ fontWeight: '500' }}>{String(v)}</span>
                          </div>
                        ))}
                      </div>
                    )
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* CENTER PANEL: Dynamic Truth Matrix & Scrollable PDF Viewer */}
          <div className="center-panel">
            {/* Dynamic Truth Matrix table if overlapping keys exist */}
            {summary && Object.keys(summary.truth_matrix).length > 0 && (
              <div className="matrix-container glass-panel">
                <div style={{ fontSize: '11px', textTransform: 'uppercase', color: 'var(--text-secondary)', marginBottom: '8px', fontWeight: 'bold' }}>
                  Dynamic Truth Matrix (Overlapping Keys)
                </div>
                <table className="matrix-table">
                  <thead>
                    <tr>
                      <th>Parameters</th>
                      {Object.keys(summary.truth_matrix[Object.keys(summary.truth_matrix)[0]] || {}).map(instId => (
                        <th key={instId}>{instId.replace(/#\d+/, '').replace('_', ' ').toUpperCase()}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(summary.truth_matrix).map(([field, instances]) => {
                      const values = Object.values(instances);
                      const hasConflict = new Set(values.map(String)).size > 1;
                      return (
                        <tr key={field} className="matrix-row">
                          <td style={{ fontWeight: '500' }}>{field.replace('_', ' ').toUpperCase()}</td>
                          {Object.entries(instances).map(([instId, val]) => (
                            <td 
                              key={instId} 
                              className={hasConflict ? 'conflict-red' : 'match-green'}
                            >
                              {typeof val === 'number' ? `$${val.toLocaleString()}` : String(val)}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* Scrollable PDF Viewer with Lazy Loading Page Components */}
            <div className="pdf-container glass-panel">
              <div className="pdf-toolbar">
                <span>PDF Package Document Viewer</span>
                <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{summary?.total_pages || 1} pages</span>
              </div>
              <div className="pdf-viewport" ref={viewerContainerRef} style={{ display: 'flex', flexDirection: 'column', gap: '40px', overflowY: 'auto', alignItems: 'center', padding: '30px 20px' }}>
                {summary && Array.from({ length: summary.total_pages }).map((_, idx) => (
                  <LazyPdfPage 
                    key={idx}
                    pageIndex={idx}
                    activeHighlight={activeHighlight}
                    onRef={el => pageRefs.current[idx] = el}
                  />
                ))}
              </div>
            </div>
          </div>

          {/* RIGHT PANEL: Agent Chat Console */}
          <div className="qa-panel glass-panel">
            <div className="qa-header">
              <h3 style={{ fontSize: '15px', fontWeight: 'bold' }}>Agent Truth Engine</h3>
              <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                Grok agent analyzes page texts for direct answers.
              </div>
            </div>

            <div className="qa-conversation">
              {chatLog.length === 0 && (
                <div style={{ color: 'var(--text-secondary)', textAlign: 'center', marginTop: '40px', fontSize: '13px' }}>
                  <p>Ask any question about the uploaded document package. If evidence does not exist, the Answerability Agent will refuse to answer.</p>
                </div>
              )}
              {chatLog.map((msg, i) => (
                <div key={i} className={`chat-bubble ${msg.sender}`}>
                  <div>{msg.text}</div>
                  
                  {/* Real-time Agent Trace */}
                  {msg.trace && (
                    <div className="agent-activity-box">
                      <div className="activity-header">⚡ Reasoning Trace</div>
                      {msg.trace.map((step, idx) => (
                        <div key={idx} className="activity-step">
                          <span className="activity-name">[{step.agent}]</span>
                          <span className="activity-detail">{step.action}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Supporting Evidence Clickable Badges */}
                  {msg.evidence && msg.evidence.length > 0 && (
                    <div className="evidence-trail">
                      <strong>Supporting Evidence Citations:</strong>
                      {msg.evidence.map((ev, idx) => (
                        <div 
                          key={idx} 
                          className="evidence-item"
                          onClick={() => handleEvidenceClick(ev)}
                        >
                          Page {ev.page_index + 1} (Click to scroll)
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {isLoading && (
                <div className="chat-bubble assistant" style={{ fontStyle: 'italic' }}>
                  Grok Agent reasoning...
                </div>
              )}
            </div>

            <form className="qa-input-bar" onSubmit={handleQuerySubmit}>
              <input 
                type="text" 
                placeholder="Query file parameters..." 
                className="qa-input"
                value={question}
                onChange={e => setQuestion(e.target.value)}
              />
              <button type="submit" className="qa-send-btn">Send</button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
