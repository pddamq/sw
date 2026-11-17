export interface KnowledgeDocument {
  id: string;
  name: string;
  chunks: number;
  created_at: string;
}
export interface KnowledgeFolder {
  name: string;
  path: string;
  folders: KnowledgeFolder[];
  documents: KnowledgeDocument[];
}

export function knowledgeTree(documents: KnowledgeDocument[]): KnowledgeFolder {
  const root: KnowledgeFolder = { name: '', path: '', folders: [], documents: [] };
  const folders = new Map<string, KnowledgeFolder>([['', root]]);
  for (const document of documents) {
    const parts = document.name.split('/');
    let parent = root;
    for (const name of parts.slice(0, -1)) {
      const path = parent.path ? `${parent.path}/${name}` : name;
      let folder = folders.get(path);
      if (!folder) {
        folder = { name, path, folders: [], documents: [] };
        folders.set(path, folder);
        parent.folders.push(folder);
      }
      parent = folder;
    }
    parent.documents.push(document);
  }
  for (const folder of folders.values()) {
    folder.folders.sort((a, b) => a.name.localeCompare(b.name));
    folder.documents.sort((a, b) => a.name.localeCompare(b.name));
  }
  return root;
}

export interface ImportResult {
  path: string;
  status: 'imported' | 'skipped' | 'failed';
  detail: string;
}
export interface ImportProgress {
  total: number;
  completed: number;
  current: string;
}

export async function importKnowledgeFiles(
  files: File[],
  send: (body: FormData) => Promise<unknown>,
  progress: (value: ImportProgress) => void,
  blocked: () => boolean = () => false,
): Promise<ImportResult[]> {
  const results: ImportResult[] = [];
  let stopped = '';
  for (const file of files) {
    const path = file.webkitRelativePath || file.name;
    progress({ total: files.length, completed: results.length, current: path });
    let result: ImportResult;
    if (stopped || blocked()) {
      result = { path, status: 'skipped', detail: stopped || '当前任务正在执行，请结束后重新选择此文件导入。' };
    } else if (!/\.(txt|md|html?|pdf|csv|json|log|xml|ya?ml|docx|xlsx|xlsm|pptx)$/i.test(file.name)) {
      result = { path, status: 'skipped', detail: '格式未支持；支持 TXT、Markdown、HTML、CSV、JSON、XML、YAML、DOCX、XLSX、PPTX 和文本型 PDF。' };
    } else if (file.size > 20 * 1024 * 1024) {
      result = { path, status: 'skipped', detail: '文件超过 20 MB。' };
    } else {
      const body = new FormData();
      body.append('file', file, file.name);
      body.append('relative_path', path);
      try {
        // Sequential requests keep memory bounded and preserve the server execution lock.
        await send(body);
        result = { path, status: 'imported', detail: '已索引' };
      } catch (error) {
        const status = (error as { status?: number })?.status;
        const detail = error instanceof Error ? error.message : '导入失败';
        result = { path, status: 'failed', detail: status ? detail : `${detail}；结果待核实，请刷新目录确认后再重试。` };
        if (!status || status === 401 || status === 403 || status === 409) {
          stopped = '批次已暂停，请核实连接、任务状态及已导入文件后重新选择剩余文件。';
        }
      }
    }
    results.push(result);
    progress({ total: files.length, completed: results.length, current: path });
  }
  return results;
}
