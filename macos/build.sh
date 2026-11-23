#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    echo "Build Dictate on an Apple Silicon Mac." >&2
    exit 1
fi
for tool in swift xcrun cmake git codesign hdiutil iconutil sips; do
    command -v "$tool" >/dev/null
done
sdk="$(xcrun --sdk macosx --show-sdk-version)"
if [[ "${sdk%%.*}" -lt 26 ]]; then
    echo "Select Xcode 26 or newer to build FluidAudio." >&2
    exit 1
fi

build="$root/artifacts/macos-build"
out="$root/artifacts/macos"
whisper="$build/whisper.cpp"
whisper_tag=v1.9.5
mkdir -p "$build" "$out"
if [[ ! -d "$whisper/.git" ]]; then
    git clone --depth 1 --branch "$whisper_tag" https://github.com/ggml-org/whisper.cpp.git "$whisper"
fi
if [[ "$(git -C "$whisper" describe --exact-match --tags HEAD)" != "$whisper_tag" ]]; then
    echo "Expected whisper.cpp $whisper_tag at $whisper." >&2
    exit 1
fi
cmake -S "$root/macos/WhisperWorker" -B "$build/whisper-worker" \
    -DWHISPER_SOURCE_DIR="$whisper" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=15.0 -DCMAKE_OSX_ARCHITECTURES=arm64
cmake --build "$build/whisper-worker" --config Release --target DictateWhisper --parallel 3

swift test --package-path "$root/macos" --jobs 3
swift build --package-path "$root/macos" --configuration release --jobs 3
swift_products="$(swift build --package-path "$root/macos" --configuration release --show-bin-path)"

staging="$(mktemp -d "$build/package.XXXXXX")"
trap 'rm -rf -- "$staging"' EXIT
volume="$staging/volume"
app="$volume/Dictate.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
install -m 755 "$swift_products/DictateMac" "$app/Contents/MacOS/DictateMac"
install -m 755 "$build/whisper-worker/DictateWhisper" "$app/Contents/MacOS/DictateWhisper"
install -m 644 "$root/macos/Dictate.Info.plist" "$app/Contents/Info.plist"
install -m 644 "$whisper/LICENSE" "$app/Contents/Resources/whisper-cpp-LICENSE.txt"
if [[ -f "$root/macos/.build/checkouts/FluidAudio/LICENSE" ]]; then
    install -m 644 "$root/macos/.build/checkouts/FluidAudio/LICENSE" "$app/Contents/Resources/FluidAudio-LICENSE.txt"
elif [[ -f "$root/macos/.build/checkouts/fluidaudio/LICENSE" ]]; then
    install -m 644 "$root/macos/.build/checkouts/fluidaudio/LICENSE" "$app/Contents/Resources/FluidAudio-LICENSE.txt"
else
    echo "The FluidAudio license is missing from the build." >&2
    exit 1
fi
printf '%s\n' 'Fluid Speech Mini by FluidVoice — https://fluidvoice.com/labs/fluid-speech-mini' \
    > "$app/Contents/Resources/Model-credits.txt"
for bundle in "$swift_products/"*.bundle; do
    [[ -d "$bundle" ]] || continue
    ditto "$bundle" "$app/Contents/Resources/$(basename "$bundle")"
done

icons="$staging/Dictate.iconset"
mkdir "$icons"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$root/icon.png" --out "$icons/icon_${size}x${size}.png" >/dev/null
    sips -z "$((size * 2))" "$((size * 2))" "$root/icon.png" --out "$icons/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns -o "$app/Contents/Resources/Dictate.icns" "$icons"
ln -s /Applications "$volume/Applications"

codesign --force --sign - "$app/Contents/MacOS/DictateWhisper"
codesign --force --sign - "$app"
codesign --verify --deep --strict "$app"

if [[ -n "${DICTATE_SMOKE_AUDIO:-}" ]]; then
    "$app/Contents/MacOS/DictateMac" --check-models "$DICTATE_SMOKE_AUDIO" "${DICTATE_SMOKE_EXPECTED:-}"
fi
hdiutil create -quiet -ov -format UDZO -volname "Dictate" -srcfolder "$volume" \
    "$staging/Dictate-macOS-arm64-beta.dmg"
hdiutil verify "$staging/Dictate-macOS-arm64-beta.dmg"
mv "$staging/Dictate-macOS-arm64-beta.dmg" "$out/"
(cd "$out" && shasum -a 256 Dictate-macOS-arm64-beta.dmg > Dictate-macOS-arm64-beta.dmg.sha256)
