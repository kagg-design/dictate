import unittest

from src.replacements import TextReplacements


class SpellingDictionaryTests(unittest.TestCase):
    def test_corrects_one_typo_in_opted_in_names(self):
        rules = TextReplacements.from_config(
            {"пхп шторм": "PhpStorm", "осом мотив": "Awesome Motive", "диктейт": "Dictate"},
            ["пхп шторм", "осом мотив"],
        )
        self.assertEqual(
            rules.apply("Открой PhpStrom, awesome motve и Dictte в пхп шторм."),
            "Открой PhpStorm, Awesome Motive и Dictte в PhpStorm.",
        )
        self.assertEqual(rules.apply("https://phpstrom.example и Awesome, motve"), "https://phpstrom.example и Awesome, motve")

    def test_keeps_exact_rules_and_skips_ambiguous_matches(self):
        rules = TextReplacements.from_config(
            {"alias": "Codex", "other": "Codey", "codex": "editor", "switch": "codex"},
            ["alias", "other"],
        )
        self.assertEqual(rules.apply("Codet, codex, switch"), "Codet, editor, codex")

    def test_edit_and_removal_update_dictionary_membership(self):
        rules = TextReplacements().update("пхп шторм", "PhpStorm", fuzzy=True)
        edited = rules.update("шторм", "PhpStorm", previous="пхп шторм")
        self.assertEqual(edited.fuzzy_sources, {"шторм"})
        self.assertEqual(edited.remove("шторм").apply("PhpStrom"), "PhpStrom")
