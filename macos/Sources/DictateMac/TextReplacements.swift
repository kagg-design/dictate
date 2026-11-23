import AppKit
import SwiftUI

struct TextReplacements {
    struct Rule: Identifiable {
        let source: String
        let replacement: String
        var id: String { source }
    }

    let rules: [Rule]
    private let ordered: [Rule]
    private let pattern: NSRegularExpression?

    init(_ saved: [String: String] = [:]) {
        var unique: [String: Rule] = [:]
        for (key, value) in saved.sorted(by: { $0.key < $1.key }) {
            let source = key.trimmingCharacters(in: .whitespacesAndNewlines)
            let replacement = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if !source.isEmpty, !replacement.isEmpty {
                unique[source.lowercased()] = Rule(source: source, replacement: replacement)
            }
        }
        rules = unique.values.sorted { $0.source.localizedCaseInsensitiveCompare($1.source) == .orderedAscending }
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
        let output = NSMutableString(string: text)
        let bounds = NSRange(text.startIndex..<text.endIndex, in: text)
        for match in pattern.matches(in: text, range: bounds).reversed() {
            if let index = ordered.indices.first(where: { match.range(at: $0 + 1).location != NSNotFound }) {
                output.replaceCharacters(in: match.range, with: ordered[index].replacement)
            }
        }
        return output as String
    }
}

struct ReplacementDraft: Identifiable {
    let id = UUID()
    var source = ""
    var value = ""
    var previous: String?
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
