#pragma once

#include "gallery_roster.h"
#include "gallery_preview_policy.h"

namespace gallery_preview {

struct NativeValue { uint64_t payload; uint32_t auxiliary, type; };
static_assert(sizeof(NativeValue) == 16, "Native expression value");
using Constructor = bool(__fastcall*)(void*, void*);
struct Runtime {
    uintptr_t base = 0;
    Constructor original_constructor = nullptr;
};
inline Runtime& runtime() { static Runtime value; return value; }

// Reproduce 93CBF16B's native evaluation, including failed input cleanup,
// Hero type checking and the native typed-resource writer. Only the final
// resource selection differs, for the exact non-playable hero rules above.
inline bool __fastcall evaluate(void*, void* scope, void* state, void* config,
                               NativeValue* output) {
    const auto base = runtime().base;
    uint64_t hero = 0, resource = 0;
    const auto expression = *reinterpret_cast<uintptr_t*>(static_cast<uint8_t*>(config) + 0x20);
    if (expression) {
        NativeValue value = {0, 0, 2};
        auto evaluator = *reinterpret_cast<void**>(expression + 8);
        auto table = *reinterpret_cast<uintptr_t**>(evaluator);
        using Expression = bool(__fastcall*)(void*, void*, void*, void*, NativeValue*);
        if (reinterpret_cast<Expression>(table[4])(evaluator, scope, state,
                                                  reinterpret_cast<void*>(expression), &value)) {
            using ReadGuid = uint64_t(__fastcall*)(NativeValue*, unsigned);
            hero = reinterpret_cast<ReadGuid>(base + 0xF65540)(&value, 0);
        }
        using Destroy = void(__fastcall*)(NativeValue*);
        reinterpret_cast<Destroy>(base + 0xF63740)(&value);
    }
    bool valid = false;
    if (hero) {
        using Load = void*(__fastcall*)(uint64_t);
        auto definition = static_cast<uint8_t*>(reinterpret_cast<Load>(base + 0x173BA30)(hero));
        if (definition) {
            using IsType = bool(__fastcall*)(void*, const void*);
            auto table = *reinterpret_cast<uintptr_t**>(definition);
            valid = reinterpret_cast<IsType>(table[2])(definition,
                                      reinterpret_cast<const void*>(base + 0x2DA3470));
            if (valid) {
                resource = select_resource(hero, definition[0x1BC] != 0,
                    *reinterpret_cast<uint64_t*>(definition + 0x88),
                    *reinterpret_cast<uint64_t*>(definition + 0x28));
            }
        }
    }
    if (!valid) {
        using Error = void(__fastcall*)(void*);
        reinterpret_cast<Error>(base + 0x1B7FD20)(state);
    }
    using WriteGuid = void(__fastcall*)(NativeValue*, uint64_t);
    reinterpret_cast<WriteGuid>(base + 0xF67090)(output, resource);
    return true;
}

inline bool __fastcall construct(void* builder, void* definition) {
    auto& host = runtime();
    if (!host.original_constructor(builder, definition)) return false;
    const auto resource = *reinterpret_cast<uint64_t*>(static_cast<uint8_t*>(builder) + 0x2080);
    if (!needs_display_filter(resource)) return true;
    const auto absent = *reinterpret_cast<uint8_t*>(host.base + 0x323FFB0);
    return retain_display_components(static_cast<DisplayBuilder*>(builder), absent);
}

constexpr uintptr_t expression_rva = 0x9491A0, constructor_rva = 0x64EC30;
constexpr size_t hook_code_size = 64;
inline void absolute_jump(uint8_t* code, uintptr_t target) {
    const uint8_t prefix[] = {0x48, 0xB8};
    std::memcpy(code, prefix, sizeof(prefix));
    std::memcpy(code + 2, &target, 8);
    code[10] = 0xFF; code[11] = 0xE0;
}

template <size_t Size>
inline bool replace_entry(uintptr_t address, uintptr_t destination,
                          const uint8_t (&original)[Size], DWORD& error) {
    static_assert(Size >= 8, "Atomic entry patch");
    if (address % 8) { error = ERROR_INVALID_ADDRESS; return false; }
    uint8_t bytes[] = {0xE9, 0, 0, 0, 0, 0x90, 0x90, 0x90};
    gallery_roster::rel32(bytes + 1, address + 1, destination);
    LONG64 expected, replacement;
    std::memcpy(&expected, original, 8);
    std::memcpy(&replacement, bytes, 8);
    DWORD previous;
    if (!VirtualProtect(reinterpret_cast<void*>(address), 8, PAGE_EXECUTE_READWRITE, &previous)) {
        error = GetLastError(); return false;
    }
    const bool applied = InterlockedCompareExchange64(reinterpret_cast<volatile LONG64*>(address),
                                                      replacement, expected) == expected;
    FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(address), 8);
    DWORD unused;
    VirtualProtect(reinterpret_cast<void*>(address), 8, previous, &unused);
    error = applied ? ERROR_SUCCESS : ERROR_RETRY;
    return applied;
}

