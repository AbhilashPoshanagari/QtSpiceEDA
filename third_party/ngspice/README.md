# ngspice shared library setup

QtSpiceApp talks to ngspice through its **shared library API** (the same
API used by tools like KiCad's ngspice simulation, or ngspice's own
`sharedspice` example). It does NOT shell out to the `ngspice` command-line
binary — everything happens in-process via `NgspiceAdapter`
(`src/simulator/NgspiceAdapter.cpp`), which loads the library dynamically
with `QLibrary` and resolves the handful of functions it needs by name.

## What you need

1. **The shared library itself**, built with `--enable-shared` (‑‑with‑ngshared
   on some distros):
   - Linux: `libngspice.so` (often `libngspice.so.0`)
   - Windows: `ngspice.dll` (+ its import lib if you want static linking)
   - macOS: `libngspice.dylib`

2. *(Optional, only needed if you want compile-time type-checking against
   the real API instead of the bundled `ngspice_api.h` mirror)*:
   `sharedspice.h`, found in the ngspice source tree at
   `src/include/ngspice/sharedspice.h`.

## Where to put things

```
third_party/ngspice/
  include/
    sharedspice.h        <- optional, see above
  lib/
    libngspice.so         <- optional, only needed for static linking
  bin/
    ngspice.dll            <- Windows runtime copy, auto-copied next to the .exe by CMake
```

At **runtime**, the library just needs to be discoverable the normal OS way:

- Linux: on your `LD_LIBRARY_PATH`, or installed to `/usr/lib` /
  `/usr/local/lib` via your package manager (`apt install libngspice0`,
  `pacman -S ngspice`, etc.)
- Windows: `ngspice.dll` next to `QtSpiceApp.exe` (CMake does this
  automatically if you drop the DLL in `third_party/ngspice/bin/`), or
  anywhere on `PATH`.
- macOS: `brew install ngspice`, then make sure `libngspice.dylib` is on
  `DYLD_LIBRARY_PATH` or in a standard Homebrew lib path.

## Building ngspice with shared-library support from source

```bash
git clone https://git.code.sf.net/p/ngspice/ngspice ngspice
cd ngspice
./autogen.sh
mkdir release && cd release
../configure --with-ngshared --enable-xspice --enable-cider
make -j$(nproc)
sudo make install
```

`--with-ngshared` is the flag that produces `libngspice.so` instead of (or
alongside) the standalone `ngspice` binary.

## Verifying it loaded

Run the app; the toolbar shows **"● ngspice ready"** in green once
`Simulator::connectToNgspice()` succeeds at startup, or **"○ ngspice
unavailable"** in red with the failure reason available via
`NgspiceAdapter::lastError()` (surfaced in the status bar / Console tab).
If it fails, it's almost always one of:

- the library isn't on the loader's search path (see above), or
- you built ngspice without `--with-ngshared`, so the exported symbols
  (`ngSpice_Init`, `ngSpice_Command`, `ngSpice_Circ`, `ngGet_Vec_Info`,
  ...) don't exist in the binary.
