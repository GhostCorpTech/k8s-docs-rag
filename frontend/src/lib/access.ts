// Personal demo key: kept in this browser only and sent as a Bearer token.
const KEY = "k8s-rag.access-key.v1";

export function loadKey(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function saveKey(value: string): void {
  try {
    localStorage.setItem(KEY, value.trim());
  } catch {
    /* private mode: the key lives for this tab only */
  }
}

export function clearKey(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

/** A key sent as a link (`#/?key=…`). Read once at start-up; the view router then rewrites the hash. */
export function keyFromLink(): string | null {
  const query = location.hash.split("?")[1];
  const value = query ? new URLSearchParams(query).get("key") : null;
  return value?.trim() || null;
}

export function authHeaders(): Record<string, string> {
  const key = loadKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}
