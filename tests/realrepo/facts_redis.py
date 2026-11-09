"""Hand-checked facts about Redis 8.10.3 (redis-server, built with MALLOC=libc).

How each fact was checked: I opened the proof lines in the pinned source, found the function each line
sits in by reading upwards to its header, and used grep over src/*.c to make sure no other call site
exists when a fact says "exactly". The suite re-checks every proof line on every run.
"""

from __future__ import annotations

import re
from pathlib import Path

from facts import Edit, Fact, Fn, Line

T = "src/t_string.c"
S = "src/server.c"


def command_table_procs(root: Path) -> list[Fn]:
    """Every command handler in src/commands.def, read from the source text itself (not from the tool).

    Each entry is MAKE_CMD(name, summary, ..., function, arity, ...); the handler is the 14th argument
    (see `#define MAKE_CMD` in src/commands.c:4). NULL means a container command with no handler.
    """
    procs: set[str] = set()
    for line in (root / "src" / "commands.def").read_text().splitlines():
        if "MAKE_CMD(" not in line or line.startswith("#define"):
            continue
        body = line[line.index("MAKE_CMD(") + len("MAKE_CMD(") :]
        args, depth, cur, quoted, i = [], 0, "", False, 0
        while i < len(body):
            ch = body[i]
            if quoted:
                cur += ch
                if ch == "\\":
                    cur += body[i + 1]
                    i += 1
                elif ch == '"':
                    quoted = False
            elif ch == '"':
                quoted, cur = True, cur + ch
            elif ch in "([{":
                depth, cur = depth + 1, cur + ch
            elif ch in ")]}":
                if depth == 0:
                    args.append(cur.strip())
                    break
                depth, cur = depth - 1, cur + ch
            elif ch == "," and depth == 0:
                args.append(cur.strip())
                cur = ""
            else:
                cur += ch
            i += 1
        procs.add(args[13])
    procs.discard("NULL")
    files = {}
    for path in sorted((root / "src").glob("*.c")):
        for m in re.finditer(
            r"^\s*void\s+(\w+Command)\s*\(client \*c\)\s*\{", path.read_text(errors="replace"), re.M
        ):
            files.setdefault(m.group(1), f"src/{path.name}")
    return [Fn(p, files.get(p, "?"), "via_pointer") for p in sorted(procs)]


