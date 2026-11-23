import XCTest
@testable import DictateMac

final class PhraseBoundaryTests: XCTestCase {
    func testContinuousSpeechRemainsAvailableForFinalDecode() {
        XCTAssertNil(PhraseBoundary.last(in: [Float](repeating: 0.2, count: 48_000)))
    }

    func testCompletedPhraseKeepsTheFollowingSpeech() {
        let phrase = [Float](repeating: 0.2, count: 19_200)
        let pause = [Float](repeating: 0, count: 7_680)
        let next = [Float](repeating: 0.15, count: 6_400)
        let capture = phrase + pause + next
        let cut = PhraseBoundary.last(in: capture)
        XCTAssertEqual(cut, phrase.count + pause.count / 2)
        if let cut { XCTAssertEqual(Array(capture.dropFirst(cut)).suffix(next.count), next[...]) }
    }
}
