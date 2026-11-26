import XCTest
@testable import DictateMac

final class InsertionTargetTests: XCTestCase {
    func testSlackTypingKeepsUnicodeButCannotTypeReturnOrTab() {
        let transcript = "Начало\r\n👩‍💻\tSlack\u{2028}конец\u{1B}"
        XCTAssertEqual(InsertionTarget.slackTypingText(transcript), "Начало 👩‍💻 Slack конец ")
    }
}