FACTS = [
    # ------------------------------------------------------------ callers and callees
    Fact(
        id="redis-callers-setGenericCommand",
        why="setGenericCommand is called from exactly four functions, all in t_string.c (grep finds no other "
        "call site in src/). It is never passed as a pointer.",
        kind="callers",
        query=f"setGenericCommand@{T}",
        proof=(
            Line(T, 435, "void setCommand(client *c) {"),
            Line(T, 443, "setGenericCommand(c, args.flags, c->argv[1]"),
            Line(T, 446, "void setnxCommand(client *c) {"),
            Line(T, 448, "setGenericCommand(c, OBJ_SET_NX"),
            Line(T, 451, "void setexCommand(client *c) {"),
            Line(T, 453, "setGenericCommand(c, OBJ_EX"),
            Line(T, 456, "void psetexCommand(client *c) {"),
            Line(T, 458, "setGenericCommand(c, OBJ_PX"),
        ),
        must=(Fn("setCommand", T), Fn("setnxCommand", T), Fn("setexCommand", T), Fn("psetexCommand", T)),
        must_not=(Fn("getCommand", T), Fn("msetGenericCommand", T)),
        exact=True,
    ),
    Fact(
        id="redis-callees-setCommand",
        why="setCommand parses its arguments, re-encodes the value, then calls setGenericCommand.",
        kind="callees",
        query=f"setCommand@{T}",
        proof=(
            Line(T, 438, "parseExtendedStringArgumentsOrReply(c, 3, &args, COMMAND_SET)"),
            Line(T, 442, "c->argv[2] = tryObjectEncoding(c->argv[2]);"),
            Line(T, 443, "setGenericCommand(c, args.flags"),
            Line(T, 301, "int parseExtendedStringArgumentsOrReply(client *c, int start_pos"),
            Line("src/object.c", 999, "robj *tryObjectEncoding(robj *o) {"),
        ),
        must=(
            Fn("parseExtendedStringArgumentsOrReply", T, "exact"),
            Fn("tryObjectEncoding", "src/object.c", "exact"),
            Fn("setGenericCommand", T, "exact"),
        ),
        must_not=(Fn("getGenericCommand", T),),
    ),
    Fact(
        id="redis-callers-getGenericCommand",
        why="GET, GETDEL and GETSET go through getGenericCommand, and so does SET with the GET option "
        "(setGenericCommand line 100). My first draft said SET never calls it; the source proved that wrong. "
        "setCommand itself does not call it (its body is lines 435-444).",
        kind="callers",
        query=f"getGenericCommand@{T}",
        proof=(
            Line(
                T, 87, "void setGenericCommand(client *c, int flags, robj *key, robj **valref, robj *expire,"
            ),
            Line(T, 99, "if (flags & OBJ_SET_GET) {"),
            Line(T, 100, "if (getGenericCommand(c) == C_ERR) return;"),
            Line(T, 475, "void getCommand(client *c) {"),
            Line(T, 476, "getGenericCommand(c);"),
            Line(T, 557, "void getdelCommand(client *c) {"),
            Line(T, 558, "if (getGenericCommand(c) == C_ERR) return;"),
            Line(T, 568, "void getsetCommand(client *c) {"),
            Line(T, 569, "if (getGenericCommand(c) == C_ERR) return;"),
        ),
        must=(
            Fn("getCommand", T),
            Fn("setGenericCommand", T),
            Fn("getdelCommand", T),
            Fn("getsetCommand", T),
        ),
        must_not=(Fn("setCommand", T),),
        exact=True,
    ),
    Fact(
        id="redis-callers-lookupKeyReadOrReply-cross-file",
        why="lookupKeyReadOrReply is defined in db.c and called from t_string.c: a link across files.",
        kind="callers",
        query="lookupKeyReadOrReply@src/db.c",
        proof=(
            Line("src/db.c", 385, "kvobj *lookupKeyReadOrReply(client *c, robj *key, robj *reply) {"),
            Line(T, 461, "int getGenericCommand(client *c) {"),
            Line(T, 464, "lookupKeyReadOrReply(c, c->argv[1], shared.null[c->resp])"),
        ),
        must=(Fn("getGenericCommand", T, "exact"),),
    ),
    Fact(
        id="redis-callers-checkStringLength",
        why="The static helper checkStringLength is called only by SETRANGE (twice) and APPEND.",
        kind="callers",
        query=f"checkStringLength@{T}",
        proof=(
            Line(T, 26, "static int checkStringLength(client *c, long long size, long long append) {"),
            Line(T, 580, "void setrangeCommand(client *c) {"),
            Line(T, 604, "checkStringLength(c,offset,value_len)"),
            Line(T, 623, "checkStringLength(c,offset,value_len)"),
            Line(T, 1369, "void appendCommand(client *c) {"),
            Line(T, 1391, "checkStringLength(c,stringObjectLen(o),append_len)"),
        ),
        must=(Fn("setrangeCommand", T), Fn("appendCommand", T)),
        exact=True,
    ),
    Fact(
        id="redis-callers-call",
        why="processCommand runs a parsed command by calling call().",
        kind="callers",
        query=f"call@{S}",
        proof=(Line(S, 4467, "int processCommand(client *c) {"), Line(S, 4844, "call(c,flags);")),
        must=(Fn("processCommand", S, "exact"),),
    ),
    # ------------------------------------------------------------ function pointers
    Fact(
        id="redis-dispatch-every-command-handler",
        why="call() runs every command through c->cmd->proc(c), a redisCommandProc pointer. The handlers are "
        "listed in the command table (commands.def, included by commands.c). Every one of them must show up "
        "as a via_pointer callee of call(). The list is read from commands.def, not from AeroTrace.",
        kind="callees",
        query=f"call@{S}",
        proof=(
            Line(S, 3992, "void call(client *c, int flags) {"),
            Line(S, 4058, "c->cmd->proc(c);"),
            Line("src/server.h", 2959, "typedef void redisCommandProc(client *c);"),
            Line("src/commands.c", 12, '#include "commands.def"'),
            Line("src/module.c", 1386, "cp->rediscmd->proc = RedisModuleCommandDispatcher;"),
        ),
        must_from_source=command_table_procs,
        # module commands are not in the table: module.c sets their handler to this dispatcher
        must=(Fn("RedisModuleCommandDispatcher", "src/module.c", "via_pointer"),),
    ),
    Fact(
        id="redis-dispatch-comparison-is-not-registration",
        why="sflushCommand is never put in the command table (commands.def has no entry for it, and grep "
        "finds no assignment). blocked.c only compares c->cmd->proc with it. Comparing an address does not "
        "make a function callable, so call() can never reach sflushCommand.",
        kind="callees",
        query=f"call@{S}",
        proof=(
            Line("src/cluster.c", 2192, "void sflushCommand(client *c) {"),
            Line("src/blocked.c", 243, "if (c->cmd && c->cmd->proc == sflushCommand)"),
            Line("src/blocked.c", 308, "if (c->cmd && c->cmd->proc == sflushCommand)"),
        ),
        must_not=(Fn("sflushCommand", "src/cluster.c"),),
        known_bug="Comparing a function's address (== f) is treated as taking its address, so pointer calls "
        "of the same type are linked to it.",
    ),
    Fact(
        id="redis-dispatch-not-other-pointer-types",
        why="serverCron has type int(aeEventLoop*, long long, void*), not redisCommandProc, so the call "
        "through c->cmd->proc in call() can never reach it. call() has no direct call to it either.",
        kind="callees",
        query=f"call@{S}",
        proof=(
            Line(S, 1562, "int serverCron(struct aeEventLoop *eventLoop, long long id, void *clientData) {"),
            Line(S, 4058, "c->cmd->proc(c);"),
        ),
        must_not=(Fn("serverCron", S), Fn("processTimeEvents", "src/ae.c")),
    ),
    Fact(
        id="redis-timer-callback-serverCron",
        why="initServer registers serverCron as a timer callback; the event loop calls it through "
        "te->timeProc, which has the same type (aeTimeProc).",
        kind="callees",
        query="processTimeEvents@src/ae.c",
        proof=(
            Line(S, 3201, "aeCreateTimeEvent(server.el, 1, serverCron, NULL, NULL)"),
            Line("src/ae.c", 284, "static int processTimeEvents(aeEventLoop *eventLoop) {"),
            Line("src/ae.c", 335, "retval = te->timeProc(eventLoop, id, te->clientData);"),
            Line(
                "src/ae.h",
                47,
                "typedef int aeTimeProc(struct aeEventLoop *eventLoop, long long id, void *clientData);",
            ),
        ),
        must=(Fn("serverCron", S, "via_pointer"),),
        must_not=(Fn("setCommand", T),),
    ),
    # ------------------------------------------------------------ blast radius
    Fact(
        id="redis-impact-setGenericCommand",
        why="Changing setGenericCommand affects SET (direct caller), call() and processCommand. call() is one "
        "step away through data: it reads server.dirty (line 4021), which SET writes. (It is also two steps "
        "away through the command table pointer; AeroTrace keeps the shortest path.) processCommand calls "
        "call(). crc16 calls nothing and does not read server.dirty, so it can never be affected.",
        kind="impact",
        query=f"setGenericCommand@{T}",
        depth=3,
        proof=(
            Line(T, 443, "setGenericCommand(c, args.flags"),
            Line(T, 190, "server.dirty++;"),
            Line(S, 4021, "dirty = server.dirty;"),
            Line(S, 4844, "call(c,flags);"),
            Line("src/crc16.c", 82, "uint16_t crc16(const char *buf, int len) {"),
            Line("src/crc16.c", 86, "crc = (crc<<8) ^ crc16tab[((crc>>8) ^ *buf++)&0x00FF];"),
        ),
        must=(
            Fn("setCommand", T, "exact", 1),
            Fn("call", S, "data", 1),
            Fn("processCommand", S, "data", 2),
        ),
        must_not=(Fn("crc16", "src/crc16.c"),),
    ),
    # ------------------------------------------------------------ globals and struct fields
    Fact(
        id="redis-writes-setGenericCommand-dirty",
        why="SET marks the dataset as changed with server.dirty++ (a field of struct redisServer).",
        kind="writes",
        query=f"setGenericCommand@{T}",
        proof=(Line(T, 168, "server.dirty++;"), Line(T, 190, "server.dirty++;")),
        must=("redisServer::dirty",),
    ),
    Fact(
        id="redis-writes-getGenericCommand-nothing",
        why="getGenericCommand only assigns a local (o) and replies; it writes no global or field itself.",
        kind="writes",
        query=f"getGenericCommand@{T}",
        proof=(
            Line(T, 461, "int getGenericCommand(client *c) {"),
            Line(T, 464, "if ((o = lookupKeyReadOrReply(c, c->argv[1], shared.null[c->resp])) == NULL)"),
            Line(T, 471, "addReplyBulk(c,o);"),
        ),
        must_not=("redisServer::dirty",),
        exact=True,
    ),
    Fact(
        id="redis-writes-call-stat_numcommands",
        why="call() counts every command it runs.",
        kind="writes",
        query=f"call@{S}",
        proof=(Line(S, 4228, "server.stat_numcommands++;"),),
        must=("redisServer::stat_numcommands",),
    ),
    Fact(
        id="redis-writes-resetServerStats",
        why="CONFIG RESETSTAT zeroes the counter.",
        kind="writes",
        query=f"resetServerStats@{S}",
        proof=(Line(S, 2913, "void resetServerStats(void) {"), Line(S, 2916, "server.stat_numcommands = 0;")),
        must=("redisServer::stat_numcommands",),
    ),
    Fact(
        id="redis-reads-serverCron-stat_numcommands",
        why="serverCron samples the command counter for the ops/sec metric.",
        kind="reads",
        query=f"serverCron@{S}",
        proof=(Line(S, 1601, "trackInstantaneousMetric(STATS_METRIC_COMMAND, server.stat_numcommands"),),
        must=("redisServer::stat_numcommands",),
    ),
    Fact(
        id="redis-writes-through-atomicIncr",
        why="atomicIncr(var, n) expands to atomic_fetch_add_explicit(&var, n, ...), so writeToClient writes "
        "server.stat_net_output_bytes even though no '=' appears.",
        kind="writes",
        query="writeToClient@src/networking.c",
        proof=(
            Line("src/networking.c", 2892, "atomicIncr(server.stat_net_output_bytes, totwritten);"),
            # -std=gnu11 has C11 atomics, so this branch of atomicvar.h is the one compiled (line 92's #if)
            Line(
                "src/atomicvar.h",
                92,
                "#if !defined(__ATOMIC_VAR_FORCE_SYNC_MACROS) && defined(__STDC_VERSION__)",
            ),
            Line(
                "src/atomicvar.h",
                100,
                "#define atomicIncr(var,count) atomic_fetch_add_explicit(&var,(count),memory_order_relaxed)",
            ),
        ),
        must=("redisServer::stat_net_output_bytes",),
        known_bug="Writes made by passing &var to a function or builtin (atomics) are not recorded.",
    ),
    # ------------------------------------------------------------ code the build does not compile
    Fact(
        id="redis-ifdef-test-code-is-not-indexed",
        why="quicklist.c has its own static ustime(), but only inside #ifdef REDIS_TEST, which a normal "
        "build does not define. So the only ustime is the one in server.c.",
        kind="find",
        query="ustime",
        proof=(
            Line(S, 242, "long long ustime(void) {"),
            Line("src/quicklist.c", 1899, "#ifdef REDIS_TEST"),
            Line("src/quicklist.c", 1941, "static long long ustime(void) {"),
        ),
        must=(Fn("ustime", S),),
        must_not=(Fn("ustime", "src/quicklist.c"),),
        exact=True,
    ),
    # ------------------------------------------------------------ diff impact (patches not applied)
    Fact(
        id="redis-diff-edit-setGenericCommand",
        why="A one-line change inside setGenericCommand changes only that function; SET and call() are "
        "affected. GET's code path is not changed.",
        kind="diff",
        proof=(Line(T, 90, "long long milliseconds = 0; /* initialized to avoid any harmless warning */"),),
        edits=(
            Edit(
                T,
                "    long long milliseconds = 0; /* initialized to avoid any harmless warning */\n",
                "    long long milliseconds = -1; /* golden test edit */\n",
            ),
        ),
        changed=(f"setGenericCommand@{T}",),
        must=(Fn("setCommand", T), Fn("call", S)),
        must_not=(Fn("crc16", "src/crc16.c"),),
        no_outside=True,
    ),
    Fact(
        id="redis-diff-delete-checkStringLength",
        why="Deleting the static helper breaks its only callers, SETRANGE and APPEND.",
        kind="diff",
        proof=(Line(T, 26, "static int checkStringLength("), Line(T, 38, "}")),
        edits=(
            Edit(
                T,
                "static int checkStringLength(client *c, long long size, long long append) {\n"
                "    if (mustObeyClient(c))\n"
                "        return C_OK;\n"
                "    /* 'uint64_t' cast is there just to prevent undefined behavior on overflow */\n"
                "    long long total = (uint64_t)size + append;\n"
                "    /* Test configured max-bulk-len representing a limit of the biggest string object,\n"
                "     * and also test for overflow. */\n"
                "    if (total > server.proto_max_bulk_len || total < size || total < append) {\n"
                '        addReplyError(c,"string exceeds maximum allowed size (proto-max-bulk-len)");\n'
                "        return C_ERR;\n"
                "    }\n"
                "    return C_OK;\n"
                "}\n",
                "",
            ),
        ),
        changed=(f"checkStringLength@{T}",),
        changed_state={f"checkStringLength@{T}": "removed"},
        must=(Fn("setrangeCommand", T, "exact", 1), Fn("appendCommand", T, "exact", 1)),
    ),
    Fact(
        id="redis-diff-field-type-change",
        why="Changing the type of redisServer.stat_numcommands affects every function that reads or writes it "
        "(call, resetServerStats, serverCron), even though no function body changed.",
        kind="diff",
        proof=(
            Line("src/server.h", 2171, "long long stat_numcommands;     /* Number of processed commands */"),
            Line(S, 4228, "server.stat_numcommands++;"),
            Line(S, 2916, "server.stat_numcommands = 0;"),
            Line(S, 1601, "server.stat_numcommands"),
        ),
        edits=(
            Edit(
                "src/server.h",
                "    long long stat_numcommands;     /* Number of processed commands */\n",
                "    unsigned long long stat_numcommands;     /* Number of processed commands */\n",
            ),
        ),
        changed_decls=("redisServer::stat_numcommands",),
        must=(
            Fn("call", S, "uses_changed", 1),
            Fn("resetServerStats", S, "uses_changed", 1),
            Fn("serverCron", S, "uses_changed", 1),
        ),
        must_not=(Fn("crc16", "src/crc16.c"),),
    ),
    Fact(
        id="redis-diff-field-type-change-no-false-note",
        why="The same patch changes one declaration line, and that line is fully understood (the field). So "
        "nothing should be listed under 'Changes AeroTrace could not follow'.",
        kind="diff",
        proof=(Line("src/server.h", 2171, "long long stat_numcommands;"),),
        edits=(
            Edit(
                "src/server.h",
                "    long long stat_numcommands;     /* Number of processed commands */\n",
                "    unsigned long long stat_numcommands;     /* Number of processed commands */\n",
            ),
        ),
        changed_decls=("redisServer::stat_numcommands",),
        no_outside=True,
        known_bug="In patch mode the new text of a changed declaration is reported as 'could not follow' "
        "(only the old side is matched to the field).",
    ),
    Fact(
        id="redis-diff-comment-only",
        why="Rewording a comment outside every function changes no code, so nothing can be affected.",
        kind="diff",
        proof=(
            Line(T, 40, "/* The setGenericCommand() function implements the SET operation with different"),
        ),
        edits=(
            Edit(
                T,
                "/* The setGenericCommand() function implements the SET operation with different\n",
                "/* The setGenericCommand() function implements the SET command with different\n",
            ),
        ),
        changed=(),
        must_not=(Fn("setCommand", T), Fn("setGenericCommand", T)),
    ),
]
