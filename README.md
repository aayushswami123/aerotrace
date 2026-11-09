# AeroTrace

A safety layer for AI coding agents working on critical, legacy C and C++ code.
Before an agent edits code, AeroTrace shows what the change will affect. It runs fully on your machine.

Status: early development.

## Try it

```bash
uv tool install git+https://github.com/aayushswami123/aerotrace   # or: pipx install ...
cd your-c-project
aerotrace doctor     # checks libclang and finds compile_commands.json
aerotrace index      # builds .aerotrace/index.db
aerotrace status     # what was indexed, and what changed since
```

For accurate results, give AeroTrace your build's `compile_commands.json`
(CMake: `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`; Make: run the build under [`bear`](https://github.com/rizsotto/Bear)).

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
