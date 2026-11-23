import re
import sys
import threading
from dataclasses import dataclass

from src.logger import logger

uia_client = None
if sys.platform == "win32":
    try:
        import comtypes
        import comtypes.client as uia_client
    except ImportError:
        pass

CONTEXT_CHARACTERS = 48
CONTEXT_TIMEOUT_SECONDS = 0.25
_context_read_lock = threading.Lock()
_first_word = re.compile(r'''^[«“"(]*([^\W\d_]+)(?=\s|$|[.,!?…:;»”")]+(?:\s|$))''')
_abbreviation = re.compile(r"\b(?:[^\W\d_]\.){2,}$")
_sentence_endings = ".!?…\r\n"
_opening_punctuation = '«“"([{'
_closing_punctuation = ".,!?…:;»”’)]}"


@dataclass(frozen=True)
class CursorContext:
    before: str
    after: str
    search_field: bool = False


def format_for_cursor(text, context):
    text = text.lstrip(" \t")
    if not text:
        return text
    if context is None:
        return text if text[-1].isspace() else text + " "

    text = text.strip(" \t")
    if not text:
        return text

    before, after = context.before, context.after
    following = after.lstrip(" \t")
    if context.search_field or (following and following[0] not in "\r\n"):
        if (
            len(text) > 1 and text.endswith(".") and not text.endswith("..")
            and not _abbreviation.search(text)
        ):
            text = text[:-1]

    if not context.search_field:
        preceding = before.rstrip(" \t")
        sentence_start = not preceding or preceding[-1] in _sentence_endings
        continues_sentence = preceding and (
            preceding[-1].isalnum() or preceding[-1] in ",;:»”)]}"
        )
        first_word = _first_word.match(text)
        if first_word:
            word = first_word[1]
            start = first_word.start(1)
            if sentence_start and word.islower():
                text = text[:start] + word[0].upper() + text[start + 1:]
            elif continues_sentence and word.istitle() and word != "I":
                text = text[:start] + word[0].lower() + text[start + 1:]

    if before and (before[-1].isalnum() or before[-1] in _closing_punctuation):
        if text[0].isalnum() or text[0] in _opening_punctuation:
            text = " " + text
    if not text[-1].isspace():
        if not after and not context.search_field:
            text += " "
        elif after and (after[0].isalnum() or after[0] in _opening_punctuation):
            if text[-1].isalnum() or text[-1] in _closing_punctuation:
                text += " "
    return text


def read_cursor_context():
    # The provider is in another process and can stall. Keep at most one
    # lookup in flight and let dictation continue when it takes too long.
    if uia_client is None or not _context_read_lock.acquire(blocking=False):
        return None

    result = []

    def lookup():
        try:
            result.append(_read_ui_automation_context())
        except Exception as error:
            logger.debug("Cursor context unavailable (%s).", type(error).__name__)
        finally:
            _context_read_lock.release()

    reader = threading.Thread(target=lookup, name="CursorContextReader", daemon=True)
    try:
        reader.start()
    except RuntimeError:
        _context_read_lock.release()
        return None
    reader.join(CONTEXT_TIMEOUT_SECONDS)
    return result[0] if result else None


def _read_ui_automation_context():
    comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    automation = None
    try:
        uia = uia_client.GetModule("UIAutomationCore.dll")
        automation = uia_client.CreateObject(uia.CUIAutomation8, interface=uia.IUIAutomation2)
        automation.ConnectionTimeout = int(CONTEXT_TIMEOUT_SECONDS * 1000)
        automation.TransactionTimeout = int(CONTEXT_TIMEOUT_SECONDS * 1000)
        return _read_focused_selection(automation, uia)
    finally:
        automation = None
        comtypes.CoUninitialize()


def _read_focused_selection(automation, uia):
    element = automation.GetFocusedElement()
    if not element or element.CurrentIsPassword or not element.CurrentHasKeyboardFocus:
        return None
    control_type = element.CurrentControlType
    if control_type not in (uia.UIA_EditControlTypeId, uia.UIA_DocumentControlTypeId):
        return None

    pattern = element.GetCurrentPattern(uia.UIA_TextPatternId)
    if not pattern:
        return None
    ranges = pattern.QueryInterface(uia.IUIAutomationTextPattern).GetSelection()
    if not ranges or ranges.Length != 1:
        return None
    selection = ranges.GetElement(0)
    if not selection:
        return None
    if control_type == uia.UIA_DocumentControlTypeId:
        if selection.GetAttributeValue(uia.UIA_IsReadOnlyAttributeId) is not False:
            return None

    start = uia.TextPatternRangeEndpoint_Start
    end = uia.TextPatternRangeEndpoint_End
    before = selection.Clone()
    before.MoveEndpointByRange(end, selection, start)
    left_count = before.MoveEndpointByUnit(start, uia.TextUnit_Character, -CONTEXT_CHARACTERS)
    after = selection.Clone()
    after.MoveEndpointByRange(start, selection, end)
    after.MoveEndpointByUnit(end, uia.TextUnit_Character, CONTEXT_CHARACTERS)

    # A provider can count graphemes rather than UTF-16 units. Reject ranges
    # that exceed this budget so a truncated prefix can't be used at the caret.
    text_limit = 2 * CONTEXT_CHARACTERS
    left_text = (before.GetText(text_limit) or "") if left_count else ""
    right_text = after.GetText(text_limit) or ""
    if max(len(left_text), len(right_text)) >= text_limit:
        return None

    search_field = (element.CurrentAriaRole or "").casefold() == "searchbox" or any(
        (identifier or "").casefold() in ("searchbox", "addressbar", "omniboxviewviews")
        for identifier in (element.CurrentAutomationId, element.CurrentClassName)
    )
    if not element.CurrentHasKeyboardFocus:
        return None
    return CursorContext(left_text, right_text, search_field)
