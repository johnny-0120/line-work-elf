"""Validate grounded discussion topics and render owner-only digest sections."""


def render_topics(topics, originals):
    if not isinstance(topics, list) or len(topics) > 4:
        raise ValueError('Invalid discussion topics')
    lines = []
    for topic in topics:
        if not isinstance(topic, dict):
            raise ValueError('Invalid discussion topic')
        refs = topic.get('references')
        if not isinstance(refs, list) or not 1 <= len(refs) <= 6:
            raise ValueError('Discussion evidence required')
        sources, quotes = set(), []
        for ref in refs:
            original = originals[ref['message_id']]
            quote = ref['evidence']
            if not isinstance(quote, str) or len(quote.strip()) < 2 or quote not in original['text']:
                raise ValueError('Invalid discussion evidence')
            sources.add(original['source'])
            quotes.append((original, quote))
        if len(sources) != 1:
            raise ValueError('Cannot merge unrelated sources')

        def field(key, limit, required=False):
            value = topic.get(key, '')
            if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
                raise ValueError('Invalid discussion text')
            return value.strip()

        title = field('topic', 40, True)
        discussion = field('discussion', 160, True)
        decisions = field('decisions', 120)
        unresolved = field('open_questions', 120)
        suggestions = topic.get('suggested_next_steps', [])
        if (not isinstance(suggestions, list) or len(suggestions) > 2
                or any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 100 for x in suggestions)):
            raise ValueError('Invalid next steps')
        lines.extend(['\n【討論主題｜' + quotes[0][0]['name'] + '】' + title,
                      '聊了什麼：' + discussion])
        if decisions:
            lines.append('已確認結論：' + decisions)
        if unresolved:
            lines.append('尚待釐清：' + unresolved)
        if suggestions:
            lines.append('建議下一步（AI 建議，尚未決定或執行）：')
            lines.extend('・' + value.strip() for value in suggestions)
        lines.extend('依據：' + quote[:100] for _, quote in quotes[:2])
        if len(quotes) > 2:
            lines.append('另參考同一來源 ' + str(len(quotes) - 2) + ' 則新訊息。')
    return lines
