"""The Part B tasks used by the harness, as data. Prompts and checks are copied from docs/benchmark/tasks.md.

Only the tasks of the Sonnet pilot are here (R03, R04, B01, C02). `run.py prepare` checks that every prompt
and the Redis suffix are byte-identical to tasks.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

REDIS_SUFFIX = """\
You are in the root of the Redis 8.10.3 source tree. Build with `make -C src -j$BENCH_JOBS MALLOC=libc`.
Tests run with `./runtest --clients $BENCH_JOBS --baseport $BENCH_BASEPORT --portcount 500 --single <unit>`,
for example `--single unit/type/string`. If you start a redis-server yourself, use port $BENCH_PORT and
stop it when you are done. When you are done, the build must succeed with no compiler warnings and the
existing tests must still pass. Do not edit the Tcl tests under tests/. Do not commit."""


@dataclass(frozen=True)
class Check:
    name: str
    cmd: str  # shell, run from the judged copy's root with $JUDGE, $RUN_TMP, $PORT, $PRISTINE set
    want: str | None = None  # expected stdout (stripped); None = pass on exit code 0
    want_empty: bool = False  # pass when stdout is empty


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    units: tuple[str, ...]
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def full_prompt(self) -> str:
        return self.prompt + "\n\n" + REDIS_SUFFIX


def _argcount(name: str, n: int) -> Check:
    return Check(f"argcount {name} {n}", f'python3 -I "$JUDGE/argcount.py" src {name} {n}')


R03 = Task(
    id="R03",
    prompt="""\
Change notifyKeyspaceEvent() and notifyKeyspaceEventWithSubkeys() (src/notify.c) so that their fourth
parameter is `redisDb *db` instead of `int dbid`. Update the prototypes in src/server.h and every caller to
pass the database pointer. Inside notify.c use db->id where the number is needed. Do not change the module
API (moduleNotifyKeyspaceEvent and the RM_* functions keep their current signatures).""",
    units=(
        "unit/pubsub",
        "unit/keyspace",
        "unit/expire",
        "unit/type/hash",
        "unit/type/hash-field-expire",
        "unit/moduleapi/keyspace_events",
        "unit/moduleapi/postnotifications",
    ),
    checks=(
        Check(
            "prototype notifyKeyspaceEvent",
            "tr -s ' \\t\\n' ' ' < src/server.h | grep -c 'void notifyKeyspaceEvent(int type, const char "
            "\\*event, robj \\*key, redisDb \\*db);'",
            want="1",
        ),
        Check(
            "prototype notifyKeyspaceEventWithSubkeys",
            "tr -s ' \\t\\n' ' ' < src/server.h | grep -c 'void notifyKeyspaceEventWithSubkeys(int type, "
            "const char \\*event, robj \\*key, redisDb \\*db, robj \\*\\*subkeys, int count);'",
            want="1",
        ),
        _argcount("notifyKeyspaceEvent", 4),
        _argcount("notifyKeyspaceEventWithSubkeys", 6),
        Check(
            "module API unchanged",
            "grep -c 'void moduleNotifyKeyspaceEvent(int type, const char \\*event, robj \\*key, int dbid, "
            "robj \\*\\*subkeys, int count);' src/server.h",
            want="1",
        ),
    ),
)

R04 = Task(
    id="R04",
    prompt="""\
No time event handler uses its `long long id` argument. Remove it from the aeTimeProc type in src/ae.h, so
it becomes:

typedef int aeTimeProc(struct aeEventLoop *eventLoop, void *clientData);

Update the event loop and every time event handler in the source tree (server, benchmark and anything
else that is built by `make -C src`).""",
    units=(
        "unit/expire",
        "unit/other",
        "unit/info",
        "unit/maxmemory",
        "unit/moduleapi/timer",
        "integration/redis-benchmark",
    ),
    checks=(
        Check(
            "typedef",
            "tr -s ' \\t\\n' ' ' < src/ae.h | grep -c 'typedef int aeTimeProc(struct aeEventLoop "
            "\\*eventLoop, void \\*clientData);'",
            want="1",
        ),
        *(
            _argcount(n, 2)
            for n in (
                "serverCron",
                "activeDefragTimeProc",
                "evictionTimeProc",
                "IOThreadCron",
                "moduleTimerHandler",
                "showThroughput",
                "timeProc",
            )
        ),
    ),
)

B01 = Task(
    id="B01",
    prompt="""\
In struct redisCommand (src/server.h), move the `since` field so that it comes directly after
`declared_name`, before `summary`. Commands must behave, and be documented by COMMAND DOCS, exactly as
before.""",
    units=("unit/introspection-2", "unit/introspection", "integration/redis-cli"),
    checks=(
        Check(
            "field order",
            "sed -n '/^struct redisCommand {/,/^};/p' src/server.h | grep -oE "
            "'\\*(declared_name|summary|complexity|since);' | head -4 | tr '\\n' ' '",
            want="*declared_name; *since; *summary; *complexity;",
        ),
        Check(
            "MAKE_CMD follows the struct",
            "grep -c '^#define MAKE_CMD(.*) name,since,summary,complexity,' src/commands.c",
            want="1",
        ),
        Check("COMMAND DOCS identical (probe-b01.sh)", 'sh "$JUDGE/probe-b01.sh" "$PORT" "$PRISTINE"'),
    ),
)

C02 = Task(
    id="C02",
    prompt="""\
The clients_pending_read field of struct redisServer is created at startup but never used. Remove the field
and its initialisation.""",
    units=("unit/networking", "unit/info"),
    checks=(Check("no clients_pending_read left", "grep -rnI 'clients_pending_read' src", want_empty=True),),
)

TASKS = {t.id: t for t in (R03, R04, B01, C02)}
