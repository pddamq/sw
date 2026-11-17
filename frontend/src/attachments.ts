export const attachmentAccept = '.png,.jpg,.jpeg,.webp,.txt,.md,.pdf,.html,.htm,.csv,.json,.log,.xml,.yaml,.yml';
const extensions = new Set(attachmentAccept.split(','));
export function validAttachment(file: Pick<File, 'name' | 'size'>): string | null {
  const suffix = '.' + file.name.split('.').pop()?.toLowerCase();
  if (!extensions.has(suffix)) return `${file.name}：请选择图片、TXT、MD、PDF、HTML、CSV、JSON、LOG、XML 或 YAML 文件。`;
  if (!file.size || file.size > 8 * 1024 * 1024) return `${file.name}：文件应大于 0 字节，且最大为 8 MB。`;
  return null;
}
export function hasFileDrag(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types || []).includes('Files');
}
