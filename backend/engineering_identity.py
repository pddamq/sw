"""Live document bindings, distinct from immutable engineering requirements."""
from __future__ import annotations

import copy
import ntpath


EXTENSIONS = {'part': '.sldprt', 'assembly': '.sldasm', 'drawing': '.slddrw'}


def alias(value):
    return ntpath.normpath(value.replace('/', '\\')).casefold() if isinstance(value, str) and value else None


def ready(status):
    return (status.get('attached') is True and isinstance(status.get('documents'), list)
            and type(status.get('solidWorksPid')) is int and status['solidWorksPid'] > 0
            and not any(status.get(k) for k in ('uiBlocked', 'runningOperation', 'warnings', 'truncated')))


def identity(doc, status):
    return {'handle': doc.get('handle'), 'title': doc.get('title'), 'path': alias(doc.get('path')),
            'type': doc.get('type'), 'solidWorksPid': status.get('solidWorksPid'),
            'operationLog': status.get('operationLog')}


def native_path(path, kind):
    if not isinstance(path, str) or not path.strip():
        raise ValueError('首次保存需要明确的 path。')
    path = path.strip().replace('/', '\\')
    extension = EXTENSIONS[kind]
    if not path.lower().endswith(extension):
        path += extension
    return ntpath.normpath(path)


def title_stem(title):
    stem, extension = ntpath.splitext(title)
    return stem if extension.casefold() in EXTENSIONS.values() else title


class DocumentBindings:
    def __init__(self, error_type):
        self.entries = {}
        self.error_type = error_type

    def fail(self):
        raise self.error_type('已确认文档的路径、句柄或连接身份发生变化；停止修改，请核对文件后新建任务重新确认。')

    def capture(self, plans, status):
        if not ready(status):
            self.fail()
        captured = {}
        for plan in plans:
            title = plan['target_document']
            expected_title = (self.entries.get(title) or {}).get('title', title)
            docs = [d for d in status['documents'] if isinstance(d, dict)
                    and alias(d.get('title')) == alias(expected_title)]
            if len(docs) > 1:
                self.fail()
            if docs and (docs[0].get('title') != expected_title or not docs[0].get('handle')):
                self.fail()
            captured[title] = identity(docs[0], status) if docs else None
        return captured

    def confirm(self, captured, status):
        if not ready(status):
            self.fail()
        for title, binding in captured.items():
            expected_title = binding['title'] if binding else title
            docs = [d for d in status['documents'] if isinstance(d, dict)
                    and alias(d.get('title')) == alias(expected_title)]
            if binding is None:
                if docs:
                    self.fail()
            elif len(docs) != 1 or identity(docs[0], status) != binding:
                self.fail()

    def matches(self, title, doc, status):
        binding = self.entries.get(title)
        return (ready(status) and binding is not None and identity(doc, status) == binding
                and sum(alias(d.get('title')) == alias(doc.get('title'))
                        for d in status['documents'] if isinstance(d, dict)) == 1)

    def locate(self, arguments, status, contracts):
        if not ready(status):
            self.fail()
        docs = [d for d in status['documents'] if isinstance(d, dict)]
        addressed = arguments.get('doc')
        if addressed is None:
            matches = [d for d in docs if d.get('target') is True]
        else:
            key = alias(addressed)
            matches = [d for d in docs if key and key in {alias(d.get(k)) for k in ('title', 'handle', 'path')}]
            if not matches:
                handles = {b['handle'] for title, b in self.entries.items()
                           if b and alias(title) == key}
                matches = [d for d in docs if d.get('handle') in handles]
        if len(matches) != 1:
            raise ValueError('未唯一定位操作文档或超出确认范围；请读取 doc_list 并明确 doc，不执行修改。')
        doc = matches[0]
        title = next((title for title in contracts if title == doc.get('title') or
                      (self.entries.get(title) and self.entries[title]['handle'] == doc.get('handle'))), None)
        if title is None:
            raise ValueError('本次修改目标超出已确认的文档范围；请单独调用 engineering_scope_extend 追加该文档并等待确认。')
        expected_type = contracts[title].get('document_type')
        if expected_type and doc.get('type') != expected_type:
            raise self.error_type('现场文档类型与已确认要求不一致，停止修改。')
        if not self.matches(title, doc, status):
            self.fail()
        return title, doc

    def validate_new(self, title, status):
        if not ready(status):
            self.fail()
        if self.entries.get(title):
            raise ValueError('同名文档已经打开或已绑定，请复用其句柄；不要重建已有零件。')
        if any(alias(d.get('title')) == alias(title) for d in status['documents'] if isinstance(d, dict)):
            raise ValueError('同名文档已经打开，请复用其句柄；不要重建已有零件。')

    def created(self, title, status, result):
        if result.get('warnings'):
            self.fail()
        captured = self.capture([{'target_document': title}], status)
        doc = result.get('document', {})
        binding = captured[title]
        if not binding or doc.get('handle') != binding['handle'] or result.get('doc') != binding['title']:
            self.fail()
        self.entries[title] = binding

    def validate_first_save(self, title, doc, arguments):
        if doc.get('path') or doc.get('access') != 'owned':
            raise ValueError('mode=as 仅用于已确认且由执行器拥有的未保存新文档；已有文件请用 inPlace 或 copy。')
        path = native_path(arguments.get('path'), doc['type'])
        if title_stem(ntpath.basename(path)).casefold() != title_stem(title).casefold():
            raise ValueError('首次保存文件名须与已确认文档名称一致；本接口不允许重命名目标。')
        return path

    def saved(self, title, before, status, result, requested_path):
        if not ready(status) or result.get('warnings'):
            self.fail()
        saved = result.get('savedTo')
        actual = alias(saved)
        requested = alias(requested_path)
        if not actual or (actual != requested if ntpath.isabs(requested_path)
                          else not actual.endswith('\\' + requested)):
            self.fail()
        docs = [d for d in status['documents'] if isinstance(d, dict) and d.get('handle') == before['handle']]
        if len(docs) != 1:
            self.fail()
        doc = docs[0]
        expected_names = {ntpath.basename(requested_path).casefold(), title_stem(ntpath.basename(requested_path)).casefold()}
        after = identity(doc, status)
        if (after['path'] != actual or str(after['title']).casefold() not in expected_names
                or any(after[k] != before[k] for k in ('handle', 'type', 'solidWorksPid', 'operationLog'))
                or result.get('document', {}).get('handle') != after['handle']
                or result.get('doc') != after['title']):
            self.fail()
        if (any(other != title and binding and alias(binding['title']) == alias(after['title'])
                for other, binding in self.entries.items())
                or sum(alias(d.get('title')) == alias(after['title']) for d in status['documents']
                       if isinstance(d, dict)) != 1):
            self.fail()
        self.entries[title] = copy.deepcopy(after)
