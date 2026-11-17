"""Read-only tools for the application's imported text, separate from CAD help."""
from .knowledge import KnowledgeBase


def spec(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': list(required),
                           'additionalProperties': False}}}


OFFSET = {'type': 'integer', 'minimum': 0, 'maximum': 10000000}
TOOLS = {item['function']['name']: item for item in [
    spec('knowledge_list', 'List files imported into the user knowledge library. Filter by filename substring and page with next_offset.',
         {'query': {'type': 'string', 'maxLength': 240}, 'offset': OFFSET}),
    spec('knowledge_search', 'Search indexed text in the uploaded library. Returns relevant excerpts, not a complete inventory.',
         {'query': {'type': 'string', 'minLength': 1, 'maxLength': 1000}}, ['query']),
    spec('knowledge_read', 'Read ordered text chunks from one imported file by its library document_id. Continue at next_offset if needed.',
         {'document_id': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}, 'offset': OFFSET}, ['document_id']),
]}
PURPOSES = {'knowledge_list': '查看已导入的知识库文件', 'knowledge_search': '检索知识库中的相关片段',
            'knowledge_read': '读取指定知识库文件的文本片段'}


async def call(kb: KnowledgeBase, name: str, args: dict):
    if name == 'knowledge_list':
        return kb.db.knowledge_page(**args), []
    if name == 'knowledge_search':
        report = {}
        sources = await kb.search(args['query'], report=report)
        return {'matches': sources, **report}, sources
    if name == 'knowledge_read':
        page = kb.db.document_page(**args)
        return page, [{'document_id': page['document_id'], 'name': page['name'], **chunk}
                      for chunk in page['chunks']]
    raise ValueError('Unknown knowledge tool')
