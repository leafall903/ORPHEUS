#pragma once

#include <windows.h>
#include <cstdint>
#include <cstring>

namespace gallery_roster {

constexpr uintptr_t branch_rva = 0x7F11F0;
constexpr uintptr_t grid_call_rva = 0xA7E1C0;
constexpr uintptr_t rows_call_rva = 0xA7EDA2;
constexpr uint64_t hero_type = 0x02E0000000000000;
constexpr uint16_t npc_indices[] = {
    0x16B, 0x16C, 0x16D, 0x16E, 0x16F, 0x170, 0x171, 0x172,
    0x173, 0x175, 0x176, 0x177, 0x178, 0x179, 0x17C, 0x1AC,
    0x1B8, 0x1BA, 0x1BB, 0x1C7, 0x1CE, 0x1F9, 0x21D,
};

// The native compare at 7F11E9 sets the flags. At the patched branch the
// return address is [rsp+28h], and RBX is the offset in the source roster.
constexpr uint8_t instructions[] = {
    0x0F, 0x85, 0, 0, 0, 0,        // jne accept (already playable)
    0x4C, 0x8B, 0x5C, 0x24, 0x28,  // mov r11, [rsp+28h]
    0x4C, 0x8D, 0x15, 0, 0, 0, 0,  // lea r10, [rip+grid_return]
    0x4D, 0x39, 0xD3,              // cmp r11, r10
    0x0F, 0x84, 0, 0, 0, 0,        // je lookup
    0x4C, 0x8D, 0x15, 0, 0, 0, 0,  // lea r10, [rip+rows_return]
    0x4D, 0x39, 0xD3,              // cmp r11, r10
    0x0F, 0x85, 0, 0, 0, 0,        // jne skip
    0x4C, 0x8B, 0x15, 0, 0, 0, 0,  // lookup: mov r10, [rip+roster]
    0x4D, 0x8B, 0x14, 0x1A,        // mov r10, [r10+rbx]
    0x48, 0x8D, 0x0D, 0, 0, 0, 0,  // lea rcx, [rip+allowlist]
    0x41, 0xBB, 23, 0, 0, 0,       // mov r11d, allowlist length
    0x4C, 0x39, 0x11,              // next: cmp [rcx], r10
    0x0F, 0x84, 0, 0, 0, 0,        // je accept (allowlisted)
    0x48, 0x83, 0xC1, 0x08,        // add rcx, 8
    0x41, 0xFF, 0xCB,              // dec r11d
    0x0F, 0x85, 0, 0, 0, 0,        // jne next
    0xE9, 0, 0, 0, 0,              // jmp skip
};
static_assert(sizeof(instructions) == 94, "check branch fixups when editing the stub");
static_assert(sizeof(npc_indices) / sizeof(npc_indices[0]) == 23, "keep the stub's count in sync");
constexpr size_t code_size = sizeof(instructions) + 23 * sizeof(uint64_t);

inline void rel32(uint8_t* output, uintptr_t origin, uintptr_t target) {
    const auto displacement = static_cast<int32_t>(static_cast<int64_t>(target) -
                                                   static_cast<int64_t>(origin + 4));
    std::memcpy(output, &displacement, sizeof(displacement));
}

inline void write_code(uint8_t* output, uintptr_t base, uintptr_t code_address) {
    std::memcpy(output, instructions, sizeof(instructions));
    struct Fixup { size_t offset; uintptr_t target; };
    const Fixup fixups[] = {
        {2, base + 0x7F11F6}, {14, base + grid_call_rva + 5},
        {23, code_address + 43}, {30, base + rows_call_rva + 5},
        {39, base + 0x7F1272}, {46, base + 0x2F5E4A8},
        {57, code_address + sizeof(instructions)}, {72, base + 0x7F11F6},
        {85, code_address + 67}, {90, base + 0x7F1272},
    };
    for (const auto& fixup : fixups) {
        rel32(output + fixup.offset, code_address + fixup.offset, fixup.target);
    }
    size_t offset = sizeof(instructions);
    for (const auto index : npc_indices) {
        const uint64_t guid = hero_type | index;
        std::memcpy(output + offset, &guid, sizeof(guid));
        offset += sizeof(guid);
    }
}

inline uint8_t* allocate_code(uintptr_t base, uintptr_t end, DWORD& error) {
    error = ERROR_NOT_ENOUGH_MEMORY;
    SYSTEM_INFO info = {};
    GetSystemInfo(&info);
    const uintptr_t mask = info.dwAllocationGranularity - 1;
    for (uintptr_t address = (end + mask) & ~mask; address < base + 0x70000000;) {
        MEMORY_BASIC_INFORMATION region = {};
        if (!VirtualQuery(reinterpret_cast<void*>(address), &region, sizeof(region))) {
            error = GetLastError();
            break;
        }
        const auto next = reinterpret_cast<uintptr_t>(region.BaseAddress) + region.RegionSize;
        if (region.State == MEM_FREE && next >= address + code_size) {
            auto code = VirtualAlloc(reinterpret_cast<void*>(address), code_size,
                                     MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
            if (code) {
                return static_cast<uint8_t*>(code);
            }
            error = GetLastError();
        }
        address = (next + mask) & ~mask;
    }
    return nullptr;
}

enum class Install { waiting, installed, failed };

struct InstallResult {
    Install state;
    const char* stage;
    DWORD error;
};

template <size_t Size>
bool matches(uintptr_t address, const uint8_t (&expected)[Size]) {
    uint8_t actual[Size];
    SIZE_T read = 0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(address),
                             actual, Size, &read) && read == Size &&
           std::memcmp(actual, expected, Size) == 0;
}

inline InstallResult install(uintptr_t base, uintptr_t end) {
    if (end < base || end - base < 0x2F5E4B4) {
        return {Install::failed, "image bounds", ERROR_BAD_EXE_FORMAT};
    }
    constexpr uint8_t original[] = {0x0F, 0x84, 0x7C, 0, 0, 0, 0x48, 0x8B};
    constexpr uint8_t compare[] = {0x80, 0xB8, 0xBC, 0x01, 0, 0, 0};
    constexpr uint8_t grid_call[] = {0xE8, 0xCB, 0x2F, 0xD7, 0xFF};
    constexpr uint8_t rows_call[] = {0xE8, 0xE9, 0x23, 0xD7, 0xFF};
    const auto address = base + branch_rva;
    if (!matches(address - sizeof(compare), compare) || !matches(address, original) ||
        !matches(base + grid_call_rva, grid_call) || !matches(base + rows_call_rva, rows_call)) {
        // These pages are encrypted in the on-disk executable.
        return {Install::waiting, "decrypted signatures", ERROR_SUCCESS};
    }
    auto branch = reinterpret_cast<uint8_t*>(address);
    DWORD allocation_error = ERROR_SUCCESS;
    auto code = allocate_code(base, end, allocation_error);
    if (!code) {
        return {Install::failed, "near allocation", allocation_error};
    }
    write_code(code, base, reinterpret_cast<uintptr_t>(code));
    DWORD previous = 0;
    const char* stage = "code protection";
    bool prepared = VirtualProtect(code, code_size, PAGE_EXECUTE_READ, &previous) != FALSE;
    if (prepared) {
        stage = "code cache flush";
        prepared = FlushInstructionCache(GetCurrentProcess(), code, code_size) != FALSE;
    }
    if (prepared) {
        stage = "branch protection";
        prepared = VirtualProtect(branch, sizeof(original), PAGE_EXECUTE_READWRITE, &previous) != FALSE;
    }
    if (!prepared) {
        const DWORD error = GetLastError();
        VirtualFree(code, 0, MEM_RELEASE);
        return {Install::failed, stage, error};
    }
    uint8_t patch[] = {0xE9, 0, 0, 0, 0, 0x90, original[6], original[7]};
    rel32(patch + 1, reinterpret_cast<uintptr_t>(branch + 1), reinterpret_cast<uintptr_t>(code));
    LONG64 expected, replacement;
    std::memcpy(&expected, original, sizeof(expected));
    std::memcpy(&replacement, patch, sizeof(replacement));
    const auto replaced = InterlockedCompareExchange64(reinterpret_cast<volatile LONG64*>(branch),
                                                        replacement, expected) == expected;
    FlushInstructionCache(GetCurrentProcess(), branch, sizeof(patch));
    DWORD unused = 0;
    VirtualProtect(branch, sizeof(original), previous, &unused);
    if (!replaced) {
        VirtualFree(code, 0, MEM_RELEASE);
        return {Install::failed, "branch changed before exchange", ERROR_RETRY};
    }
    return {Install::installed, "complete", ERROR_SUCCESS};
}

}  // namespace gallery_roster
