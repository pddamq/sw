export type SourceSnippet = { name: string; text: string; document_id?: string; chunk_id?: string | number; index?: number };
export function sourceKey(source: SourceSnippet) {
  return JSON.stringify([source.document_id || source.name, source.chunk_id ?? source.text]);
}
export function mergeSources(previous: SourceSnippet[], incoming: SourceSnippet[]) {
  const unique = new Map(previous.map(source => [sourceKey(source), source]));
  for (const source of incoming) unique.set(sourceKey(source), source);
  return [...unique.values()];
}
export function groupSources(sources: SourceSnippet[]) {
  const groups = new Map<string, { id: string; name: string; snippets: SourceSnippet[] }>();
  for (const source of mergeSources([], sources)) {
    const id = source.document_id || source.name;
    if (!groups.has(id)) groups.set(id, { id, name: source.name, snippets: [] });
    groups.get(id)!.snippets.push(source);
  }
  return [...groups.values()];
}

export type SourceGroup = ReturnType<typeof groupSources>[number];
export const SOURCE_GROUP_LIMIT = 6;
export const SOURCE_SNIPPET_LIMIT = 3;

/** Collapses long citation lists so a single turn stays readable; expanding shows every group and snippet. */
export function summarizeSources(groups: SourceGroup[], expanded: boolean,
  groupLimit = SOURCE_GROUP_LIMIT, snippetLimit = SOURCE_SNIPPET_LIMIT) {
  const totalSnippets = groups.reduce((sum, group) => sum + group.snippets.length, 0);
  const collapsedTruncated = groups.length > groupLimit || groups.some(group => group.snippets.length > snippetLimit);
  if (expanded) return { groups: groups.map(group => ({ ...group, totalSnippets: group.snippets.length })), totalSnippets, hiddenGroups: 0, hiddenSnippets: 0, truncated: false, canCollapse: collapsedTruncated };
  const visible = groups.slice(0, groupLimit)
    .map(group => ({ ...group, totalSnippets: group.snippets.length, snippets: group.snippets.slice(0, snippetLimit) }));
  const hiddenGroups = Math.max(0, groups.length - groupLimit);
  const hiddenSnippets = totalSnippets - visible.reduce((sum, group) => sum + group.snippets.length, 0);
  return { groups: visible, totalSnippets, hiddenGroups, hiddenSnippets, truncated: hiddenGroups > 0 || hiddenSnippets > 0, canCollapse: false };
}
