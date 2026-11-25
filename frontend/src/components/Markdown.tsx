import { memo } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "../types";
import { docPath, lastHeading } from "../lib/format";

const CODE = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g;
const MARKER = /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\](?!\()/g;

/** Turn "[1]" / "[1, 3]" into links with a private scheme, never inside code. */
export function linkCitations(text: string): string {
  return text
    .split(CODE)
    .map((part, index) =>
      index % 2 === 1
        ? part
        : part.replace(MARKER, (_, ids: string) =>
            ids
              .split(",")
              .map((id) => `[${id.trim()}](cite:${id.trim()})`)
              .join(""),
          ),
    )
    .join("");
}

function CitationChip({ id, citation }: { id: string; citation?: Citation }) {
  if (!citation) return <span className="cite cite-missing">{id}</span>;
  return (
    <span className="cite-wrap">
      <a
        className="cite"
        href={citation.url}
        target="_blank"
        rel="noreferrer"
        aria-label={`Source ${id}: ${citation.title}`}
      >
        {id}
      </a>
      <span className="cite-card" role="tooltip">
        <strong>{citation.title}</strong>
        <span>{lastHeading(citation.heading)}</span>
        <small>{docPath(citation.url)}</small>
      </span>
    </span>
  );
}

export const Markdown = memo(function Markdown({ text, citations }: { text: string; citations: Citation[] }) {
  const byIndex = new Map(citations.map((item) => [String(item.index), item]));
  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => (url.startsWith("cite:") ? url : defaultUrlTransform(url))}
        components={{
          a({ href, children }) {
            if (href?.startsWith("cite:")) {
              const id = href.slice(5);
              return <CitationChip id={id} citation={byIndex.get(id)} />;
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
          table({ children }) {
            return (
              <div className="table-scroll">
                <table>{children}</table>
              </div>
            );
          },
        }}
      >
        {linkCitations(text)}
      </ReactMarkdown>
    </div>
  );
});
