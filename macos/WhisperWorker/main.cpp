#include "whisper.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace {

constexpr uint32_t error_bit = 1u << 31;
constexpr uint32_t max_samples = 16'000u * 601;

bool reply(const std::string &text, bool error = false) {
    const uint32_t size = static_cast<uint32_t>(text.size()) | (error ? error_bit : 0u);
    return std::fwrite(&size, sizeof(size), 1, stdout) == 1 &&
           std::fwrite(text.data(), 1, text.size(), stdout) == text.size() &&
           std::fflush(stdout) == 0;
}

} // namespace

int main(int argc, char **argv) {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    if (argc != 2) {
        reply("The Whisper worker requires one local model path.", true);
        return 1;
    }

    auto context_params = whisper_context_default_params();
    context_params.use_gpu = true;
    std::unique_ptr<whisper_context, decltype(&whisper_free)> context(
        whisper_init_from_file_with_params(argv[1], context_params), whisper_free);
    if (!context) {
        reply("Whisper could not open the model.", true);
        return 1;
    }
    if (!reply("")) {
        return 1;
    }

    auto params = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
    params.language = "auto";
    params.no_context = true;
    params.print_progress = false;
    params.print_realtime = false;
    params.print_timestamps = false;
    params.n_threads = static_cast<int>(std::clamp(std::thread::hardware_concurrency(), 1u, 6u));

    uint32_t count = 0;
    while (std::fread(&count, sizeof(count), 1, stdin) == 1) {
        if (count == 0 || count > max_samples) {
            reply("The recording is outside the supported length.", true);
            return 1;
        }
        std::vector<float> samples(count);
        if (std::fread(samples.data(), sizeof(float), samples.size(), stdin) != samples.size()) {
            return 1;
        }
        if (whisper_full(context.get(), params, samples.data(), static_cast<int>(samples.size())) != 0) {
            if (!reply("Whisper could not finish decoding the recording.", true)) {
                return 1;
            }
            continue;
        }

        std::string transcript;
        for (int segment = 0; segment < whisper_full_n_segments(context.get()); ++segment) {
            transcript += whisper_full_get_segment_text(context.get(), segment);
        }
        if (!reply(transcript)) {
            return 1;
        }
    }
    return std::feof(stdin) ? 0 : 1;
}
