# Cross-Compiling Rust for Raspberry Pi Zero W (ARMv6)

This document summarizes the lessons learned while building a Rust application for **Raspberry Pi Zero W** (ARMv6 architecture) on a modern Mac/Linux host.

## 1. Hardware Architecture Constraints
The Raspberry Pi Zero, Zero W, and Pi 1 use the **ARM1176JZF-S** processor, which is **ARMv6**.
*   **The Problem:** Most "ARM" tutorials and default targets (like `armv7-unknown-linux-gnueabihf`) target **ARMv7** or higher.
*   **Result:** Running an ARMv7 binary on ARMv6 hardware results in an `Illegal instruction` error because the binary contains instructions (like `IDIV` or certain Thumb-2 features) that the ARMv6 CPU does not understand.

## 2. Choosing the Right Target
*   **Target:** `arm-unknown-linux-gnueabihf`
    *   `arm`: Basic 32-bit ARM.
    *   `unknown-linux`: Target OS.
    *   `gnueabihf`: GNU C library with **Hard Float** (required for Raspbian/Raspberry Pi OS).
*   **Avoid:** `arm-unknown-linux-gnueabi` (Soft Float). While it works, it often results in `-bash: ./binary: cannot execute: required file not found` because the system's dynamic linker is looking for `ld-linux-armhf.so.3` but the binary is looking for `ld-linux.so.3`.

## 3. The Secret Ingredient: Target CPU Flags
Even with the correct target, the compiler may still emit ARMv7 instructions. You **must** explicitly tell the compiler to target the ARMv6 CPU.

**Command:**
```bash
RUSTFLAGS="-C target-cpu=arm1176jzf-s" cargo zigbuild --target arm-unknown-linux-gnueabihf.2.17 --release
```
*Alternatively with `cross`:*
```bash
RUSTFLAGS="-C target-cpu=arm1176jzf-s" cross build --target arm-unknown-linux-gnueabihf --release
```

## 4. Handling Critical Dependencies
Some crates are notorious for failing on ARMv6 due to optimized assembly.

### TLS / HTTPS (`reqwest`)
*   **Issue:** The `rustls` crate depends on `ring`, which contains ARMv7-specific assembly that causes `Illegal instruction` on Pi Zero.
*   **Solution:** Use `native-tls` with the `vendored` feature. This compiles OpenSSL from source specifically for the target CPU.
    ```toml
    [dependencies]
    reqwest = { version = "0.12", features = ["native-tls", "json", "blocking"], default-features = false }
    openssl = { version = "0.10", features = ["vendored"] }
    ```

### D-Bus / Bluetooth (`btleplug`)
*   **Issue:** Linking against system `libdbus` during cross-compilation is difficult and requires complex sysroots.
*   **Solution:** Use the `vendored` feature for the `dbus` crate.
    ```toml
    [dependencies]
    dbus = { version = "0.9", features = ["vendored"] }
    ```

## 5. Verification Tools
If a binary fails, use these commands on the Raspberry Pi to diagnose:
1.  Check architecture: `uname -m` (Should be `armv6l`).
2.  Check binary details: `file ./your-binary`
    *   Look for `BuildID`. If it stays the same after a rebuild, you are running a cached/old binary.
3.  Check dependencies: `ldd ./your-binary`
    *   If it says `not a dynamic executable` or `not found`, you have an ABI mismatch (Soft vs Hard float).

## 6. Recommended Workflow
1.  **Clean often:** `cargo clean` ensures no ARMv7 artifacts are cached.
2.  **Use Zig or Cross:** `cargo-zigbuild` is highly recommended for its ease of handling GLIBC versions and C cross-compilation.
3.  **Unique Filenames:** When testing multiple builds, rename the binary (e.g., `door-monitor-v6`) to ensure you are actually running the newest version on the device.
