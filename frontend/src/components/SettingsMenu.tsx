import { useEffect, useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import type { RetrievalSettings, Strategy } from "../types";
import { DEFAULT_SETTINGS } from "../lib/store";

export function SettingsMenu({
  settings,
  onChange,
}: {
  settings: RetrievalSettings;
  onChange: (settings: RetrievalSettings) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !ref.current?.contains(event.target as Node))
        setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  const set = (patch: Partial<RetrievalSettings>) => onChange({ ...settings, ...patch });
  const isDefault =
    settings.strategy === DEFAULT_SETTINGS.strategy &&
    settings.rerank === DEFAULT_SETTINGS.rerank &&
    settings.limit === DEFAULT_SETTINGS.limit;

  return (
    <div className="settings" ref={ref}>
      <button
        className={`chip-button ${open ? "on" : ""}`}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <SlidersHorizontal size={14} />
        <span>
          {settings.strategy === "window" ? "Window" : "Heading"} · {settings.rerank ? "Rerank" : "Hybrid"} · top-
          {settings.limit}
        </span>
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Retrieval settings">
          <div className="field">
            <label>Chunking strategy</label>
            <div className="segmented" role="radiogroup">
              {(["window", "heading"] as Strategy[]).map((strategy) => (
                <button
                  key={strategy}
                  role="radio"
                  aria-checked={settings.strategy === strategy}
                  className={settings.strategy === strategy ? "on" : ""}
                  onClick={() => set({ strategy })}
                >
                  {strategy === "window" ? "Sliding window" : "By heading"}
                </button>
              ))}
            </div>
            <p className="help">
              Window chunks: MRR@10 0.793 vs 0.660 for heading chunks on the benchmark (p = 0.003).
            </p>
          </div>
          <div className="field row">
            <div>
              <label htmlFor="rerank">LLM reranker</label>
              <p className="help">
                Reorders the top-20 hybrid hits. Adds ~17 s median latency; no significant gain on the benchmark.
              </p>
            </div>
            <button
              id="rerank"
              role="switch"
              aria-checked={settings.rerank}
              className={`switch ${settings.rerank ? "on" : ""}`}
              onClick={() => set({ rerank: !settings.rerank })}
            >
              <i />
            </button>
          </div>
          <div className="field">
            <label>Sources in context</label>
            <div className="segmented" role="radiogroup">
              {[4, 6, 8, 10].map((limit) => (
                <button
                  key={limit}
                  role="radio"
                  aria-checked={settings.limit === limit}
                  className={settings.limit === limit ? "on" : ""}
                  onClick={() => set({ limit })}
                >
                  {limit}
                </button>
              ))}
            </div>
          </div>
          <button className="link-button" disabled={isDefault} onClick={() => onChange(DEFAULT_SETTINGS)}>
            Reset to benchmark-best defaults
          </button>
        </div>
      )}
    </div>
  );
}
