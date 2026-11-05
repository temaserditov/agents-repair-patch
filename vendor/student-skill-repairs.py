"""Neutral student adaptations; never imports the owner's personal voice data."""
import re

OWNER_PATHS = re.compile(r'knowledge/voice(?:bank)?/|corpus-findings|owner-sample-routing|project-writing/references/owner-|READY_CHECKLIST|validate-final-copy|VoiceBank')
STUDENT_SOURCES = '''Используй только факты и материалы текущего пользователя. Его образцы речи
необязательны: если их нет, пиши нейтрально и не выдумывай авторскую манеру.
Не требуй личный корпус автора курса или машинную проверку, которой нет в поставке.
Факты, понятность, жанр и точный финальный текст проверяй независимо от наличия образцов.'''
CONTENT_ENGINE_PATH = 'tools/swift-design-engine/CONTENT_ENGINE.md'
STUDENT_ENGINE_PATH = '~/Documents/Content/CONTENT_ENGINE.md'


def markdown_parts(text):
    """Yield (is_code, text), including fences and unclosed code verbatim."""
    pending = []
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*?)(?:\r?\n)?$', line)
        if fence is None and marker:
            if pending:
                yield False, ''.join(pending)
            pending = [line]
            fence = marker.group(1)
        elif fence is not None:
            pending.append(line)
            if (marker and marker.group(1)[0] == fence[0]
                    and len(marker.group(1)) >= len(fence) and not marker.group(2).strip()):
                yield True, ''.join(pending)
                pending = []
                fence = None
        else:
            pending.append(line)
    if pending:
        yield fence is not None, ''.join(pending)


def repair_prose(text):
    # Preserve paragraph boundaries and unrelated list items. A wrapped sentence
    # can span lines; a personal path must not remove the rest of its paragraph.
    units = re.split(r'(\n[ \t]*\n)|(?=^[ \t]*(?:[-*+]\s|\d+[.)]\s|#{1,6}\s))', text, flags=re.MULTILINE)
    result = []
    for unit in units:
        if not unit:
            continue
        if not OWNER_PATHS.search(unit):
            result.append(unit)
            continue
        sentences = re.split(r'(?<=[.!?])([ \t\r\n]+)(?=[A-ZА-ЯЁ])', unit)
        for index in range(0, len(sentences), 2):
            sentence = sentences[index]
            if not OWNER_PATHS.search(sentence):
                result.append(sentence)
                if index + 1 < len(sentences):
                    result.append(sentences[index + 1])
    return ''.join(result)


def repair_markdown(name, data):
    text = data.decode('utf-8')
    # This path relocation is explicitly required everywhere, for any profile.
    text = text.replace(CONTENT_ENGINE_PATH, STUDENT_ENGINE_PATH)
    needs_sources = bool(OWNER_PATHS.search(text))
    text = ''.join(part if code else repair_prose(part) for code, part in markdown_parts(text))
    if needs_sources and STUDENT_SOURCES not in text:
        note = '## Источники для ученической версии\n\n' + STUDENT_SOURCES + '\n\n'
        header = re.match(r'\A---\r?\n.*?\r?\n---(?:\r?\n|$)', text, re.S)
        offset = header.end() if header else 0
        text = text[:offset] + '\n' + note + text[offset:]
    return text.encode('utf-8')
