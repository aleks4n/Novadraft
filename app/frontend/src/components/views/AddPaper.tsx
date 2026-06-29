import { useState } from "react";
import { Icon } from "../Icon";
import { ViewHeader } from "../chrome";
import { RunTranscript } from "../RunTranscript";
import { useWorkspace } from "../../lib/store";

export const SUGGESTIONS = [
  // --- One example per input query type ---
  { q: "1706.03762", label: "Attention Is All You Need", from: "arXiv ID", note: "Query type: arXiv ID" },
  { q: "https://arxiv.org/abs/2010.11929", label: "An Image is Worth 16x16 Words", from: "arXiv URL", note: "Query type: arXiv URL" },
  { q: "10.48550/arXiv.1810.04805", label: "BERT", from: "DOI", note: "Query type: DOI" },
  { q: "Deep Residual Learning for Image Recognition", label: "ResNet", from: "Paper title", note: "Query type: free-text title" },
  { q: "https://proceedings.mlr.press/v37/ioffe15.pdf", label: "Batch Normalization", from: "PDF URL", note: "Query type: direct PDF URL" },

  // --- Different paper types ---
  { q: "2203.02155", label: "InstructGPT (conference paper)", from: "arXiv ID", note: "Paper type: conference paper" },
  { q: "10.1038/nature14539", label: "Deep Learning (Nature review)", from: "DOI", note: "Paper type: journal review article" },
  { q: "10.6028/NIST.SP.800-207", label: "Zero Trust Architecture", from: "DOI", note: "Paper type: technical report / standard" },
  { q: "Distilling the Knowledge in a Neural Network", label: "Knowledge Distillation (workshop)", from: "Paper title", note: "Paper type: workshop paper" },

  // --- PDF-only: no arXiv record, parsed straight from the PDF ---
  { q: "https://www.cs.toronto.edu/~hinton/absps/JMLRdropout.pdf", label: "Dropout (PDF only, no arXiv)", from: "PDF URL", note: "Edge case: PDF only — no arXiv ID" },
  { q: "10.1145/3292500.3330701", label: "ACM paper (PDF behind publisher)", from: "DOI", note: "Edge case: PDF only — resolve via DOI" },

  // --- No download access: metadata resolves but full text is paywalled / gated ---
  { q: "10.1126/science.aaa8415", label: "Machine learning (Science, paywalled)", from: "DOI", note: "Edge case: no download access — paywalled" },
  { q: "10.1109/5.726791", label: "LeNet (IEEE, gated)", from: "DOI", note: "Edge case: no download access — gated full text" },
];

export function AddPaper() {
  const { active, run, startPaperRun, setNav } = useWorkspace();
  const [query, setQuery] = useState("");
  const running = run.status === "running";

  const submit = () => {
    if (!query.trim() || running) return;
    startPaperRun(query);
  };

  return (
    <div className="view view--newidea">
      <ViewHeader
        eyebrow="Add paper"
        title="Ingest a reference paper"
        subtitle="Enter an arXiv ID, URL, DOI, or title. The agent fetches and parses it, writes a structured summary, and extracts the research gaps the paper states — streaming each step below."
        breadcrumb={[
          { label: "Research" },
          { label: active?.shortTitle || "Topic", onClick: () => setNav({ view: "topic", id: null }) },
          { label: "Add paper", current: true },
        ]}
      />

      <div className="newidea-shell">
        <textarea
          className="newidea-text"
          placeholder="arXiv ID (1706.03762), URL, DOI, or paper title…"
          rows={4}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }}
          disabled={running}
        />

        <div className="newidea-foot">
          <span className="newidea-pill">
            <Icon name="target" /><span>Workspace: <b>{active?.shortTitle || "—"}</b></span>
          </span>
          <div className="newidea-foot-right">
            <button className="btn btn--primary btn--circle" onClick={submit} disabled={running || !query.trim()} title="Run paper agent (⌘↵)">
              <Icon name={running ? "clock" : "arrow-right"} />
            </button>
          </div>
        </div>

        {run.status !== "idle" && <RunTranscript run={run} />}
      </div>

      <div className="newidea-suggest">
        <div className="newidea-suggest-label">Or try one of these</div>
        <div className="newidea-suggest-grid">
          {SUGGESTIONS.map((s, i) => (
            <button key={i} className="suggest-card" onClick={() => setQuery(s.q)} disabled={running}>
              <div className="suggest-title">{s.label}</div>
              <div className="suggest-from"><Icon name="compass" /> {s.from}</div>
              <div className="suggest-note">{s.note}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
