export function canonicalSearch(params: URLSearchParams): string {
  const normalized = new URLSearchParams(params);
  normalized.sort();
  return normalized.toString();
}
