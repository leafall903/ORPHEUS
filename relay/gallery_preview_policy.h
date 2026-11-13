#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace gallery_preview {

struct ResourceRule { uint64_t hero, preview, display; };
// Only the non-playable heroes whose authored preview model differs from their body.
constexpr ResourceRule resource_rules[] = {
    {0x02E000000000016BULL, 0x0400000000000C5EULL, 0x0400000000001403ULL},
    {0x02E000000000016CULL, 0x0400000000000C5EULL, 0x040000000000143EULL},
    {0x02E000000000016DULL, 0x0400000000000C5EULL, 0x040000000000146EULL},
    {0x02E000000000016EULL, 0x0400000000000C5EULL, 0x04000000000014EDULL},
    {0x02E000000000016FULL, 0x0400000000000C5EULL, 0x04000000000014A9ULL},
    {0x02E0000000000170ULL, 0x0400000000000C5EULL, 0x04000000000014D2ULL},
    {0x02E0000000000171ULL, 0x0400000000000C5EULL, 0x04000000000014D2ULL},
    {0x02E0000000000172ULL, 0x0400000000000C5EULL, 0x04000000000014D2ULL},
    {0x02E0000000000175ULL, 0x040000000000021BULL, 0x04000000000016BEULL},
    {0x02E0000000000176ULL, 0x040000000000021BULL, 0x04000000000016D6ULL},
    {0x02E0000000000177ULL, 0x040000000000021BULL, 0x040000000000171CULL},
    {0x02E00000000001C7ULL, 0x0400000000001AD1ULL, 0x0400000000001FDAULL},
    {0x02E00000000001F9ULL, 0x0400000000001AD1ULL, 0x04000000000022D7ULL},
    {0x02E000000000021DULL, 0x0400000000002474ULL, 0x04000000000025C3ULL},
};

inline uint64_t select_resource(uint64_t hero, bool playable, uint64_t preview,
                                uint64_t gameplay) {
    if (!playable) {
        for (const auto& rule : resource_rules) {
            if (hero == rule.hero && preview == rule.preview && gameplay == rule.display)
                return rule.display;
        }
    }
    return preview;
}

inline bool needs_display_filter(uint64_t resource) {
    for (const auto& rule : resource_rules)
        if (rule.display == resource) return true;
    return false;
}

// 104319's native component index table F525E0, not guessed entity flags.
constexpr uint8_t model_component = 4;
constexpr uint8_t local_idle_component = 42;
struct BuilderEntry {
    uint8_t index;
    uint8_t padding[7];
    uintptr_t definition;
    uintptr_t extra[2];
};
struct DisplayBuilder {
    uintptr_t vtable;
    BuilderEntry entries[256];
    uint32_t component_mask[8];
    uint32_t cursor, padding;
    DisplayBuilder* first_child;
    DisplayBuilder* next_sibling;
};
static_assert(sizeof(uintptr_t) == 8, "The relay targets the 104319 x64 client");
static_assert(sizeof(BuilderEntry) == 0x20, "Native builder entry stride");
static_assert(offsetof(DisplayBuilder, component_mask) == 0x2008, "Native component mask");
static_assert(offsetof(DisplayBuilder, first_child) == 0x2030, "Native first-child getter");
static_assert(offsetof(DisplayBuilder, next_sibling) == 0x2038, "Native next-sibling getter");

// Validate the entire owned child tree before changing any entries. The native
// STU definitions and component configurations remain shared, read-only inputs.
inline bool retain_display_components(DisplayBuilder* root, uint8_t absent_index) {
    if (!root) return false;
    DisplayBuilder* nodes[64] = {root};
    size_t count = 1;
    for (size_t current = 0; current < count; ++current) {
        for (auto child = nodes[current]->first_child; child; child = child->next_sibling) {
            if (count == 64) return false;
            for (size_t i = 0; i < count; ++i) if (nodes[i] == child) return false;
            nodes[count++] = child;
        }
    }
    for (size_t current = 0; current < count; ++current) {
        auto& builder = *nodes[current];
        std::memset(builder.component_mask, 0, sizeof(builder.component_mask));
        for (size_t i = 0; i < 256; ++i) {
            auto& entry = builder.entries[i];
            if ((i == model_component || i == local_idle_component) && entry.index == i) {
                builder.component_mask[i / 32] |= uint32_t(1) << (i % 32);
            } else {
                std::memset(&entry, 0, sizeof(entry));
                entry.index = absent_index;
            }
        }
    }
    return true;
}

} // namespace gallery_preview
