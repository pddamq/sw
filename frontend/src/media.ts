export type MediaItem = {
  id: string;
  name: string;
  kind: "reference" | "tool" | "document";
  width: number;
  height: number;
  size: number;
  task_id?: string;
};
export function validImageFile(
  file: Pick<File, "type" | "size">,
): string | null {
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type))
    return "请选择 PNG、JPEG 或 WebP 图片。";
  if (!file.size || file.size > 8 * 1024 * 1024)
    return "图片大小应在 0～8 MB 之间。";
  return null;
}
