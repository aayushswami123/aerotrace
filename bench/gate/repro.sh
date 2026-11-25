#!/bin/bash
AT=${AT:-aerotrace}; rm -rf work; mkdir work; cd work; TOP=$PWD
mk() { cd $TOP/$1; python3 -c 'import json,glob,os;d=os.getcwd();json.dump([{"directory":d,"file":f,"arguments":["cc","-c",f]} for f in sorted(glob.glob("*.c"))],open("compile_commands.json","w"))'
  git init -q . && git add -A && git -c user.email=a@b -c user.name=a commit -qm base
  cc *.c -o /tmp/repro_$$.out && echo "[$1] baseline builds"; rm -f /tmp/repro_$$.out; $AT index >/dev/null 2>&1; }
build() { for f in *.c; do cc -ferror-limit=0 -c $f -o /tmp/repro_$$_$f.o 2>&1 | grep -E "error:"; done
  cc /tmp/repro_$$_*.o -o /tmp/repro_$$.out 2>&1 | grep -E "Undefined|duplicate symbol|referenced from"; rm -f /tmp/repro_$$*; }
ask() { "$@" > /tmp/repro_$$.txt 2>&1; rc=$?; grep -v '^(Re-indexed' /tmp/repro_$$.txt | head -${N:-2}; echo "exit code: $rc"; rm -f /tmp/repro_$$.txt; }

echo "===== R1 function removed together with its header prototype (patch mode)"
mkdir r1; cd r1
printf 'int scale(int x);\n' > lib.h
printf '#include "lib.h"\nint scale(int x) {\n    return x * 2;\n}\n' > lib.c
printf '#include "lib.h"\nint main(void) {\n    return scale(3);\n}\n' > main.c
mk r1
printf '%s\n' '--- a/lib.c' '+++ b/lib.c' '@@ -1,4 +1 @@' ' #include "lib.h"' '-int scale(int x) {' '-    return x * 2;' '-}' '--- a/lib.h' '+++ b/lib.h' '@@ -1 +0,0 @@' '-int scale(int x);' > ../r1.diff
ask $AT check --patch ../r1.diff
patch -p1 -s < ../r1.diff; echo "compiler:"; build

echo "===== R2 function removed, still named in a table (git mode: edit applied, then check)"
mkdir $TOP/r2; cd $TOP/r2
printf 'int op_add(int a);\nint op_sub(int a);\n' > ops.h
printf '#include "ops.h"\nint op_add(int a) { return a + 1; }\nint op_sub(int a) {\n    return a - 1;\n}\n' > ops.c
printf '#include "ops.h"\ntypedef int (*op_fn)(int);\nop_fn table[] = { op_add, op_sub };\nint main(void) { return table[0](1); }\n' > main.c
mk r2
printf '#include "ops.h"\nint op_add(int a) { return a + 1; }\n' > ops.c
N=1 ask $AT check
echo "compiler:"; build

echo "===== R3 function-like macro changes arity in a header (patch mode + hook)"
mkdir $TOP/r3; cd $TOP/r3
printf '#define SCALE(x) ((x) * 2)\n' > defs.h
printf '#include "defs.h"\nint main(void) {\n    return SCALE(2);\n}\n' > user.c
mk r3
printf '%s\n' '--- a/defs.h' '+++ b/defs.h' '@@ -1 +1 @@' '-#define SCALE(x) ((x) * 2)' '+#define SCALE(x, k) ((x) * (k))' > ../r3.diff
N=1 ask $AT check --patch ../r3.diff
python3 - "$PWD" <<'PY' > ../r3_event.json
import json,sys
r=sys.argv[1]
print(json.dumps({"session_id":"s","transcript_path":"/dev/null","cwd":r,"hook_event_name":"PreToolUse","tool_name":"Edit","tool_use_id":"t",
 "tool_input":{"file_path":r+"/defs.h","old_string":"#define SCALE(x) ((x) * 2)","new_string":"#define SCALE(x, k) ((x) * (k))","replace_all":False}}))
PY
out=$($AT hook run < ../r3_event.json 2>&1); echo "hook: exit $? output bytes: ${#out}"
patch -p1 -s < ../r3.diff; echo "compiler:"; build

echo "===== R4 parameter int -> int* (patch mode)"
mkdir $TOP/r4; cd $TOP/r4
printf 'int get(int k);\n' > lib.h
printf '#include "lib.h"\nint get(int k) {\n    return k;\n}\n' > lib.c
printf '#include "lib.h"\nint main(void) {\n    int k = 1;\n    return get(k);\n}\n' > main.c
mk r4
printf '%s\n' '--- a/lib.c' '+++ b/lib.c' '@@ -1,4 +1,4 @@' ' #include "lib.h"' '-int get(int k) {' '-    return k;' '+int get(int *k) {' '+    return *k;' ' }' '--- a/lib.h' '+++ b/lib.h' '@@ -1 +1 @@' '-int get(int k);' '+int get(int *k);' > ../r4.diff
ask $AT check --patch ../r4.diff
patch -p1 -s < ../r4.diff; echo "compiler:"; build

echo "===== R5 duplicate function definition added in another file (patch mode)"
mkdir $TOP/r5; cd $TOP/r5
printf 'int calc(int a) { return a; }\n' > lib.c
printf 'int other(void) { return 0; }\n' > other.c
printf 'int calc(int a);\nint main(void) { return calc(0); }\n' > main.c
mk r5
printf '%s\n' '--- a/other.c' '+++ b/other.c' '@@ -1 +1,2 @@' ' int other(void) { return 0; }' '+int calc(int a) { return a + 1; }' > ../r5.diff
N=1 ask $AT check --patch ../r5.diff
patch -p1 -s < ../r5.diff; echo "compiler:"; build

echo "===== R6 patch that does not match the file (patch mode)"
cd $TOP/r4; git checkout -q .
printf '%s\n' '--- a/lib.c' '+++ b/lib.c' '@@ -1,4 +1,4 @@' ' #include "lib.h"' '-int nothing_like_this(int k) {' '+int get(long k) {' '     return k;' ' }' > ../r6.diff
N=1 ask $AT check --patch ../r6.diff

echo "===== R7 function made static while another file calls it (git mode)"
mkdir $TOP/r7; cd $TOP/r7
printf 'int helper(int a) {\n    return a;\n}\nint lib_entry(void) { return helper(1); }\n' > lib.c
printf 'int helper(int a);\nint main(void) { return helper(2); }\n' > main.c
mk r7
printf 'static int helper(int a) {\n    return a;\n}\nint lib_entry(void) { return helper(1); }\n' > lib.c
N=1 ask $AT check
echo "compiler:"; build
