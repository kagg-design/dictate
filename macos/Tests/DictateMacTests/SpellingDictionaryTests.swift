import XCTest
@testable import DictateMac

final class SpellingDictionaryTests: XCTestCase {
    func testCorrectsOptedInNamesAndSkipsURLs() {
        let dictionary = TextReplacements(
            ["пхп шторм": "PhpStorm", "осом мотив": "Awesome Motive", "диктейт": "Dictate"],
            fuzzySources: ["пхп шторм", "осом мотив"]
        )
        XCTAssertEqual(dictionary.apply("PhpStrom, awesome motve и Dictte."), "PhpStorm, Awesome Motive и Dictte.")
        XCTAssertEqual(dictionary.apply("https://phpstrom.example"), "https://phpstrom.example")
    }

    func testExactRulesWinAndAmbiguousMatchesStayIntact() {
        let dictionary = TextReplacements(
            ["alias": "Codex", "other": "Codey", "codex": "editor", "switch": "codex"],
            fuzzySources: ["alias", "other"]
        )
        XCTAssertEqual(dictionary.apply("Codet, codex, switch"), "Codet, editor, codex")
    }
}
