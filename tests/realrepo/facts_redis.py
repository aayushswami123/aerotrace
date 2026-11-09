"""Hand-checked facts about Redis 8.10.3 (redis-server, built with MALLOC=libc).

How each fact was checked: I opened the proof lines in the pinned source, found the function each line
sits in by reading upwards to its header, and used grep over src/*.c to make sure no other call site
exists when a fact says "exactly". The suite re-checks every proof line on every run.
"""

from __future__ import annotations

import re
from pathlib import Path

from facts import Addr, Call, Edit, Fact, Fn, Group, Line

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
        known_bug="#12: Comparing a function's address (== f) is treated as taking its address, so pointer calls "
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
    ),
    Fact(
        id="redis-writes-through-atomicSet",
        why="atomicSet(var, v) expands to atomic_store_explicit(&var, v, ...): the bio thread stores the "
        "fsynced offset.",
        kind="writes",
        query="bioProcessBackgroundJobs@src/bio.c",
        proof=(
            Line("src/bio.c", 261, "void *bioProcessBackgroundJobs(void *arg) {"),
            Line("src/bio.c", 333, "atomicSet(server.fsynced_reploff_pending, job->fd_args.offset);"),
            Line(
                "src/atomicvar.h",
                110,
                "#define atomicSet(var,value) atomic_store_explicit(&var,value,memory_order_relaxed)",
            ),
        ),
        must=("redisServer::fsynced_reploff_pending",),
    ),
    Fact(
        id="redis-atomicGet-is-a-read-not-a-write",
        why="atomicGet(var, dst) expands to dst = atomic_load_explicit(&var, ...): it reads var. "
        "backgroundRewriteDoneHandler (lines 3847-4027) only loads fsynced_reploff_pending; grep finds no "
        "atomicSet of it in that range.",
        kind="reads",
        query="backgroundRewriteDoneHandler@src/aof.c",
        proof=(
            Line("src/aof.c", 3847, "void backgroundRewriteDoneHandler(int exitcode, int bysignal) {"),
            Line("src/aof.c", 3973, "atomicGet(server.fsynced_reploff_pending, fsynced_reploff_pending);"),
            Line("src/aof.c", 4027, "}"),
            Line("src/atomicvar.h", 108, "dstvar = atomic_load_explicit(&var,memory_order_relaxed);"),
        ),
        must=("redisServer::fsynced_reploff_pending",),
    ),
    Fact(
        id="redis-atomicGet-does-not-write",
        why="The same load must not be reported as a write (that would be a false alarm).",
        kind="writes",
        query="backgroundRewriteDoneHandler@src/aof.c",
        proof=(
            Line("src/aof.c", 3973, "atomicGet(server.fsynced_reploff_pending, fsynced_reploff_pending);"),
        ),
        must_not=("redisServer::fsynced_reploff_pending",),
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
    # ------------------------------------------------------------ signatures (build item 5, part A)
    Fact(
        id="redis-signature-checkStringLength",
        why="All three calls pass 3 arguments and compare the result with != C_OK. A change to its "
        "parameters breaks exactly these three lines.",
        kind="signature",
        query=f"checkStringLength@{T}",
        proof=(
            Line(T, 26, "static int checkStringLength(client *c, long long size, long long append) {"),
            Line(T, 604, "if (checkStringLength(c,offset,value_len) != C_OK)"),
            Line(T, 623, "if (checkStringLength(c,offset,value_len) != C_OK)"),
            Line(T, 1391, "if (checkStringLength(c,stringObjectLen(o),append_len) != C_OK)"),
        ),
        calls=(
            Call("setrangeCommand", T, 604, "compared", "with !=", 3, "exact"),
            Call("setrangeCommand", T, 623, "compared", "with !=", 3, "exact"),
            Call("appendCommand", T, 1391, "compared", "with !=", 3, "exact"),
        ),
        exact=True,
        returns_void=False,
    ),
    Fact(
        id="redis-signature-getGenericCommand-uses",
        why="Three callers compare the result with == C_ERR; GET ignores it.",
        kind="signature",
        query=f"getGenericCommand@{T}",
        proof=(
            Line(T, 100, "if (getGenericCommand(c) == C_ERR) return;"),
            Line(T, 476, "getGenericCommand(c);"),
            Line(T, 558, "if (getGenericCommand(c) == C_ERR) return;"),
            Line(T, 569, "if (getGenericCommand(c) == C_ERR) return;"),
        ),
        calls=(
            Call("setGenericCommand", T, 100, "compared", "with =="),
            Call("getCommand", T, 476, "ignored"),
            Call("getdelCommand", T, 558, "compared", "with =="),
            Call("getsetCommand", T, 569, "compared", "with =="),
        ),
        exact=True,
    ),
    Fact(
        id="redis-signature-setGenericCommand-void",
        why="setGenericCommand returns void and takes 9 parameters; its four callers pass 9 arguments each.",
        kind="signature",
        query=f"setGenericCommand@{T}",
        proof=(
            Line(
                T, 87, "void setGenericCommand(client *c, int flags, robj *key, robj **valref, robj *expire,"
            ),
            Line(T, 88, "int unit, robj *match_value, robj *ok_reply, robj *abort_reply)"),
            Line(
                T,
                443,
                "setGenericCommand(c, args.flags, c->argv[1], &(c->argv[2]), args.expire, args.unit, args.match_value, NULL, NULL);",
            ),
            Line(
                T,
                448,
                "setGenericCommand(c, OBJ_SET_NX, c->argv[1], &(c->argv[2]), NULL, 0, NULL, shared.cone, shared.czero);",
            ),
            Line(
                T,
                453,
                "setGenericCommand(c, OBJ_EX, c->argv[1], &(c->argv[3]), c->argv[2], UNIT_SECONDS, NULL, NULL, NULL);",
            ),
            Line(
                T,
                458,
                "setGenericCommand(c, OBJ_PX, c->argv[1], &(c->argv[3]), c->argv[2], UNIT_MILLISECONDS, NULL, NULL, NULL);",
            ),
        ),
        calls=(
            Call("setCommand", T, 443, "ignored", n_args=9),
            Call("setnxCommand", T, 448, "ignored", n_args=9),
            Call("setexCommand", T, 453, "ignored", n_args=9),
            Call("psetexCommand", T, 458, "ignored", n_args=9),
        ),
        exact=True,
        returns_void=True,
    ),
    Fact(
        id="redis-signature-setCommand-table-and-comparison",
        why="Nothing calls setCommand directly (grep finds only its definition and these two lines). The "
        "command table stores it, and setGenericCommand compares c->cmd->proc with it. call() reaches it "
        "through the redisCommandProc pointer. The timer pointer in processTimeEvents has another type.",
        kind="signature",
        query=f"setCommand@{T}",
        proof=(
            Line(
                "src/commands.def",
                13094,
                '{MAKE_CMD("set","Sets the string value of a key, ignoring its type.',
            ),
            Line("src/commands.def", 13094, ",setCommand,"),
            Line(T, 201, "if ((c->cmd->proc == setCommand) && c->argc == 5) {"),
            Line(S, 4058, "c->cmd->proc(c);"),
        ),
        addresses=(
            Addr("src/commands.def", 13094, "stored in an initializer"),
            Addr(T, 201, "compared"),
        ),
        pointer_calls=(Call("call", S, 4058, "ignored", certainty="probable"),),
        must_not=(Fn("processTimeEvents", "src/ae.c"),),
        exact=True,  # no direct call sites
    ),
    Fact(
        id="redis-signature-serverCron-callback",
        why="initServer passes serverCron to aeCreateTimeEvent as its 3rd argument; the event loop calls it "
        "through te->timeProc and stores the result in retval.",
        kind="signature",
        query=f"serverCron@{S}",
        proof=(
            Line(S, 3201, "aeCreateTimeEvent(server.el, 1, serverCron, NULL, NULL)"),
            Line("src/ae.c", 335, "retval = te->timeProc(eventLoop, id, te->clientData);"),
        ),
        addresses=(Addr(S, 3201, "passed to aeCreateTimeEvent() as argument 3"),),
        pointer_calls=(
            Call("processTimeEvents", "src/ae.c", 335, "stored", "in retval", certainty="probable"),
        ),
        must_not=(Fn("call", S),),
        exact=True,
    ),
    Fact(
        id="redis-signature-lookupKeyReadOrReply-stored-then-compared",
        why="GET stores the result in o and compares that assignment with NULL in the same expression. "
        "GETBIT stores it in a new variable and compares on the next line, which AeroTrace does not follow: "
        "it must say only 'stored'.",
        kind="signature",
        query="lookupKeyReadOrReply@src/db.c",
        proof=(
            Line(T, 464, "if ((o = lookupKeyReadOrReply(c, c->argv[1], shared.null[c->resp])) == NULL)"),
            Line("src/bitops.c", 911, "kvobj *kv = lookupKeyReadOrReply(c, c->argv[1], shared.czero);"),
        ),
        calls=(
            Call("getGenericCommand", T, 464, "stored", "in o, then compared with =="),
            Call("getbitCommand", "src/bitops.c", 911, "stored", "in kv"),
        ),
    ),
    Fact(
        id="redis-git-diff-new-parameter-keeps-old-callers",
        why="Adding a parameter to checkStringLength in the working tree leaves its three calls with too few "
        "arguments. They no longer compile, but they are exactly what the change breaks, so diff must still "
        "list setrangeCommand and appendCommand (#14). Only t_string.c is compiled, to keep the test fast; "
        "the function and all its callers are in it.",
        kind="git_diff",
        only_files=(T,),
        proof=(
            Line(T, 26, "static int checkStringLength(client *c, long long size, long long append) {"),
            Line(T, 604, "if (checkStringLength(c,offset,value_len) != C_OK)"),
            Line(T, 1391, "if (checkStringLength(c,stringObjectLen(o),append_len) != C_OK)"),
        ),
        edits=(
            Edit(
                T,
                "static int checkStringLength(client *c, long long size, long long append) {\n",
                "static int checkStringLength(client *c, long long size, long long append, int strict) {\n",
            ),
        ),
        changed=(f"checkStringLength@{T}",),
        must=(Fn("setrangeCommand", T, "exact", 1), Fn("appendCommand", T, "exact", 1)),
        must_not=(Fn("setCommand", T),),
    ),
    # ------------------------------------------------------------ ranked signature answers (work item "ranking")
    Fact(
        id="redis-ranking-getGenericCommand-uses-before-ignored",
        why="Three callers compare the result with == C_ERR; GET ignores it. A ranked answer lists the three "
        "calls that use the result before the one that ignores it, even though getCommand (line 476) comes "
        "before getdelCommand (558) in the file. It must not put the ignored call first. call() and "
        "processCommand reach it only through getCommand's table entry, so the summary must count them as "
        "reached through a pointer call, and call() must never be exact.",
        kind="signature",
        query=f"getGenericCommand@{T}",
        proof=(
            Line(T, 100, "if (getGenericCommand(c) == C_ERR) return;"),
            Line(T, 476, "getGenericCommand(c);"),
            Line(T, 558, "if (getGenericCommand(c) == C_ERR) return;"),
            Line(T, 569, "if (getGenericCommand(c) == C_ERR) return;"),
            # setCommand calls setGenericCommand directly; getCommand is only in the command table (grep
            # finds no direct call), so call() and its callers reach it only through c->cmd->proc.
            Line(T, 435, "void setCommand(client *c) {"),
            Line(T, 475, "void getCommand(client *c) {"),
            Line("src/commands.def", 13079, ",getCommand,"),
            Line(S, 3992, "void call(client *c, int flags) {"),
            Line(S, 4058, "c->cmd->proc(c);"),
            Line(S, 4467, "int processCommand(client *c) {"),
            Line(S, 4844, "call(c,flags);"),
        ),
        depth=2,
        upstream=(
            Fn("setCommand", T, "exact"),
            Fn("call", S, "via_pointer"),
            Fn("processCommand", S, "via_pointer"),
        ),
        must_not=(Fn("call", S, "exact"),),
        ranked=(
            ("setGenericCommand", T, 100),
            ("getdelCommand", T, 558),
            ("getsetCommand", T, 569),
            ("getCommand", T, 476),
        ),
    ),
    Fact(
        id="redis-ranking-setCommand-pointer-groups",
        why="Nothing calls setCommand directly. call() reaches it through c->cmd->proc (a redisCommandProc "
        "pointer), and processCommand and execCommand call call(), so they come in through the pointer call "
        "in call() and are only probable (via_pointer). loadSingleAppendOnlyFile replays the AOF through "
        "cmd->proc, and loadAppendOnlyFiles calls it. processTimeEvents calls te->timeProc, an aeTimeProc "
        "(a different type), so it must not show up as a caller.",
        kind="signature",
        query=f"setCommand@{T}",
        depth=1,
        proof=(
            Line(S, 3992, "void call(client *c, int flags) {"),
            Line(S, 4058, "c->cmd->proc(c);"),
            Line(S, 4467, "int processCommand(client *c) {"),
            Line(S, 4844, "call(c,flags);"),
            Line("src/multi.c", 127, "void execCommand(client *c) {"),
            Line("src/multi.c", 229, "call(c,CMD_CALL_FULL);"),
            Line("src/aof.c", 1810, "int loadSingleAppendOnlyFile(char *filename) {"),
            Line("src/aof.c", 1993, "cmd->proc(fakeClient);"),
            Line("src/aof.c", 2113, "int loadAppendOnlyFiles(aofManifest *am) {"),
            Line("src/aof.c", 2152, "ret = loadSingleAppendOnlyFile(aof_name);"),
            Line("src/server.h", 2959, "typedef void redisCommandProc(client *c);"),
            Line("src/server.h", 3073, "redisCommandProc *proc; /* Command implementation */"),
            Line(
                "src/ae.h",
                47,
                "typedef int aeTimeProc(struct aeEventLoop *eventLoop, long long id, void *clientData);",
            ),
            Line("src/ae.h", 63, "aeTimeProc *timeProc;"),
            Line("src/ae.c", 335, "retval = te->timeProc(eventLoop, id, te->clientData);"),
        ),
        groups=(
            Group(
                f"call@{S}",
                4058,
                (Fn("processCommand", S, "via_pointer"), Fn("execCommand", "src/multi.c", "via_pointer")),
            ),
            Group(
                "loadSingleAppendOnlyFile@src/aof.c",
                1993,
                (Fn("loadAppendOnlyFiles", "src/aof.c", "via_pointer"),),
            ),
        ),
        must_not=(Fn("processTimeEvents", "src/ae.c"),),
    ),
    Fact(
        id="redis-ranking-addReply-void-and-short",
        why="addReply returns void, so a ranked answer must never say a caller ignores its result. It has "
        "hundreds of call sites; the default answer must stay short and count what it leaves out.",
        kind="signature",
        query="addReply@src/networking.c",
        proof=(Line("src/networking.c", 606, "void addReply(client *c, robj *obj) {"),),
        returns_void=True,
        max_lines=60,
        text_must_not=("ignore",),
    ),
    # ------------------------------------------------------------ test impact (static links only)
    Fact(
        id="redis-tests-psetexCommand-not-known",
        why="Redis's tests are Tcl scripts (tests/unit/expire.tcl tests PSETEX), which static links can't "
        "see, and nothing in C calls psetexCommand: the command table stores it. So the answer must be "
        "'Not known', never 'no test reaches it', and no C test may be listed. The C unit tests in "
        "redisTests[] (sdsTest, ...) sit inside #ifdef REDIS_TEST, which this build does not define, so "
        "they are not test entry points; main in server.c is the server, not a test program.",
        kind="tests",
        query=f"psetexCommand@{T}",
        proof=(
            Line(T, 456, "void psetexCommand(client *c) {"),
            Line("src/commands.def", 13093, ",psetexCommand,"),
            Line("tests/unit/expire.tcl", 96, 'test "PSETEX can set sub-second expires" {'),
            Line("src/sds.c", 1290, "#ifdef REDIS_TEST"),
            Line("src/sds.c", 1307, "int sdsTest(int argc, char **argv, int flags) {"),
            Line(S, 8025, '{"sds", sdsTest},'),
            Line(S, 8051, "int main(int argc, char **argv) {"),
        ),
        not_known=("psetexCommand",),
        exact=True,  # no tests listed
        must_not=(Fn("sdsTest", "src/sds.c"), Fn("main", S)),
    ),
    # ------------------------------------------------------------ check (build item 5, part C)
    Fact(
        id="redis-check-parameter-added-breaks-calls",
        why="A 4th parameter is added to the static checkStringLength. Its three calls pass 3 arguments, so "
        "setrangeCommand (two calls) and appendCommand will not compile. It is static, so nothing outside "
        "t_string.c can call it.",
        kind="check",
        proof=(
            Line(T, 26, "static int checkStringLength(client *c, long long size, long long append) {"),
            Line(T, 580, "void setrangeCommand(client *c) {"),
            Line(T, 604, "if (checkStringLength(c,offset,value_len) != C_OK)"),
            Line(T, 623, "if (checkStringLength(c,offset,value_len) != C_OK)"),
            Line(T, 1369, "void appendCommand(client *c) {"),
            Line(T, 1391, "if (checkStringLength(c,stringObjectLen(o),append_len) != C_OK)"),
        ),
        edits=(
            Edit(
                T,
                "static int checkStringLength(client *c, long long size, long long append) {",
                "static int checkStringLength(client *c, long long size, long long append, int strict) {",
            ),
        ),
        breaks=(Fn("setrangeCommand", T, "exact"), Fn("appendCommand", T, "exact")),
        exact=True,
        must_not=(Fn("getrangeCommand", T),),
    ),
    Fact(
        id="redis-check-return-void-breaks-comparisons",
        why="checkStringLength now returns void. All three calls compare its result with != C_OK, which does "
        "not compile with a void value.",
        kind="check",
        proof=(
            Line(T, 604, "if (checkStringLength(c,offset,value_len) != C_OK)"),
            Line(T, 1391, "if (checkStringLength(c,stringObjectLen(o),append_len) != C_OK)"),
        ),
        edits=(
            Edit(T, "static int checkStringLength(client *c,", "static void checkStringLength(client *c,"),
        ),
        breaks=(Fn("setrangeCommand", T, "exact"), Fn("appendCommand", T, "exact")),
    ),
    Fact(
        id="redis-check-field-retype-affects-users-no-break",
        why="stat_numcommands becomes unsigned long long. Every use still compiles, so its users are affected, "
        "not broken, and the changed line is fully understood (#13).",
        kind="check",
        proof=(
            Line("src/server.h", 2171, "long long stat_numcommands;     /* Number of processed commands */"),
            Line(S, 4228, "server.stat_numcommands++;"),
            Line(S, 2916, "server.stat_numcommands = 0;"),
        ),
        edits=(
            Edit(
                "src/server.h",
                "    long long stat_numcommands;     /* Number of processed commands */\n",
                "    unsigned long long stat_numcommands;     /* Number of processed commands */\n",
            ),
        ),
        affected_only=(Fn("call", S), Fn("resetServerStats", S), Fn("serverCron", S)),
        no_breaks=True,
        no_outside=True,
        must_not=(Fn("crc16", "src/crc16.c"),),
    ),
    Fact(
        id="redis-check-bitfield-width-is-never-all-clear",
        why="The width of the bit-field redisObject.encoding changes from 4 to 5 bits. Its type looks the same, "
        "so AeroTrace can't tell what the change does; the answer must not be an all-clear and builds nothing.",
        kind="check",
        proof=(Line("src/object.h", 104, "unsigned encoding:4;"),),
        edits=(Edit("src/object.h", "unsigned encoding:4;", "unsigned encoding:5;"),),
        no_breaks=True,
        not_clear=True,
    ),
]
