"""Hand-checked facts about who reads and writes struct fields and globals (`aerotrace data`, item 5 part B),
on Redis 8.10.3 and SQLite 3.54.1.

How each fact was checked: I opened every proof line in the pinned source, found the function it sits in by
reading upwards to its header, checked the #if blocks around it, and used grep for the field or global name
over the compiled files when a fact says "exactly". Lines inside a macro call are checked by function only:
the index puts a use at the line where the macro call starts.
"""

from __future__ import annotations

from facts import Addr, Broke, Edit, Fact, Fn, Init, Line, Use

S = "src/server.c"
SH_ = "src/server.h"
LF = "src/lazyfree.c"
AOF = "src/aof.c"
C = "sqlite3.c"

REDIS = [
    Fact(
        id="redis-data-hz-writers",
        why="redisServer.hz (server.h:2053) is written by exactly six functions: updateHZ, serverCron "
        "(1572, 1579, 1581), initServerConfig, initServer, sentinelFlushConfig (saves and restores it) and "
        "sentinelTimer (grep 'server.hz =' and 'server.hz \\*='). serverCron also reads it through the "
        "run_with_period macro (server.h:836). It never writes it at server.c:1850, where server.hz is only "
        "copied into a RedisModuleCronLoopV1.",
        kind="data",
        query="redisServer::hz",
        proof=(
            Line(SH_, 2053, "int hz;                     /* serverCron() calls frequency in hertz */"),
            Line("src/config.c", 2681, "static int updateHZ(const char **err) {"),
            Line("src/config.c", 2687, "server.hz = server.config_hz;"),
            Line(S, 1562, "int serverCron(struct aeEventLoop *eventLoop, long long id, void *clientData) {"),
            Line(S, 1572, "server.hz = server.config_hz;"),
            Line(S, 1579, "server.hz *= 2;"),
            Line(S, 1581, "server.hz = CONFIG_MAX_HZ;"),
            Line(
                SH_,
                836,
                "#define run_with_period(_ms_) _run_with_period(server.cronloops, (_ms_), server.hz)",
            ),
            Line(S, 1592, "run_with_period(100) {"),
            Line(S, 1850, "RedisModuleCronLoopV1 ei = {REDISMODULE_CRON_LOOP_VERSION,server.hz};"),
            Line(S, 2401, "void initServerConfig(void) {"),
            Line(S, 2412, "server.hz = CONFIG_DEFAULT_HZ; /* Initialize it ASAP, even if it may get"),
            Line(S, 2999, "void initServer(void) {"),
            Line(S, 3016, "server.hz = server.config_hz;"),
            Line("src/sentinel.c", 2290, "int sentinelFlushConfig(void) {"),
            Line("src/sentinel.c", 2294, "server.hz = CONFIG_DEFAULT_HZ;"),
            Line("src/sentinel.c", 2296, "server.hz = saved_hz;"),
            Line("src/sentinel.c", 5514, "void sentinelTimer(void) {"),
            Line("src/sentinel.c", 5527, "server.hz = CONFIG_DEFAULT_HZ + rand() % CONFIG_DEFAULT_HZ;"),
            Line(S, 6403, "sds genRedisInfoString(dict *section_dict, int all_sections, int everything) {"),
            Line(S, 6458, '"hz:%i\\r\\n", server.hz,'),
        ),
        uses=(
            Use("updateHZ", "src/config.c", "writes", 2687, "exact"),
            Use("serverCron", S, "writes", 1572),
            Use("serverCron", S, "writes", 1579),
            Use("serverCron", S, "writes", 1581),
            Use("serverCron", S, "reads", 1592),
            Use("initServerConfig", S, "writes", 2412),
            Use("initServer", S, "writes", 3016),
            Use("sentinelFlushConfig", "src/sentinel.c", "writes", 2294),
            Use("sentinelFlushConfig", "src/sentinel.c", "writes", 2296),
            Use("sentinelTimer", "src/sentinel.c", "writes", 5527),
            Use("genRedisInfoString", S, "reads"),  # inside the FMTARGS( macro call: line not checked
        ),
        exact_writers=True,
        must_not=(Use("serverCron", S, "writes", 1850), Use("genRedisInfoString", S, "writes")),
    ),
    Fact(
        id="redis-data-dirty-two-structs",
        why="Two structs have a field named dirty: aofManifest (server.h:2012) and redisServer (server.h:2367). "
        "The manifest's is only used in aof.c; aof.c:711 writes the manifest's dirty through "
        "server.aof_manifest, not server.dirty. call() and initServer() use server.dirty, never the "
        "manifest's.",
        kind="data",
        query="aofManifest::dirty",
        proof=(
            Line(SH_, 2012, "int         dirty;"),
            Line(SH_, 2367, "long long dirty;                /* Changes to DB from the last save */"),
            Line(AOF, 506, "void markRewrittenIncrAofAsHistory(aofManifest *am) {"),
            Line(AOF, 535, "am->dirty = 1;"),
            Line(AOF, 619, "int persistAofManifest(aofManifest *am) {"),
            Line(AOF, 620, "if (am->dirty == 0) {"),
            Line(AOF, 689, "int aofDelHistoryFiles(void) {"),
            Line(AOF, 711, "server.aof_manifest->dirty = 1;"),
            Line(S, 3992, "void call(client *c, int flags) {"),
            Line(S, 4021, "dirty = server.dirty;"),
            Line(S, 3151, "server.dirty = 0;"),
        ),
        uses=(
            Use("markRewrittenIncrAofAsHistory", AOF, "writes", 535),
            Use("persistAofManifest", AOF, "reads", 620),
            Use("aofDelHistoryFiles", AOF, "writes", 711),
        ),
        must_not=(Fn("call", S), Fn("initServer", S)),
    ),
    Fact(
        id="redis-data-dirty-server-not-manifest",
        why="The other side: aof.c:711 (server.aof_manifest->dirty = 1) does not write redisServer.dirty; "
        "call() reads it at server.c:4021 and initServer() resets it at server.c:3151.",
        kind="data",
        query="redisServer::dirty",
        proof=(
            Line(AOF, 711, "server.aof_manifest->dirty = 1;"),
            Line(S, 3992, "void call(client *c, int flags) {"),
            Line(S, 4021, "dirty = server.dirty;"),
            Line(S, 2999, "void initServer(void) {"),
            Line(S, 3151, "server.dirty = 0;"),
        ),
        uses=(Use("call", S, "reads", 4021), Use("initServer", S, "writes", 3151)),
        must_not=(Fn("aofDelHistoryFiles", AOF), Fn("markRewrittenIncrAofAsHistory", AOF)),
    ),
    Fact(
        id="redis-data-subcommands-table",
        why="redisCommand.subcommands is set by the file-scope designated initializer of redisCommandTable "
        "(.subcommands=CLIENT_Subcommands at commands.def:12833) and read by populateCommandStructure and "
        "latencyAllCommandsFillCDF. subcommands_dict, another field, is used all over acl.c: not this one.",
        kind="data",
        query="redisCommand::subcommands",
        proof=(
            Line(SH_, 3084, "struct redisCommand *subcommands;"),
            Line(SH_, 3106, "dict *subcommands_dict;"),
            Line("src/commands.def", 12798, "struct COMMAND_STRUCT redisCommandTable[] = {"),
            Line("src/commands.def", 12833, ".subcommands=CLIENT_Subcommands},"),
            Line(S, 3478, "int populateCommandStructure(struct redisCommand *c) {"),
            Line(S, 3502, "if (c->subcommands) {"),
            Line("src/latency.c", 487, "if (cmd->subcommands) {"),
            Line("src/acl.c", 651, "if (cmd->subcommands_dict) {"),
        ),
        uses=(
            Use("populateCommandStructure", S, "reads", 3502),
            Use("latencyAllCommandsFillCDF", "src/latency.c", "reads", 487),
        ),
        inits=(Init("redisCommandTable", "src/commands.def", 12833),),
        exact=True,
    ),
    Fact(
        id="redis-data-child-info-pipe-escape",
        why="openChildInfoPipe passes the array server.child_info_pipe to anetPipe(), which fills it in: a "
        "write AeroTrace can't follow, so it must be listed as an escape (unknown).",
        kind="data",
        query="redisServer::child_info_pipe",
        proof=(
            Line("src/childinfo.c", 25, "void openChildInfoPipe(void) {"),
            Line("src/childinfo.c", 26, "if (anetPipe(server.child_info_pipe, O_NONBLOCK, 0) == -1) {"),
        ),
        uses=(Use("openChildInfoPipe", "src/childinfo.c", None, 26),),
        escapes=(Addr("src/childinfo.c", 26, "passed to anetPipe()"),),
    ),
    Fact(
        id="redis-data-child-info-pipe-element-is-not-an-escape",
        why="close(server.child_info_pipe[0]) passes one int by value; the array's address does not leave.",
        kind="data",
        query="redisServer::child_info_pipe",
        proof=(
            Line("src/childinfo.c", 36, "void closeChildInfoPipe(void) {"),
            Line("src/childinfo.c", 40, "close(server.child_info_pipe[0]);"),
        ),
        uses=(Use("closeChildInfoPipe", "src/childinfo.c", None, 40),),
        must_not=(Addr("src/childinfo.c", 40, "passed to close()"),),
        known_bug="#18: an array element passed by value is recorded as the array's address escaping",
    ),
    Fact(
        id="redis-data-lazyfree-objects",
        why="The static lazyfree_objects (lazyfree.c:9) is changed only through the atomicIncr/atomicDecr "
        "macros, in exactly 14 functions, and read by one (lazyfreeGetPendingObjectsCount, atomicGet). "
        "genRedisInfoString calls that reader. lazyfreeGetFreedObjectsCount and lazyfreeResetStats use "
        "lazyfreed_objects, another global (grep -n 'lazyfree_objects' src/*.c: only lazyfree.c).",
        kind="data",
        query="lazyfree_objects",
        proof=(
            Line(LF, 9, "static redisAtomic size_t lazyfree_objects = 0;"),
            Line(LF, 14, "void lazyfreeFreeObject(void *args[]) {"),
            Line(LF, 17, "atomicDecr(lazyfree_objects,1);"),
            Line(LF, 55, "void kvsLazyfreeFree(void *args[]) {"),
            Line(LF, 70, "atomicDecr(lazyfree_objects,numkeys);"),
            Line(LF, 83, "void lazyFreeTrackingTable(void *args[]) {"),
            Line(LF, 87, "atomicDecr(lazyfree_objects,len);"),
            Line(LF, 92, "void lazyFreeErrors(void *args[]) {"),
            Line(LF, 96, "atomicDecr(lazyfree_objects,len);"),
            Line(LF, 101, "void lazyFreeLuaScripts(void *args[]) {"),
            Line(LF, 107, "atomicDecr(lazyfree_objects,len);"),
            Line(LF, 112, "void lazyFreeFunctionsCtx(void *args[]) {"),
            Line(LF, 119, "atomicDecr(lazyfree_objects,len);"),
            Line(LF, 124, "void lazyFreeReplicationBacklogRefMem(void *args[]) {"),
            Line(LF, 131, "atomicDecr(lazyfree_objects,len);"),
            Line(LF, 136, "size_t lazyfreeGetPendingObjectsCount(void) {"),
            Line(LF, 138, "atomicGet(lazyfree_objects,aux);"),
            Line(LF, 143, "size_t lazyfreeGetFreedObjectsCount(void) {"),
            Line(LF, 145, "atomicGet(lazyfreed_objects,aux);"),
            Line(LF, 149, "void lazyfreeResetStats(void) {"),
            Line(LF, 150, "atomicSet(lazyfreed_objects,0);"),
            Line(LF, 229, "void freeObjAsync(robj *key, robj *obj, int dbid) {"),
            Line(LF, 236, "atomicIncr(lazyfree_objects,1);"),
            Line(LF, 347, "void emptyDbDataAsync(kvstore *keys, kvstore *expires, ebuckets hexpires,"),
            Line(LF, 348, "atomicIncr(lazyfree_objects, kvstoreSize(keys));"),
            Line(LF, 354, "void freeTrackingRadixTreeAsync(rax *tracking) {"),
            Line(LF, 357, "atomicIncr(lazyfree_objects,tracking->numele);"),
            Line(LF, 366, "void freeErrorsRadixTreeAsync(rax *errors) {"),
            Line(LF, 369, "atomicIncr(lazyfree_objects,errors->numele);"),
            Line(LF, 378, "void freeLuaScriptsAsync(dict *lua_scripts, list *lua_scripts_lru_list,"),
            Line(LF, 380, "atomicIncr(lazyfree_objects,dictSize(lua_scripts));"),
            Line(LF, 388, "void freeFunctionsAsync(functionsLibCtx *functions_lib_ctx, dict *engs) {"),
            Line(
                LF,
                390,
                "atomicIncr(lazyfree_objects,functionsLibCtxFunctionsLen(functions_lib_ctx)+dictSize(engs));",
            ),
            Line(LF, 399, "void freeReplicationBacklogRefMemAsync(list *blocks, rax *index) {"),
            Line(LF, 403, "atomicIncr(lazyfree_objects,listLength(blocks)+raxSize(index));"),
            Line(S, 6403, "sds genRedisInfoString(dict *section_dict, int all_sections, int everything) {"),
            Line(S, 6597, '"lazyfree_pending_objects:%zu\\r\\n", lazyfreeGetPendingObjectsCount(),'),
        ),
        uses=(
            Use("lazyfreeFreeObject", LF, "writes", 17),
            Use("kvsLazyfreeFree", LF, "writes", 70),
            Use("lazyFreeTrackingTable", LF, "writes", 87),
            Use("lazyFreeErrors", LF, "writes", 96),
            Use("lazyFreeLuaScripts", LF, "writes", 107),
            Use("lazyFreeFunctionsCtx", LF, "writes", 119),
            Use("lazyFreeReplicationBacklogRefMem", LF, "writes", 131),
            Use("freeObjAsync", LF, "writes", 236),
            Use("emptyDbDataAsync", LF, "writes", 348),
            Use("freeTrackingRadixTreeAsync", LF, "writes", 357),
            Use("freeErrorsRadixTreeAsync", LF, "writes", 369),
            Use("freeLuaScriptsAsync", LF, "writes", 380),
            Use("freeFunctionsAsync", LF, "writes", 390),
            Use("freeReplicationBacklogRefMemAsync", LF, "writes", 403),
            Use("lazyfreeGetPendingObjectsCount", LF, "reads", 138),
        ),
        exact=True,
        must=(Fn("genRedisInfoString", S, distance=1),),
        depth=2,
        must_not=(Fn("lazyfreeGetFreedObjectsCount", LF), Fn("lazyfreeResetStats", LF)),
    ),
    Fact(
        id="redis-data-config-hz-address-in-table",
        why='createIntConfig("hz", ..., server.config_hz, ...) in the file-scope table static_configs expands '
        "to .config.i = &(config_addr): the address of server.config_hz is stored there, and CONFIG SET "
        "writes through it. That must show up as a place its address goes.",
        kind="data",
        query="redisServer::config_hz",
        proof=(
            Line("src/config.c", 2368, ".config.i = &(config_addr) \\"),
            Line("src/config.c", 3398, "standardConfig static_configs[] = {"),
            Line(
                "src/config.c",
                3537,
                'createIntConfig("hz", NULL, MODIFIABLE_CONFIG, 0, INT_MAX, server.config_hz,',
            ),
        ),
        escapes=(Addr("src/config.c", 3537, "static_configs"),),
        known_bug="#27: the address of a field stored in a file-scope initializer is recorded nowhere",
    ),
    Fact(
        id="redis-data-removed-subcommands-designator",
        why="After removing redisCommand.subcommands (server.h:3084), the designated initializers "
        ".subcommands=... in commands.def stop compiling ('field designator ... does not refer to any "
        "field'), and so do the reads in populateCommandStructure and latencyAllCommandsFillCDF.",
        kind="data",
        query="redisCommand::subcommands",
        proof=(
            Line(SH_, 3084, "struct redisCommand *subcommands;"),
            Line("src/commands.def", 12833, ".subcommands=CLIENT_Subcommands},"),
            Line(S, 3502, "if (c->subcommands) {"),
            Line("src/latency.c", 487, "if (cmd->subcommands) {"),
        ),
        edits=(Edit(SH_, "    struct redisCommand *subcommands;\n", "\n"),),
        broken=(
            Broke("populateCommandStructure", S, 3502, "exact"),
            Broke("latencyAllCommandsFillCDF", "src/latency.c", 487, "exact"),
            Broke("", "src/commands.def", 12833),
        ),
        known_bug="#25: 'field designator ... does not refer to any field' is not recorded as a missing member",
    ),
]

