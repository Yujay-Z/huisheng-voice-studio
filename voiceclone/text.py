import difflib
import re
import unicodedata


def normalize(text):
    # Keep numerals and homophones distinct: safety takes precedence over acceptance.
    text = unicodedata.normalize('NFKC', text).casefold()
    return ''.join(c for c in text if c.isalnum())


def compare(expected, actual):
    a, b = normalize(expected), normalize(actual)
    changes = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i, j, k, l in matcher.get_opcodes():
        if tag != 'equal':
            changes.append({'type': tag, 'expected': a[i:j], 'actual': b[k:l]})
    return {'passed': bool(a) and a == b, 'expected': expected, 'recognized': actual,
            'differences': changes, 'similarity': round(matcher.ratio(), 4)}


def split_text(text, limit=72, pack=False):
    text = text.strip()
    if not normalize(text):
        raise ValueError('请输入需要朗读的文字。')
    # Require explicit spoken forms; do not silently rewrite numbers, units or symbols.
    if re.search(r'[<>\[\]{}]|https?://', text):
        raise ValueError('请把标记或网址改写成要实际朗读的文字，不支持模型控制标签。')
    parts = re.findall(r'[^。！？!?；;\n]+[。！？!?；;]?|[。！？!?；;]', text)
    result = []
    for part in parts:
        part = part.strip()
        while len(part) > limit:
            cut = max(part.rfind('，', 0, limit), part.rfind(',', 0, limit),
                      part.rfind(' ', 0, limit))
            cut = cut + 1 if cut >= limit // 3 else limit
            result.append(part[:cut].strip())
            part = part[cut:].strip()
        if normalize(part):
            result.append(part)
    if normalize(''.join(result)) != normalize(text):
        raise ValueError('文字分句失败，请检查输入。')
    if pack:
        grouped = []
        for part in result:
            if grouped and len(grouped[-1]) + 1 + len(part) <= limit:
                grouped[-1] += ' ' + part
            else:
                grouped.append(part)
        return grouped
    return result
