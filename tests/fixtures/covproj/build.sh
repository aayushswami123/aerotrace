#!/bin/sh
# The fixture's own build, with coverage. The tests run it through `build` in .aerotrace/tests.toml.
set -e
mkdir -p build
cc --coverage -O0 -Iinclude -c src/lib.c -o build/lib.o
cc --coverage -O2 -Iinclude -c src/prog.c -o build/prog.o
cc --coverage build/lib.o build/prog.o -o build/prog
