import AppKit
import SwiftUI

struct TextReplacements {
    struct Rule: Identifiable {
        let source: String
        let replacement: String
        var id: String { source }
    }

    let rules: [Rule]
    let fuzzySources: Set<String>
    private let ordered: [Rule]
    private let pattern: NSRegularExpression?
    private let dictionary: [Int: [(words: [String], spelling: String)]]
    private static let wordPattern = try! NSRegularExpression(
        pattern: #"(?<![\w./@\\-])\p{L}+(?![\w/@\\-]|\.\w)"#
    )

    init(_ saved: [String: String] = [:], fuzzySources: Set<String> = []) {
        var unique: [String: Rule] = [:]
        for (key, value) in saved.sorted(by: { $0.key < $1.key }) {
            let source = key.trimmingCharacters(in: .whitespacesAndNewlines)
            let replacement = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if !source.isEmpty, !replacement.isEmpty {
                unique[source.lowercased()] = Rule(source: source, replacement: replacement)
            }
        }
        rules = unique.values.sorted { $0.source.localizedCaseInsensitiveCompare($1.source) == .orderedAscending }
        let enabled = Set(fuzzySources.map { $0.lowercased() })
        self.fuzzySources = Set(rules.map(\.source).filter { enabled.contains($0.lowercased()) })
        let terms: [(words: [String], spelling: String)] = rules.compactMap { rule in
            guard enabled.contains(rule.source.lowercased()), Self.supportsFuzzySpelling(rule.replacement)
            else { return nil }
            return (rule.replacement.split(whereSeparator: \.isWhitespace).map { Self.normalize(String($0)) },
                    rule.replacement)
        }
        dictionary = Dictionary(grouping: terms, by: { $0.words.count })
        ordered = rules.sorted { $0.source.count > $1.source.count }
        let alternatives = ordered.map { "(" + NSRegularExpression.escapedPattern(for: $0.source) + ")" }
        pattern = alternatives.isEmpty ? nil : try? NSRegularExpression(
            pattern: "(?<!\\w)(?:" + alternatives.joined(separator: "|") + ")(?!\\w)",
            options: [.caseInsensitive]
        )
    }

    var saved: [String: String] { Dictionary(uniqueKeysWithValues: rules.map { ($0.source, $0.replacement) }) }

    func apply(_ text: String) -> String {
        guard let pattern else { return text }
        let bounds = NSRange(text.startIndex..<text.endIndex, in: text)
        let source = text as NSString
        var result = ""
        var end = 0
        for match in pattern.matches(in: text, range: bounds) {
            if let index = ordered.indices.first(where: { match.range(at: $0 + 1).location != NSNotFound }) {
                result += correctTerms(text, within: NSRange(location: end, length: match.range.location - end))
                result += ordered[index].replacement
                end = NSMaxRange(match.range)
            }
        }
        return result + correctTerms(text, within: NSRange(location: end, length: source.length - end))
    }

    static func supportsFuzzySpelling(_ spelling: String) -> Bool {
        let words = spelling.split(whereSeparator: \.isWhitespace)
        return !words.isEmpty && words.allSatisfy { normalize(String($0)).allSatisfy(\.isLetter) }
    }

    private static func normalize(_ word: String) -> String {
        word.lowercased().precomposedStringWithCanonicalMapping
    }

    private static func oneTypo(_ word: String, _ term: String) -> Bool {
        let actual = Array(word), expected = Array(term)
        guard expected.count >= 5, abs(actual.count - expected.count) <= 1 else { return false }
        if actual.count == expected.count {
            let differences = actual.indices.filter { actual[$0] != expected[$0] }
            guard differences.count == 2 else { return differences.count == 1 }
            let left = differences[0], right = differences[1]
            return right == left + 1 && actual[left] == expected[right] && actual[right] == expected[left]
        }
        let (shorter, longer) = actual.count < expected.count ? (actual, expected) : (expected, actual)
        let index = shorter.indices.first { shorter[$0] != longer[$0] } ?? shorter.count
        return shorter[index...] == longer[(index + 1)...]
    }

