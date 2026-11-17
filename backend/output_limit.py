"""Output-budget settlement, separate from transport and execution failures."""


class OutputLimitReached(RuntimeError):
    def __init__(self, limit: int, usage: dict | None = None):
        self.limit = limit
        self.usage = dict(usage or {})
        reported = self.usage.get('completion_tokens')
        measurement = f'服务报告输出 {reported} token。' if reported is not None else '服务未报告实际输出 token 用量。'
        super().__init__(
            f'模型未完整生成回复：服务报告输出达到上限（max-tokens / length），本次请求上限 {limit:,} token。'
            + measurement + '触顶步骤的已生成内容保留为未完成预览，该步骤的工具调用全部未执行；其他步骤已完成的操作保留。'
            '触顶步骤不自动重试；已有插话可进入下一步，任务仍保留触顶状态。'
            '可发送“继续”从已完成结果重新规划，或调整模型输出额度。')


def reported_usage(value) -> dict:
    """Keep only provider-reported nonnegative integers, never estimated counts."""
    result = {}
    for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        number = getattr(value, name, None)
        if type(number) is int and number >= 0:
            result[name] = number
    details = getattr(value, 'completion_tokens_details', None)
    number = details.get('reasoning_tokens') if isinstance(details, dict) else getattr(details, 'reasoning_tokens', None)
    if type(number) is int and number >= 0:
        result['reasoning_tokens'] = number
    return result
