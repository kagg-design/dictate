// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "DictateMac",
    platforms: [.macOS(.v15)],
    dependencies: [
        .package(url: "https://github.com/altic-dev/FluidAudio.git",
                 revision: "386f7195670b77f2954c7ee143ec63fcb50b6c3b")
    ],
    targets: [
        .executableTarget(
            name: "DictateMac",
            dependencies: [.product(name: "FluidAudio", package: "FluidAudio")]
        ),
        .testTarget(name: "DictateMacTests", dependencies: ["DictateMac"])
    ],
    swiftLanguageModes: [.v5]
)