    private func correctTerms(_ text: String, within bounds: NSRange) -> String {
        let source = text as NSString
        let fragment = source.substring(with: bounds)
        guard !dictionary.isEmpty else { return fragment }
        let matches = Self.wordPattern.matches(in: text, options: [.withTransparentBounds], range: bounds)
        let words = matches.map { Self.normalize(source.substring(with: $0.range)) }
        let lengths = dictionary.keys.sorted(by: >)
        var index = 0
        let output = NSMutableString(string: fragment)
        var replacements: [(NSRange, String)] = []
        while index < matches.count {
            for length in lengths {
                let end = index + length
                guard end <= matches.count, (index..<(end - 1)).allSatisfy({ offset in
                    let start = NSMaxRange(matches[offset].range)
                    let gap = source.substring(with: NSRange(location: start, length: matches[offset + 1].range.location - start))
                    return !gap.isEmpty && gap.allSatisfy { $0 == " " || $0 == "\t" }
                }) else { continue }
                var exact = Set<String>(), close = Set<String>()
                for term in dictionary[length, default: []] {
                    let differences = zip(words[index..<end], term.words).filter { $0.0 != $0.1 }
                    if differences.isEmpty {
                        exact.insert(term.spelling)
                    } else if differences.count == 1, Self.oneTypo(differences[0].0, differences[0].1) {
                        close.insert(term.spelling)
                    }
                }
                let spellings = exact.isEmpty ? close : exact
                guard !spellings.isEmpty else { continue }
                if spellings.count == 1, let spelling = spellings.first {
                    let start = matches[index].range.location
                    let range = NSRange(location: start - bounds.location, length: NSMaxRange(matches[end - 1].range) - start)
                    replacements.append((range, spelling))
                }
                index = end - 1
                break
            }
            index += 1
        }
        for (range, spelling) in replacements.reversed() { output.replaceCharacters(in: range, with: spelling) }
        return output as String
    }
}

struct ReplacementDraft: Identifiable {
    let id = UUID()
    var source = ""
    var value = ""
    var previous: String?
    var fuzzy = false
}

struct ReplacementEditor: View {
    private enum Field { case recognized, replacement }
    @State var draft: ReplacementDraft
    let save: (ReplacementDraft) throws -> Void
    @Environment(\.dismiss) private var dismiss
    @FocusState private var focus: Field?
    @State private var error = ""

    var body: some View {
        Form {
            TextField("Recognized text", text: $draft.source)
                .focused($focus, equals: .recognized)
            TextField("Replacement", text: $draft.value)
                .focused($focus, equals: .replacement)
            Toggle("Also correct similar spellings", isOn: $draft.fuzzy)
            if !error.isEmpty { Text(error).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Save") {
                    do { try save(draft); dismiss() }
                    catch { self.error = error.localizedDescription }
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .formStyle(.grouped)
        .padding(16)
        .frame(width: 475)
        .onAppear { focus = draft.source.isEmpty ? .recognized : .replacement }
    }
}

struct HistoryTranscript: NSViewRepresentable {
    let text: String
    @Binding var selection: String

    func makeCoordinator() -> Coordinator { Coordinator(selection: $selection) }

    func makeNSView(context: Context) -> NSTextView {
        let view = NSTextView(frame: .zero)
        view.isEditable = false
        view.isSelectable = true
        view.drawsBackground = false
        view.font = .systemFont(ofSize: NSFont.systemFontSize)
        view.textColor = .labelColor
        view.textContainerInset = .zero
        view.textContainer?.lineFragmentPadding = 0
        view.textContainer?.widthTracksTextView = true
        view.delegate = context.coordinator
        return view
    }

    func updateNSView(_ view: NSTextView, context: Context) {
        if view.string != text { view.string = text }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: NSTextView, context: Context) -> CGSize? {
        guard let width = proposal.width, width.isFinite, width > 0 else { return nil }
        let bounds = (text as NSString).boundingRect(
            with: NSSize(width: width, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: NSFont.systemFont(ofSize: NSFont.systemFontSize)]
        )
        return NSSize(width: width, height: max(18, ceil(bounds.height) + 3))
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        @Binding var selection: String

        init(selection: Binding<String>) { _selection = selection }

        func textViewDidChangeSelection(_ notification: Notification) {
            guard let view = notification.object as? NSTextView,
                  let range = Range(view.selectedRange(), in: view.string) else { return }
            let selected = String(view.string[range]).trimmingCharacters(in: .whitespacesAndNewlines)
            selection = selected
        }
    }
}
