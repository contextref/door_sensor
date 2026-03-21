# Door Monitor

A Rust-based BLE door sensor monitor designed for Raspberry Pi Zero W (ARMv6).

## Prerequisites

To cross-compile this project from macOS or Linux to a Raspberry Pi Zero W, you need:

1.  **Rust**: [rustup.rs](https://rustup.rs/)
2.  **Zig**: `brew install zig` (on macOS)
3.  **cargo-zigbuild**: `cargo install cargo-zigbuild`
4.  **Target architecture**:
    ```bash
    rustup target add arm-unknown-linux-gnueabihf
    ```

## Building for Raspberry Pi Zero W (ARMv6)

The Raspberry Pi Zero W uses an older ARMv6 processor. To prevent "Illegal instruction" errors, you must explicitly target its CPU architecture.

Run the following command on your development machine:

```bash
RUSTFLAGS="-C target-cpu=arm1176jzf-s" cargo zigbuild --target arm-unknown-linux-gnueabihf.2.17 --release
```

### Why these flags?
*   **`target-cpu=arm1176jzf-s`**: Tells the compiler to only use ARMv6 instructions.
*   **`.2.17`**: Specifies a GLIBC version compatible with older Raspberry Pi OS installs.

## Deployment

1.  **Transfer the binary and configuration**:
    ```bash
    # Replace with your actual Pi's IP/hostname
    scp target/arm-unknown-linux-gnueabihf/release/door-monitor ilya@myrasperrypie:~/
    scp .env ilya@myrasperrypie:~/
    ```

2.  **Run on the Pi**:
    Bluetooth access usually requires root permissions.
    ```bash
    ssh ilya@myrasperrypie
    sudo ./door-monitor
    ```

## Development & Testing

You can run tests locally on your Mac:

```bash
cargo test
```

The `Cargo.toml` is configured to use `rustls-tls` on macOS and `native-tls` (vendored OpenSSL) on Linux to ensure both platforms build and test correctly without dependency conflicts.

## Project Structure
*   `src/main.rs`: Core monitor logic.
*   `CROSS_COMPILING_ARMV6.md`: Detailed technical notes on ARMv6 cross-compilation.
*   `Cargo.toml`: Target-specific dependency management.
