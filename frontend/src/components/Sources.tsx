import { useState } from "react";
import type { Citation } from "../types";
import { docPath, lastHeading } from "../lib/format";

const COLLAPSED = 4;

export function Sources({ citations }: { citations: Citation[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!citations.length) return null;
  const visible = expanded ? citations : citations.slice(0, COLLAPSED);
  return (
    <section className="sources" aria-label="Sources">
      <div className="sources-head">
        <span>Sources</span>
        <span className="count">{citations.length}</span>
      </div>
      <ol className="source-grid">
        {visible.map((source) => (
          <li key={source.index}>
            <a href={source.url} target="_blank" rel="noreferrer" title={source.excerpt}>
              <span className="source-index">{source.index}</span>
              <span className="source-body">
                <span className="source-title">{source.title}</span>
                <span className="source-section">{lastHeading(source.heading)}</span>
                <span className="source-url">{docPath(source.url)}</span>
              </span>
            </a>
          </li>
        ))}
      </ol>
      {citations.length > COLLAPSED && (
        <button className="link-button" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Show fewer" : `Show all ${citations.length} sources`}
        </button>
      )}
    </section>
  );
}