SQLITE = [
    Fact(
        id="sqlite-data-busyTimeout",
        why="sqlite3.busyTimeout (18979) has exactly four compiled uses: read by sqlite3Pragma and "
        "sqliteDefaultBusyCallback, written by sqlite3_busy_handler and sqlite3_busy_timeout. The read at "
        "190136 is in the #else of the HAVE_NANOSLEEP test, not compiled; sqlite3Pragma only calls "
        "sqlite3_busy_timeout (147976), it never assigns the field (grep 'busyTimeout').",
        kind="data",
        query="sqlite3::busyTimeout",
        proof=(
            Line(C, 18979, "int busyTimeout;              /* Busy handler timeout, in msec */"),
            Line(C, 145741, "SQLITE_PRIVATE void sqlite3Pragma("),
            Line(C, 147976, "sqlite3_busy_timeout(db, n);"),
            Line(C, 147978, "returnSingleInt(v, db->busyTimeout);"),
            Line(C, 190102, "static int sqliteDefaultBusyCallback("),
            Line(C, 190106, "#if SQLITE_OS_WIN || !defined(HAVE_NANOSLEEP) || HAVE_NANOSLEEP"),
            Line(C, 190115, "int tmout = db->busyTimeout;"),
            Line(C, 190136, "int tmout = ((sqlite3 *)ptr)->busyTimeout;"),
            Line(C, 190170, "SQLITE_API int sqlite3_busy_handler("),
            Line(C, 190182, "db->busyTimeout = 0;"),
            Line(C, 190227, "SQLITE_API int sqlite3_busy_timeout(sqlite3 *db, int ms){"),
            Line(C, 190235, "db->busyTimeout = ms;"),
        ),
        uses=(
            Use("sqlite3Pragma", C, "reads", 147978, "exact"),
            Use("sqliteDefaultBusyCallback", C, "reads", 190115, "exact"),
            Use("sqlite3_busy_handler", C, "writes", 190182, "exact"),
            Use("sqlite3_busy_timeout", C, "writes", 190235, "exact"),
        ),
        exact=True,
        exact_writers=True,
        must_not=(Use("sqliteDefaultBusyCallback", C, None, 190136), Use("sqlite3Pragma", C, "writes")),
    ),
    Fact(
        id="sqlite-data-busyTimeout-value-question",
        why="Who reads what sqlite3_busy_timeout writes to busyTimeout: sqlite3Pragma and "
        "sqliteDefaultBusyCallback. Fields are tracked per type, not per object, so they are only probable "
        "(they may read another connection's busyTimeout).",
        kind="data",
        query="sqlite3::busyTimeout",
        data_from=f"sqlite3_busy_timeout@{C}",
        proof=(
            Line(C, 190235, "db->busyTimeout = ms;"),
            Line(C, 147978, "returnSingleInt(v, db->busyTimeout);"),
            Line(C, 190115, "int tmout = db->busyTimeout;"),
        ),
        uses=(
            Use("sqlite3Pragma", C, "reads", 147978, "probable"),
            Use("sqliteDefaultBusyCallback", C, "reads", 190115, "probable"),
        ),
        exact=True,
    ),
    Fact(
        id="sqlite-data-busyTimeout-removed",
        why="Delete the field (18979) in a copy and re-index: each of the four compiled uses stops compiling "
        "('no member named busyTimeout'), each in its own function. 190136 is not compiled, so it is not one "
        "of them. (One row per line, not per use.)",
        kind="data",
        query="sqlite3::busyTimeout",
        proof=(
            Line(C, 18979, "int busyTimeout;              /* Busy handler timeout, in msec */"),
            Line(C, 147978, "returnSingleInt(v, db->busyTimeout);"),
            Line(C, 190115, "int tmout = db->busyTimeout;"),
            Line(C, 190136, "int tmout = ((sqlite3 *)ptr)->busyTimeout;"),
            Line(C, 190182, "db->busyTimeout = 0;"),
            Line(C, 190235, "db->busyTimeout = ms;"),
        ),
        edits=(Edit(C, "  int busyTimeout;              /* Busy handler timeout, in msec */\n", "\n"),),
        broken=(
            Broke("sqlite3Pragma", C, 147978, "exact"),
            Broke("sqliteDefaultBusyCallback", C, 190115, "exact"),
            Broke("sqlite3_busy_handler", C, 190182, "exact"),
            Broke("sqlite3_busy_timeout", C, 190235, "exact"),
        ),
        must_not=(Broke("sqliteDefaultBusyCallback", C, 190136),),
    ),
    Fact(
        id="sqlite-data-bSmallMalloc-macro-and-ifdef",
        why="Sqlite3Config.bSmallMalloc is used through the sqlite3GlobalConfig macro (16505): read by "
        "sqlite3VdbeSorterInit, written by sqlite3_config. vdbeMemRenderNum names it only inside "
        "#if ... defined(__i386__), not compiled here. The global sqlite3Config sets it by position "
        "(24368), so sqlite3Config must be listed as a global that may set it by position.",
        kind="data",
        query="Sqlite3Config::bSmallMalloc",
        proof=(
            Line(C, 21502, "u8 bSmallMalloc;                  /* Avoid large memory allocations if true */"),
            Line(C, 16505, "#define sqlite3GlobalConfig sqlite3Config"),
            Line(C, 107471, "SQLITE_PRIVATE int sqlite3VdbeSorterInit("),
            Line(C, 107562, "if( sqlite3GlobalConfig.bSmallMalloc==0 ){"),
            Line(C, 188794, "SQLITE_API int sqlite3_config(int op, ...){"),
            Line(C, 188889, "sqlite3GlobalConfig.bSmallMalloc = va_arg(ap, int)!=0;"),
            Line(C, 85293, "static void vdbeMemRenderNum(int sz, char *zBuf, Mem *p){"),
            Line(C, 85298, "#if GCC_VERSION>=7000000 && GCC_VERSION<15000000 && defined(__i386__)"),
            Line(C, 85304, "assert( (sqlite3Config.bSmallMalloc!=0xee)*8==sizeof(x) );"),
            Line(C, 24362, "SQLITE_PRIVATE SQLITE_WSD struct Sqlite3Config sqlite3Config = {"),
            Line(C, 24368, "0,                         /* bSmallMalloc */"),
        ),
        uses=(
            Use("sqlite3VdbeSorterInit", C, "reads", 107562),
            Use("sqlite3_config", C, "writes", 188889),
        ),
        exact=True,
        by_position=("sqlite3Config",),
        must_not=(Fn("vdbeMemRenderNum", C),),
    ),
    Fact(
        id="sqlite-data-bSmallMalloc-positional-initializer",
        why="sqlite3Config = { ... } sets bSmallMalloc by position at 24368; that is an initializer that sets "
        "the field, and a field removal or reorder breaks it.",
        kind="data",
        query="Sqlite3Config::bSmallMalloc",
        proof=(
            Line(C, 24362, "SQLITE_PRIVATE SQLITE_WSD struct Sqlite3Config sqlite3Config = {"),
            Line(C, 24368, "0,                         /* bSmallMalloc */"),
        ),
        inits=(Init("sqlite3Config", C, 24368),),
        known_bug="#26: initializers that set fields by position are not recorded",
    ),
    Fact(
        id="sqlite-data-keyinfo-offsetof",
        why="sqlite3KeyInfoAlloc sizes a KeyInfo with SZ_KEYINFO(0), which is offsetof(KeyInfo,aColl) "
        "(19882): renaming aColl breaks that line.",
        kind="data",
        query="KeyInfo::aColl",
        proof=(
            Line(C, 19882, "#define SZ_KEYINFO(N)  (offsetof(KeyInfo,aColl) + (N)*sizeof(CollSeq*))"),
            Line(C, 151133, "SQLITE_PRIVATE KeyInfo *sqlite3KeyInfoAlloc(sqlite3 *db, int N, int X){"),
            Line(C, 151138, "p = sqlite3DbMallocRawNN(db, SZ_KEYINFO(0) + nExtra);"),
        ),
        uses=(Use("sqlite3KeyInfoAlloc", C, None, 151138),),
        known_bug="#24: offsetof(S, field) is never recorded as a use of the field",
    ),
    Fact(
        id="sqlite-data-vfs-pNext-two-copies",
        why="struct sqlite3_vfs is defined twice (sqlite3.c:1838 in the amalgamation, sqlite3.h:1517 for "
        "shell.c), so sqlite3_vfs::pNext names two fields. The sqlite3.c copy is used by sqlite3_vfs_find, "
        "vfsUnlink and sqlite3_vfs_register; do_meta_command (shell.c) uses the sqlite3.h copy. Savepoint "
        "has its own pNext (sqlite3CloseSavepoints): never mixed in.",
        kind="data",
        query=f"sqlite3_vfs::pNext@{C}",
        proof=(
            Line(C, 1838, "sqlite3_vfs *pNext;      /* Next registered VFS */"),
            Line("sqlite3.h", 1517, "sqlite3_vfs *pNext;      /* Next registered VFS */"),
            Line(C, 28027, "SQLITE_API sqlite3_vfs *sqlite3_vfs_find(const char *zVfs){"),
            Line(C, 28040, "for(pVfs = vfsList; pVfs; pVfs=pVfs->pNext){"),
            Line(C, 28051, "static void vfsUnlink(sqlite3_vfs *pVfs){"),
            Line(C, 28056, "vfsList = pVfs->pNext;"),
            Line(C, 28073, "SQLITE_API int sqlite3_vfs_register(sqlite3_vfs *pVfs, int makeDflt){"),
            Line(C, 28087, "pVfs->pNext = vfsList;"),
            Line("shell.c", 38134, "for(pVfs=sqlite3_vfs_find(0); pVfs; pVfs=pVfs->pNext){"),
            Line(C, 189559, "SQLITE_PRIVATE void sqlite3CloseSavepoints(sqlite3 *db){"),
            Line(C, 189562, "db->pSavepoint = pTmp->pNext;"),
        ),
        uses=(
            Use("sqlite3_vfs_find", C, "reads", 28040),
            Use("vfsUnlink", C, "reads", 28056),
            Use("sqlite3_vfs_register", C, "writes", 28087),
        ),
        exact=True,
        must_not=(Fn("do_meta_command", "shell.c"), Fn("sqlite3CloseSavepoints", C)),
    ),
    Fact(
        id="sqlite-data-vfsList-global",
        why="The static vfsList (28020, used through the macro at 28021) is used by exactly sqlite3_vfs_find, "
        "vfsUnlink and sqlite3_vfs_register. sqlite3_vfs_unregister only calls vfsUnlink (28109): a caller "
        "at distance 1, not a user. assert(vfsList) at 28093 is not compiled (NDEBUG is defined at 15354).",
        kind="data",
        query="vfsList",
        proof=(
            Line(C, 28020, "static sqlite3_vfs * SQLITE_WSD vfsList = 0;"),
            Line(C, 28021, "#define vfsList GLOBAL(sqlite3_vfs *, vfsList)"),
            Line(C, 28040, "for(pVfs = vfsList; pVfs; pVfs=pVfs->pNext){"),
            Line(C, 28055, "}else if( vfsList==pVfs ){"),
            Line(C, 28088, "vfsList = pVfs;"),
            Line(C, 28093, "assert(vfsList);"),
            Line(C, 15354, "# define NDEBUG 1"),
            Line(C, 28101, "SQLITE_API int sqlite3_vfs_unregister(sqlite3_vfs *pVfs){"),
            Line(C, 28109, "vfsUnlink(pVfs);"),
        ),
        uses=(
            Use("sqlite3_vfs_find", C, "reads", 28040),
            Use("vfsUnlink", C, "reads", 28055),
            Use("sqlite3_vfs_register", C, "writes", 28088),
        ),
        exact=True,
        must=(Fn("sqlite3_vfs_unregister", C, distance=1),),
        depth=1,
        must_not=(Fn("sqlite3_vfs_unregister", C), Use("sqlite3_vfs_register", C, None, 28093)),
    ),
]