// Install the display filter before enabling resource substitution. If the
// second entry changes concurrently, keep the first hook's executable storage
// alive; its constructor trampoline remains a valid native fallback.
inline gallery_roster::InstallResult install(uintptr_t base, uintptr_t end) {
    using gallery_roster::Install;
    if (end < base || end - base < 0x323FFB1)
        return {Install::failed, "preview image bounds", ERROR_BAD_EXE_FORMAT};
    constexpr uint8_t expression[] = {0x48,0x89,0x5C,0x24,0x08,0x57,0x48,0x83,0xEC,0x40};
    constexpr uint8_t constructor[] = {0x40,0x53,0x48,0x83,0xEC,0x60,0x48,0x8B,0xDA,0xE8,0x12,0x4B,0x18,0x01};
    constexpr uint8_t read_guid[] = {0x48,0x83,0xEC,0x28,0x44,0x8B,0x41,0x0C};
    constexpr uint8_t destroy[] = {0x40,0x53,0x48,0x83,0xEC,0x20,0x8B,0x41};
    constexpr uint8_t write_guid[] = {0x48,0x89,0x5C,0x24,0x08,0x57,0x48,0x83};
    constexpr uint8_t load[] = {0x48,0x83,0xEC,0x28,0x4C,0x8B,0xC1,0x48};
    constexpr uint8_t error_code[] = {0x48,0x85,0xC9,0x0F,0x85,0xA7,0x2D,0xFF};
    if (!gallery_roster::matches(base + expression_rva, expression) ||
        !gallery_roster::matches(base + constructor_rva, constructor) ||
        !gallery_roster::matches(base + 0xF65540, read_guid) ||
        !gallery_roster::matches(base + 0xF63740, destroy) ||
        !gallery_roster::matches(base + 0xF67090, write_guid) ||
        !gallery_roster::matches(base + 0x173BA30, load) ||
        !gallery_roster::matches(base + 0x1B7FD20, error_code))
        return {Install::waiting, "preview decrypted signatures", ERROR_SUCCESS};
    DWORD error;
    static_assert(hook_code_size <= gallery_roster::code_size, "Near allocation capacity");
    auto code = gallery_roster::allocate_code(base, end, error);
    if (!code) return {Install::failed, "preview allocation", error};
    absolute_jump(code, reinterpret_cast<uintptr_t>(&evaluate));
    absolute_jump(code + 16, reinterpret_cast<uintptr_t>(&construct));
    std::memcpy(code + 32, constructor, sizeof(constructor));
    gallery_roster::rel32(code + 42, reinterpret_cast<uintptr_t>(code + 42), base + 0x17D3750);
    code[46] = 0xE9;
    gallery_roster::rel32(code + 47, reinterpret_cast<uintptr_t>(code + 47), base + 0x64EC3E);
    DWORD previous;
    if (!VirtualProtect(code, hook_code_size, PAGE_EXECUTE_READ, &previous) ||
        !FlushInstructionCache(GetCurrentProcess(), code, hook_code_size)) {
        error = GetLastError(); VirtualFree(code, 0, MEM_RELEASE);
        return {Install::failed, "preview code protection", error};
    }
    runtime().base = base;
    runtime().original_constructor = reinterpret_cast<Constructor>(code + 32);
    if (!replace_entry(base + constructor_rva, reinterpret_cast<uintptr_t>(code + 16), constructor, error)) {
        VirtualFree(code, 0, MEM_RELEASE);
        return {Install::failed, "preview constructor exchange", error};
    }
    if (!replace_entry(base + expression_rva, reinterpret_cast<uintptr_t>(code), expression, error))
        return {Install::failed, "preview expression exchange", error};
    return {Install::installed, "preview complete", ERROR_SUCCESS};
}

} // namespace gallery_preview
