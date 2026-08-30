import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";
import remarkBreaks from "remark-breaks";
import "katex/dist/katex.min.css";

// remark-math uses dollar delimiters. Models also commonly return TeX's
// \(...\) and \[...\] forms, so normalize those before parsing. Keeping this
// conversion at the rendering boundary also fixes existing stored messages.
function normalizeMathDelimiters(text: string): string {
  return text
    .replace(/\\\[([\s\S]*?)\\\]/g, (_match, math: string) => `\n$$\n${math.trim()}\n$$\n`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, math: string) => `$${math.trim()}$`);
}

export function RichText({ children }: { children: string }) {
  return (
    <div className="rich-text">
      <ReactMarkdown remarkPlugins={[remarkMath, remarkBreaks]} rehypePlugins={[rehypeKatex]}>
        {normalizeMathDelimiters(children)}
      </ReactMarkdown>
    </div>
  );
}
