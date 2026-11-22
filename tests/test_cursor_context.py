import unittest
from unittest.mock import Mock

from src.cursor_context import CursorContext, _read_focused_selection, format_for_cursor


class CursorFormattingTests(unittest.TestCase):
    def test_formats_text_against_both_sides_of_the_caret(self):
        cases = (
            ("хороший.", CursorContext("", ""), "Хороший. "),
            ("хороший.", CursorContext("Первый.  ", ""), "Хороший. "),
            ("хороший.", CursorContext("Первая строка\r\n  ", ""), "Хороший. "),
            ("Хороший.", CursorContext("Это", "пример."), " хороший "),
            ("Хороший.", CursorContext("Это ", " пример."), "хороший"),
            ("Хороший.", CursorContext("Это ", "."), "хороший"),
            ("Хороший?", CursorContext("Это ", "продолжение"), "хороший? "),
            ("Хороший...", CursorContext("Это ", "продолжение"), "хороший... "),
            ("Хороший.", CursorContext("Это (", ")"), "Хороший"),
            ("Хороший.", CursorContext("Это ", "\r\nДалее"), "хороший."),
            ("Пример.", CursorContext("", "", search_field=True), "Пример"),
            ("документы.", CursorContext("поиск", "", search_field=True), " документы"),
            ("U.S.", CursorContext("", "", search_field=True), "U.S."),
            ("API.", CursorContext("Это ", " пример."), "API"),
            ("PhpStorm.", CursorContext("Это ", " пример."), "PhpStorm"),
            ("api_key.", CursorContext("", ""), "api_key. "),
            ("I agree.", CursorContext("I think ", " with that."), "I agree"),
            ("Пример.", None, "Пример. "),
        )
        for text, context, expected in cases:
            with self.subTest(text=text, context=context):
                self.assertEqual(format_for_cursor(text, context), expected)


class SelectionContextTests(unittest.TestCase):
    def setUp(self):
        self.uia = Mock()
        self.automation = Mock()
        self.element = self.automation.GetFocusedElement.return_value
        self.element.CurrentIsPassword = False
        self.element.CurrentHasKeyboardFocus = True
        self.element.CurrentControlType = self.uia.UIA_EditControlTypeId
        self.element.CurrentAriaRole = "searchbox"
        self.ranges = (
            self.element.GetCurrentPattern.return_value.QueryInterface.return_value
            .GetSelection.return_value
        )
        self.ranges.Length = 1

    def test_reads_outside_selection_and_marks_search_field(self):
        selection = self.ranges.GetElement.return_value
        before, after = Mock(), Mock()
        selection.Clone.side_effect = [before, after]
        before.GetText.return_value = "before "
        after.GetText.return_value = " after"

        self.assertEqual(
            _read_focused_selection(self.automation, self.uia),
            CursorContext("before ", " after", search_field=True),
        )

        start = self.uia.TextPatternRangeEndpoint_Start
        end = self.uia.TextPatternRangeEndpoint_End
        before.MoveEndpointByRange.assert_called_once_with(end, selection, start)
        after.MoveEndpointByRange.assert_called_once_with(start, selection, end)
        selection.GetText.assert_not_called()

    def test_password_field_is_skipped_before_reading_text(self):
        self.element.CurrentIsPassword = True

        self.assertIsNone(_read_focused_selection(self.automation, self.uia))
        self.element.GetCurrentPattern.assert_not_called()

    def test_multiple_selections_are_skipped(self):
        self.ranges.Length = 2

        self.assertIsNone(_read_focused_selection(self.automation, self.uia))
        self.ranges.GetElement.assert_not_called()
