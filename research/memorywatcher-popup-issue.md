# MemoryWatcher "Unable to resolve read address" Popup Issue

## The Problem

Project Rio shows a popup: `"Unable to resolve read address 803c77b8 PC 588"` with "Ignore for Session" / "OK" buttons. This happens:
- When the overlay is NOT running (Locations.txt persists on disk)
- When the overlay IS running, during early boot before the game initializes its memory map

## Why M'Overlay Doesn't Have This Problem

M'Overlay does **not** use Dolphin's MemoryWatcher. It reads Dolphin's process memory directly via OS-level APIs:

- **Windows**: `ReadProcessMemory` on a 32MB `MEM_MAPPED` region found via `VirtualQueryEx`
- **Linux**: `process_vm_readv` on `/dev/shm/dolphinmem` shared memory
- **macOS**: Stubs that return `false` — macOS is non-functional in M'Overlay

Because M'Overlay reads from the host-side physical backing of emulated RAM (a simple offset calculation like `host_base + 0x003C77B8`), it bypasses the emulated PowerPC MMU entirely. If the read fails, it gets a simple `false` return — no popup, no panic.

## Root Cause in Dolphin/Project Rio

The error originates in `Source/Core/Core/PowerPC/MMU.cpp` inside `ReadFromHardware`:

```cpp
PanicAlertFmt("Unable to resolve read address {:x} PC {:x}", em_address, m_ppc_state.pc);
```

This is the **final fallback** when an address doesn't match any valid emulated memory region (RAM, EXRAM, L1 cache, FakeVMEM, MMIO, or EFB).

### The Call Chain

1. `Core::OnFrameEnd()` calls `s_memory_watcher->Step(guard)` **every frame**
2. `Step()` → `ComposeMessages()` iterates all addresses from `Locations.txt`
3. `ComposeMessages()` → `ChasePointer()` for each address
4. `ChasePointer()` calls `PowerPC::MMU::HostRead<u32>(guard, value + offset)`
5. `HostRead<u32>` calls `ReadFromHardware<NoException, u32>(address)` — **no pre-validation**

### The Bug

`ChasePointer()` in `MemoryWatcher.cpp`:

```cpp
u32 MemoryWatcher::ChasePointer(const Core::CPUThreadGuard& guard, const std::string& line)
{
  u32 value = 0;
  for (u32 offset : m_addresses[line])
  {
    value = PowerPC::MMU::HostRead<u32>(guard, value + offset);
    if (!PowerPC::MMU::HostIsRAMAddress(guard, value))
      break;
  }
  return value;
}
```

- The `HostIsRAMAddress` check validates the **result** (for pointer chains), not the **input address**
- There is no pre-check before calling `HostRead` on the input address
- `HostRead` (not `HostTryRead`) is used, which does NOT pre-validate and hits the PanicAlert
- Compare: `HostTryRead` checks `HostIsRAMAddress` first and returns gracefully on failure

### When the Error Fires

The watched address `0x803C77B8` is a virtual address requiring PowerPC MMU translation:
- **Works**: When MSR.DR=1 (data translation enabled) and the game's TLB/BAT maps `0x80000000–0x81800000` to physical RAM — normal gameplay state
- **Fails**: During early boot (before memory map is initialized), game transitions, loading screens, or when watching addresses for the wrong game

## Status in Dolphin and Project Rio (as of Feb 2026)

**Neither Dolphin master nor Project Rio have fixed this.** Both have the identical vulnerable `ChasePointer()` code.

### Relevant Commit History

| Date | Hash | Author | Change |
|------|------|--------|--------|
| Dec 2015 | `525fc4f` | spxtr | Original `ChasePointer()` — no validation at all |
| Aug 2020 | `ff16846` | stblr | **"Do not follow invalid pointers"** — added post-read `HostIsRAMAddress` check on the result, but NOT a pre-read check on the input address |
| Feb 2021 | `22b3003` | AdmiralCurtiss | Switched to `HostRead_U32` (correct MMU function) |
| Oct 2025 | `8a97ce9` | SuperSamus | Switched to templated `HostRead<u32>` |

The 2020 commit (`ff16846`) looks like it addressed the problem but only partially did — it prevents chasing bad pointer chains but doesn't prevent the initial read from panicking.

### Why It Hasn't Been Fixed

- MemoryWatcher is a niche feature (most overlays on Windows/Linux use direct process memory instead)
- The 2020 partial fix made it seem resolved
- No GitHub issue or PR has been filed about MemoryWatcher triggering the PanicAlert specifically
- No dedicated discussions about it in the Dolphin repo
- macOS is the platform where MemoryWatcher matters most (only viable approach), but macOS is the least-used Dolphin platform

### Direct Process Memory Reading (M'Overlay's approach) Not Viable on macOS

On macOS, reading Dolphin's process memory directly requires:
1. Dolphin re-signed with `com.apple.security.get-task-allow` entitlement (re-done after every update)
2. The reading process needs root or the same entitlement
3. Mach APIs: `task_for_pid()` → `mach_vm_region()` → `vm_read_overwrite()`

This is why M'Overlay's macOS module is non-functional (stubs returning `false`), and why MemoryWatcher is the only practical approach on macOS.

## Solutions

### Fix 1: Clear Locations.txt on overlay shutdown (DONE)

Prevents the error when the overlay is NOT running. Already implemented in `dolphin_adapter.py` `stop()` → `_clear_locations_file()`.

### Fix 2: Patch Project Rio's MemoryWatcher.cpp (RECOMMENDED)

Add an `HostIsRAMAddress` pre-check in `ChasePointer()` before calling `HostRead`:

```cpp
u32 MemoryWatcher::ChasePointer(const Core::CPUThreadGuard& guard, const std::string& line)
{
  u32 value = 0;
  for (u32 offset : m_addresses[line])
  {
    u32 addr = value + offset;
    if (!PowerPC::MMU::HostIsRAMAddress(guard, addr))
      break;
    value = PowerPC::MMU::HostRead<u32>(guard, addr);
    if (!PowerPC::MMU::HostIsRAMAddress(guard, value))
      break;
  }
  return value;
}
```

This would silently skip unresolvable addresses instead of triggering a PanicAlert. Could be submitted as a PR to [ProjectRio/ProjectRio](https://github.com/ProjectRio/ProjectRio).

### Fix 3: Delayed Locations.txt write

Only write `Locations.txt` after detecting the game has booted (e.g., after receiving first valid data on the socket). Downside: no reliable external signal for game boot, and MemoryWatcher re-reads `Locations.txt` only at startup — so the file must exist before Dolphin launches.

## Recommendation

Fix 2 (patching Project Rio) is the proper solution. The missing pre-check is arguably a bug in Dolphin's MemoryWatcher — `HostTryRead` exists specifically for safe reads, or at minimum `HostIsRAMAddress` should guard the call. This is a one-line change in Project Rio's fork.

Fix 1 (already implemented) handles the "overlay not running" case as a bonus.
