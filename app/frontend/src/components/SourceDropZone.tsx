// Drag-and-drop (or click-to-pick) target for supplying a paper's full-text
// source when the fetch couldn't download one (restricted / paywalled). Accepts
// a PDF or a LaTeX source archive (.zip / .tar.gz); hands the file to `onFile`.
import { useRef, useState } from "react";
import { Icon } from "./Icon";

const ACCEPT = ".pdf,.zip,.tar.gz,.tgz,.tar";

export function SourceDropZone({
  onFile,
  busy = false,
}: {
  onFile: (file: File) => void;
  busy?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const take = (files: FileList | null) => {
    if (busy || !files || files.length === 0) return;
    onFile(files[0]);
  };

  return (
    <div
      className={"source-drop" + (over ? " source-drop--over" : "") + (busy ? " source-drop--busy" : "")}
      onDragOver={(e) => { e.preventDefault(); if (!busy) setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files); }}
      onClick={() => !busy && inputRef.current?.click()}
      role="button"
      tabIndex={0}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(e) => { take(e.target.files); e.target.value = ""; }}
      />
      <Icon name={busy ? "clock" : "attach"} />
      <div className="source-drop-body">
        <b>{busy ? "Uploading & resuming…" : "Drop a PDF or LaTeX source here"}</b>
        <span>{busy ? "Attaching the file and continuing the pipeline." : "or click to choose a file · .pdf, .zip, .tar.gz"}</span>
      </div>
    </div>
  );
}
