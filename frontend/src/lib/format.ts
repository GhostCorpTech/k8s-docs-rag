export const fmt = (value: number | null | undefined, digits = 3) =>
  value == null || Number.isNaN(value) ? "—" : value.toFixed(digits);

export const ms = (value: number | undefined) =>
  value == null ? "—" : value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;

export function docPath(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.hostname}${parsed.pathname}`.replace(/\/$/, "");
  } catch {
    return url;
  }
}

export const lastHeading = (heading: string) => heading.split(" > ").at(-1) ?? heading;

export const configLabel = (strategy: string, reranker: boolean) =>
  `${strategy === "window" ? "Window" : "Heading"} · ${reranker ? "LLM rerank" : "no rerank"}`;
