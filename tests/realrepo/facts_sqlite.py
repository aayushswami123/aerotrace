"""Hand-checked facts about SQLite 3.54.1 (the official amalgamation: sqlite3.c and shell.c).

How each fact was checked: I opened the proof lines in the pinned source, found the function each line
sits in by reading upwards to its header, checked the #if blocks around it (what a default Linux/macOS
build compiles), and used grep to make sure no other call site exists when a fact says "exactly".
"""

from __future__ import annotations

from facts import Addr, Call, Edit, Fact, Fn, Line

C = "sqlite3.c"
SH = "shell.c"

FACTS = [
    # ------------------------------------------------------------ callers and callees
    Fact(
        id="sqlite-callers-openDatabase",
        why="The static openDatabase is called by exactly the three public open functions (grep finds no "
        "other call site).",
        kind="callers",
        query=f"openDatabase@{C}",
        proof=(
            Line(C, 191739, "static int openDatabase("),
            Line(C, 192104, "SQLITE_API int sqlite3_open("),
            Line(C, 192108, "return openDatabase(zFilename, ppDb,"),
            Line(C, 192111, "SQLITE_API int sqlite3_open_v2("),
            Line(C, 192117, "return openDatabase(filename, ppDb, (unsigned int)flags, zVfs);"),
            Line(C, 192124, "SQLITE_API int sqlite3_open16("),
            Line(C, 192145, "rc = openDatabase(zFilename8, ppDb,"),
        ),
        must=(Fn("sqlite3_open", C), Fn("sqlite3_open_v2", C), Fn("sqlite3_open16", C)),
        exact=True,
    ),
    Fact(
        id="sqlite-callers-sqlite3_open-from-shell",
        why="shell.c calls sqlite3_open, which is defined in sqlite3.c: links across the two files. The call in "
        "recoverTransferSettings is NOT compiled: it needs SQLITE_ENABLE_DBPAGE_VTAB, which a default build "
        "does not set (my first draft missed this #if; the source proved it wrong).",
        kind="callers",
        query=f"sqlite3_open@{C}",
        proof=(
            Line(SH, 1957, "static const char *qrfJsonbToJson(Qrf *p, int iCol){"),
            Line(SH, 1968, 'rc = sqlite3_open(":memory:",&db);'),
            Line(SH, 17087, "sqlite3expert *sqlite3_expert_new(sqlite3 *db, char **pzErrmsg){"),
            Line(SH, 17102, 'rc = sqlite3_open(":memory:", &pNew->dbv);'),
            Line(SH, 20770, "#if !defined(SQLITE_OMIT_VIRTUALTABLE) && defined(SQLITE_ENABLE_DBPAGE_VTAB)"),
            Line(SH, 20773, "#define SQLITE_SHELL_HAVE_RECOVER 0"),
            Line(SH, 20775, "#if SQLITE_SHELL_HAVE_RECOVER"),
            Line(SH, 22985, "static void recoverTransferSettings(sqlite3_recover *p){"),
            Line(SH, 23000, 'int rc = sqlite3_open("", &db2);'),
        ),
        must=(
            Fn("qrfJsonbToJson", SH, "exact"),
            Fn("sqlite3_expert_new", SH, "exact"),
        ),
        must_not=(Fn("recoverTransferSettings", SH),),
    ),
    Fact(
        id="sqlite-callees-sqlite3_step",
        why="sqlite3_step retries sqlite3Step while the schema changes.",
        kind="callees",
        query=f"sqlite3_step@{C}",
        proof=(
            Line(C, 94224, "SQLITE_API int sqlite3_step(sqlite3_stmt *pStmt){"),
            Line(C, 94235, "while( (rc = sqlite3Step(v))==SQLITE_SCHEMA"),
        ),
        must=(Fn("sqlite3Step", C, "exact"),),
    ),
    Fact(
        id="sqlite-callees-sqlite3Step",
        why="sqlite3Step runs the bytecode engine.",
        kind="callees",
        query=f"sqlite3Step@{C}",
        proof=(
            Line(C, 94082, "static int sqlite3Step(Vdbe *p){"),
            Line(C, 94174, "rc = sqlite3VdbeExec(p);"),
        ),
        must=(Fn("sqlite3VdbeExec", C, "exact"),),
    ),
    Fact(
        id="sqlite-impact-sqlite3VdbeExec",
        why="A change to the bytecode engine reaches sqlite3Step (its caller) and sqlite3_step. sqlite3_step is "
        "one step away through data: it reads v->pc, which sqlite3VdbeExec writes (line 98110 is inside "
        "sqlite3VdbeExec, which runs past its vdbe_return label at line 105794). sqlite3_libversion only "
        "returns a constant, so it can never be affected.",
        kind="impact",
        query=f"sqlite3VdbeExec@{C}",
        depth=2,
        proof=(
            Line(C, 97179, "SQLITE_PRIVATE int sqlite3VdbeExec("),
            Line(C, 94174, "rc = sqlite3VdbeExec(p);"),
            Line(C, 94235, "rc = sqlite3Step(v)"),
            Line(C, 94237, "int savedPc = v->pc;"),
            Line(C, 98110, "p->pc = (int)(pOp - aOp) + 1;"),
            Line(C, 105794, "vdbe_return:"),
            Line(C, 188449, "SQLITE_API const char *sqlite3_libversion(void){ return sqlite3_version; }"),
        ),
        must=(Fn("sqlite3Step", C, "exact", 1), Fn("sqlite3_step", C, "data", 1)),
        must_not=(Fn("sqlite3_libversion", C),),
    ),
    Fact(
        id="sqlite-callers-returnSingleInt",
        why="All 16 calls to the static returnSingleInt are inside sqlite3Pragma.",
        kind="callers",
        query=f"returnSingleInt@{C}",
        proof=(
            Line(C, 145533, "static void returnSingleInt(Vdbe *v, i64 value){"),
            Line(C, 145741, "SQLITE_PRIVATE void sqlite3Pragma("),
            Line(C, 147978, "returnSingleInt(v, db->busyTimeout);"),
        ),
        must=(Fn("sqlite3Pragma", C),),
        exact=True,
    ),
    Fact(
        id="sqlite-leaf-sqlite3_libversion",
        why="sqlite3_libversion returns a constant: it calls nothing and writes nothing.",
        kind="callees",
        query=f"sqlite3_libversion@{C}",
        proof=(
            Line(C, 188449, "SQLITE_API const char *sqlite3_libversion(void){ return sqlite3_version; }"),
        ),
        exact=True,
    ),
    # ------------------------------------------------------------ same name, two files
    Fact(
        id="sqlite-static-appendText-in-shell",
        why="shell.c and sqlite3.c each have their own static appendText. Calls in shell.c go to shell.c's.",
        kind="callers",
        query=f"appendText@{SH}",
        proof=(
            Line(SH, 26356, "static void appendText(ShellText *p, const char *zAppend, char quote){"),
            Line(SH, 26417, "static char *shellFakeSchema("),
            Line(SH, 26438, "appendText(&s, zSchema, cQuote);"),
            Line(C, 193228, "static char *appendText(char *p, const char *z){"),
        ),
        must=(Fn("shellFakeSchema", SH, "exact"),),
        must_not=(Fn("sqlite3_create_filename", C),),
    ),
    Fact(
        id="sqlite-static-appendText-in-sqlite3",
        why="The other direction: sqlite3_create_filename calls sqlite3.c's appendText, never shell.c's.",
        kind="callers",
        query=f"appendText@{C}",
        proof=(
            Line(C, 193243, "SQLITE_API const char *sqlite3_create_filename("),
            Line(C, 193261, "p = appendText(p, zDatabase);"),
        ),
        must=(Fn("sqlite3_create_filename", C, "exact"),),
        must_not=(Fn("shellFakeSchema", SH),),
    ),
    # ------------------------------------------------------------ function pointers
    Fact(
        id="sqlite-vfs-xRead",
        why="sqlite3OsRead calls id->pMethods->xRead, type int(sqlite3_file*, void*, int, sqlite3_int64). "
        "The unix, in-memory-db and in-memory-journal VFSes put unixRead, memdbRead and memjrnlRead in that "
        "slot. The xWrite functions take a const void*, a different type, so they must not be linked.",
        kind="callees",
        query=f"sqlite3OsRead@{C}",
        proof=(
            Line(C, 1178, "int (*xRead)(sqlite3_file*, void*, int iAmt, sqlite3_int64 iOfst);"),
            Line(
                C,
                27753,
                "SQLITE_PRIVATE int sqlite3OsRead(sqlite3_file *id, void *pBuf, int amt, i64 offset){",
            ),
            Line(C, 27755, "return id->pMethods->xRead(id, pBuf, amt, offset);"),
            Line(C, 46473, "unixRead,                   /* xRead */"),
            Line(C, 54784, "memdbRead,                       /* xRead */"),
            Line(C, 110111, "memjrnlRead,      /* xRead */"),
            Line(C, 54726, "static int memdbWrite(sqlite3_file*,const void*,int iAmt, sqlite3_int64 iOfst);"),
        ),
        must=(
            Fn("unixRead", C, "via_pointer"),
            Fn("memdbRead", C, "via_pointer"),
            Fn("memjrnlRead", C, "via_pointer"),
        ),
        must_not=(Fn("unixWrite", C), Fn("memdbWrite", C), Fn("memjrnlWrite", C)),
    ),
    Fact(
        id="sqlite-windows-vfs-not-compiled",
        why="winRead is inside #if SQLITE_OS_WIN, which is 0 on Linux and macOS. It must not be claimed.",
        kind="find",
        query="winRead",
        proof=(
            Line(C, 49262, "#if SQLITE_OS_WIN               /* This file is used for Windows only */"),
            Line(C, 51066, "static int winRead("),
        ),
        must_not=(Fn("winRead", C),),
        exact=True,
    ),
    Fact(
        id="sqlite-kvvfs-not-compiled",
        why="kvvfsReadDb is inside #if SQLITE_OS_KV || (SQLITE_OS_UNIX && defined(SQLITE_OS_KV_OPTIONAL)); "
        "a default build sets neither.",
        kind="find",
        query="kvvfsReadDb",
        proof=(
            Line(C, 39588, "#if SQLITE_OS_KV || (SQLITE_OS_UNIX && defined(SQLITE_OS_KV_OPTIONAL))"),
            Line(C, 40171, "static int kvvfsReadDb("),
        ),
        must_not=(Fn("kvvfsReadDb", C),),
        exact=True,
    ),
    Fact(
        id="sqlite-exec-callback-into-shell",
        why="The shell passes captureOutputCallback to sqlite3_exec, which calls it through xCallback. "
        "That is a pointer call from sqlite3.c into shell.c.",
        kind="callees",
        query=f"sqlite3_exec@{C}",
        proof=(
            Line(C, 142838, "SQLITE_API int sqlite3_exec("),
            Line(C, 142906, "if( xCallback(pArg, nCol, azVals, azCols) ){"),
            Line(
                SH, 27816, "static int captureOutputCallback(void *pArg, int nArg, char **azArg, char **az){"
            ),
            Line(SH, 37122, "rc = sqlite3_exec(p->db, zSql, captureOutputCallback, &str, &zErrMsg);"),
        ),
        must=(Fn("captureOutputCallback", SH, "via_pointer"),),
    ),
    Fact(
        id="sqlite-busy-handler-callback",
        why="sqlite3_busy_timeout installs sqliteDefaultBusyCallback (cast to int(*)(void*,int)); "
        "sqlite3InvokeBusyHandler calls it through p->xBusyHandler, which has that type.",
        kind="callees",
        query=f"sqlite3InvokeBusyHandler@{C}",
        proof=(
            Line(C, 16422, "int (*xBusyHandler)(void *,int);  /* The busy callback */"),
            Line(C, 190102, "static int sqliteDefaultBusyCallback("),
            Line(C, 190154, "SQLITE_PRIVATE int sqlite3InvokeBusyHandler(BusyHandler *p){"),
            Line(C, 190157, "rc = p->xBusyHandler(p->pBusyArg, p->nBusy);"),
            Line(C, 190233, "sqlite3_busy_handler(db, (int(*)(void*,int))sqliteDefaultBusyCallback,"),
        ),
        must=(Fn("sqliteDefaultBusyCallback", C, "via_pointer"),),
    ),
    Fact(
        id="sqlite-pager-busy-handler-other-type",
        why="The pager's busy handler is a different pointer type, int(*)(void*). It is set to "
        "btreeInvokeBusyHandler, and it can never reach sqliteDefaultBusyCallback (which takes two arguments).",
        kind="callees",
        query=f"pager_wait_on_lock@{C}",
        proof=(
            Line(C, 59648, "int (*xBusyHandler)(void*); /* Function to call when busy */"),
            Line(C, 62959, "static int pager_wait_on_lock(Pager *pPager, int locktype){"),
            Line(C, 62974, "pPager->xBusyHandler(pPager->pBusyHandlerArg)"),
            Line(C, 75261, "static int btreeInvokeBusyHandler(void *pArg){"),
            Line(C, 75450, "sqlite3PagerSetBusyHandler(pBt->pPager, btreeInvokeBusyHandler, pBt);"),
        ),
        must=(Fn("btreeInvokeBusyHandler", C, "via_pointer"),),
        must_not=(Fn("sqliteDefaultBusyCallback", C),),
    ),
    # ------------------------------------------------------------ globals and struct fields
    Fact(
        id="sqlite-writes-busyTimeout",
        why="sqlite3_busy_timeout stores the timeout in the connection struct.",
        kind="writes",
        query=f"sqlite3_busy_timeout@{C}",
        proof=(
            Line(C, 18979, "int busyTimeout;              /* Busy handler timeout, in msec */"),
            Line(C, 190227, "SQLITE_API int sqlite3_busy_timeout(sqlite3 *db, int ms){"),
            Line(C, 190235, "db->busyTimeout = ms;"),
        ),
        must=("sqlite3::busyTimeout",),
    ),
    Fact(
        id="sqlite-reads-busyTimeout",
        why="The default busy callback reads the timeout. Without HAVE_NANOSLEEP the first branch of the "
        "#if at line 190106 is compiled, and it reads db->busyTimeout.",
        kind="reads",
        query=f"sqliteDefaultBusyCallback@{C}",
        proof=(
            Line(C, 190106, "#if SQLITE_OS_WIN || !defined(HAVE_NANOSLEEP) || HAVE_NANOSLEEP"),
            Line(C, 190115, "int tmout = db->busyTimeout;"),
        ),
        must=("sqlite3::busyTimeout",),
    ),
    Fact(
        id="sqlite-writes-global-config-field",
        why="sqlite3GlobalConfig is a macro for the global sqlite3Config (SQLITE_OMIT_WSD is not set), so "
        "sqlite3_config writes the field Sqlite3Config.bMemstat.",
        kind="writes",
        query=f"sqlite3_config@{C}",
        proof=(
            Line(C, 16505, "#define sqlite3GlobalConfig sqlite3Config"),
            Line(C, 188794, "SQLITE_API int sqlite3_config(int op, ...){"),
            Line(C, 188885, "sqlite3GlobalConfig.bMemstat = va_arg(ap, int);"),
        ),
        must=("Sqlite3Config::bMemstat",),
    ),
    Fact(
        id="sqlite-reads-and-writes-isInit",
        why="sqlite3Initialize checks isInit, then sets it to 1.",
        kind="writes",
        query=f"sqlite3Initialize@{C}",
        proof=(
            Line(C, 188553, "static SQLITE_NOINLINE int sqlite3Initialize(void){"),
            Line(C, 188626, "if( sqlite3GlobalConfig.isInit==0 && sqlite3GlobalConfig.inProgress==0 ){"),
            Line(C, 188660, "sqlite3GlobalConfig.isInit = 1;"),
        ),
        must=("Sqlite3Config::isInit",),
        must_not=("MemPage::isInit", "PCacheGlobal::isInit", "WalIndexHdr::isInit", "Decimal::isInit"),
    ),
    Fact(
        id="sqlite-writes-memset-global",
        why="memset(&mem0, 0, ...) clears the whole allocator state; mem0 is a macro for the global mem0 "
        "(GLOBAL(t, v) is just v without SQLITE_OMIT_WSD).",
        kind="writes",
        query=f"sqlite3MallocEnd@{C}",
        proof=(
            Line(C, 31881, "} mem0 = { 0, SQLITE_MAX_MEMORY, SQLITE_MAX_MEMORY, 0 };"),
            Line(C, 32013, "SQLITE_PRIVATE void sqlite3MallocEnd(void){"),
            Line(C, 32017, "memset(&mem0, 0, sizeof(mem0));"),
        ),
        must=("mem0",),
    ),
    Fact(
        id="sqlite-writes-memset-through-macro",
        why="pcache1 is a macro for the global pcache1_g, so memset(&pcache1, ...) writes pcache1_g.",
        kind="writes",
        query=f"pcache1Shutdown@{C}",
        proof=(
            Line(C, 57232, "} pcache1_g;"),
            Line(C, 57239, "#define pcache1 (GLOBAL(struct PCacheGlobal, pcache1_g))"),
            Line(C, 57748, "static void pcache1Shutdown(void *NotUsed){"),
            Line(C, 57751, "memset(&pcache1, 0, sizeof(pcache1));"),
        ),
        must=("pcache1_g",),
    ),
    # ------------------------------------------------------------ diff impact (patches not applied)
    Fact(
        id="sqlite-diff-edit-busy_timeout",
        why="Changing sqlite3_busy_timeout's body affects the shell's .timeout command (do_meta_command in "
        "shell.c), across files.",
        kind="diff",
        proof=(Line(C, 190235, "db->busyTimeout = ms;"), Line(SH, 37999, "sqlite3_busy_timeout(p->db,")),
        edits=(Edit(C, "    db->busyTimeout = ms;\n", "    db->busyTimeout = ms + 0;\n"),),
        changed=(f"sqlite3_busy_timeout@{C}",),
        must=(Fn("do_meta_command", SH, "exact", 1),),
        must_not=(Fn("sqlite3_libversion", C),),
        no_outside=True,
    ),
    Fact(
        id="sqlite-diff-delete-returnSingleInt",
        why="Deleting returnSingleInt breaks its only caller, sqlite3Pragma.",
        kind="diff",
        proof=(Line(C, 145533, "static void returnSingleInt(Vdbe *v, i64 value){"), Line(C, 145536, "}")),
        edits=(
            Edit(
                C,
                "static void returnSingleInt(Vdbe *v, i64 value){\n"
                "  sqlite3VdbeAddInt64(v, 1, value);\n"
                "  sqlite3VdbeAddOp2(v, OP_ResultRow, 1, 1);\n"
                "}\n",
                "",
            ),
        ),
        changed=(f"returnSingleInt@{C}",),
        changed_state={f"returnSingleInt@{C}": "removed"},
        must=(Fn("sqlite3Pragma", C, "exact", 1),),
    ),
    Fact(
        id="sqlite-diff-field-type-change",
        why="Changing the type of sqlite3.busyTimeout affects every function that uses the field: "
        "sqlite3_busy_timeout and sqlite3_busy_handler (write), sqliteDefaultBusyCallback and "
        "sqlite3Pragma (read).",
        kind="diff",
        proof=(
            Line(C, 18979, "int busyTimeout;"),
            Line(C, 190235, "db->busyTimeout = ms;"),
            Line(C, 190170, "SQLITE_API int sqlite3_busy_handler("),
            Line(C, 190182, "db->busyTimeout = 0;"),
            Line(C, 190115, "int tmout = db->busyTimeout;"),
            Line(C, 147978, "returnSingleInt(v, db->busyTimeout);"),
        ),
        edits=(
            Edit(
                C,
                "  int busyTimeout;              /* Busy handler timeout, in msec */\n",
                "  i64 busyTimeout;              /* Busy handler timeout, in msec */\n",
            ),
        ),
        changed_decls=("sqlite3::busyTimeout",),
        must=(
            Fn("sqlite3_busy_timeout", C, "uses_changed", 1),
            Fn("sqlite3_busy_handler", C, "uses_changed", 1),
            Fn("sqliteDefaultBusyCallback", C, "uses_changed", 1),
            Fn("sqlite3Pragma", C, "uses_changed", 1),
        ),
        must_not=(Fn("sqlite3_libversion", C),),
    ),
    Fact(
        id="sqlite-diff-field-type-change-no-false-note",
        why="The same patch changes one declaration line, and that line is fully understood (the field). So "
        "nothing should be listed under 'Changes AeroTrace could not follow'.",
        kind="diff",
        proof=(Line(C, 18979, "int busyTimeout;"),),
        edits=(
            Edit(
                C,
                "  int busyTimeout;              /* Busy handler timeout, in msec */\n",
                "  i64 busyTimeout;              /* Busy handler timeout, in msec */\n",
            ),
        ),
        changed_decls=("sqlite3::busyTimeout",),
        no_outside=True,
        known_bug="#13: In patch mode the new text of a changed declaration is reported as 'could not follow' "
        "(only the old side is matched to the field).",
    ),
    # ------------------------------------------------------------ signatures (build item 5, part A)
    Fact(
        id="sqlite-signature-openDatabase",
        why="Two of the three open functions return openDatabase's result directly; sqlite3_open16 stores it in rc.",
        kind="signature",
        query=f"openDatabase@{C}",
        proof=(
            Line(C, 192108, "return openDatabase(zFilename, ppDb,"),
            Line(C, 192117, "return openDatabase(filename, ppDb, (unsigned int)flags, zVfs);"),
            Line(C, 192145, "rc = openDatabase(zFilename8, ppDb,"),
        ),
        calls=(
            Call("sqlite3_open", C, 192108, "returned", n_args=4),
            Call("sqlite3_open_v2", C, 192117, "returned", n_args=4),
            Call("sqlite3_open16", C, 192145, "stored", "in rc", 4),
        ),
        exact=True,
    ),
    Fact(
        id="sqlite-signature-sqlite3_open-shell-and-api-table",
        why="The shell stores sqlite3_open's result in rc in one place and ignores it in another. The "
        "extension API table sqlite3Apis stores its address. The recover code is not compiled, so it must "
        "not appear.",
        kind="signature",
        query=f"sqlite3_open@{C}",
        proof=(
            Line(SH, 1968, 'rc = sqlite3_open(":memory:",&db);'),
            Line(SH, 30012, "static void open_db(ShellState *p, int openFlags){"),
            Line(SH, 30059, "sqlite3_open(0, &p->db);"),
            Line(C, 143834, "static const sqlite3_api_routines sqlite3Apis = {"),
            Line(C, 143913, "sqlite3_open,"),
            Line(SH, 20773, "#define SQLITE_SHELL_HAVE_RECOVER 0"),
        ),
        calls=(
            Call("qrfJsonbToJson", SH, 1968, "stored", "in rc", 2),
            Call("open_db", SH, 30059, "ignored", n_args=2),
        ),
        addresses=(Addr(C, 143913, "stored in an initializer"),),
        must_not=(Fn("recoverTransferSettings", SH),),
    ),
    Fact(
        id="sqlite-signature-returnSingleInt-void",
        why="returnSingleInt returns void; all 16 calls are statements of their own in sqlite3Pragma "
        "(grep: 16 lines start with 'returnSingleInt(v,').",
        kind="signature",
        query=f"returnSingleInt@{C}",
        proof=(
            Line(C, 145533, "static void returnSingleInt(Vdbe *v, i64 value){"),
            Line(C, 147978, "returnSingleInt(v, db->busyTimeout);"),
        ),
        calls=(Call("sqlite3Pragma", C, 147978, "ignored", n_args=2),),
        returns_void=True,
    ),
    Fact(
        id="sqlite-signature-busy-callback",
        why="sqlite3_busy_timeout passes the callback (cast) to sqlite3_busy_handler as argument 2. "
        "sqlite3InvokeBusyHandler calls it through xBusyHandler and stores the result in rc. The pager's "
        "busy handler has another type (one argument), so pager_wait_on_lock must not be listed.",
        kind="signature",
        query=f"sqliteDefaultBusyCallback@{C}",
        proof=(
            Line(C, 190233, "sqlite3_busy_handler(db, (int(*)(void*,int))sqliteDefaultBusyCallback,"),
            Line(C, 190157, "rc = p->xBusyHandler(p->pBusyArg, p->nBusy);"),
            Line(C, 62974, "pPager->xBusyHandler(pPager->pBusyHandlerArg)"),
        ),
        addresses=(Addr(C, 190233, "passed to sqlite3_busy_handler() as argument 2"),),
        pointer_calls=(Call("sqlite3InvokeBusyHandler", C, 190157, "stored", "in rc", 2, "probable"),),
        must_not=(Fn("pager_wait_on_lock", C),),
        exact=True,
    ),
    Fact(
        id="sqlite-signature-static-appendText-in-shell",
        why="A signature change to shell.c's static appendText affects only shell.c's calls, never "
        "sqlite3_create_filename's calls to sqlite3.c's own appendText.",
        kind="signature",
        query=f"appendText@{SH}",
        proof=(
            Line(SH, 26438, "appendText(&s, zSchema, cQuote);"),
            Line(C, 193261, "p = appendText(p, zDatabase);"),
        ),
        calls=(Call("shellFakeSchema", SH, 26438, "ignored", n_args=3),),
        must_not=(Fn("sqlite3_create_filename", C),),
    ),
]
