# Security policy

AeroTrace reads source code that is often private and safety-critical. We treat these as security bugs:

- Any network traffic made by AeroTrace (it must make none at analysis time).
- Reading or writing files outside the repo being analysed, other than files `aerotrace install` was asked to change.
- Code execution triggered by analysing a repo (for example, through a crafted `compile_commands.json`).
- Leaking source code or paths into logs or output meant for someone else.

What AeroTrace does to stay safe on an untrusted repo:

- It never runs the compiler or any other program named in `compile_commands.json`. A cross compiler's
  target is read from its name.
- Flags that load plugins into clang (`-fplugin=`, `-Xclang -load`, ...) are removed, including inside
  response files.
- Response files (`@file`) are only read from inside the repo or the build folder.
- Git is only used to read (`diff`, `show`, `ls-files`, `rev-parse`), and refs are checked first.
  Every git call sets `GIT_NO_LAZY_FETCH=1`, so a partial clone never fetches. A missing old file is reported.
  This needs git 2.45 or newer; older git ignores it.

## Reporting

Please **don't** open a public issue. Email **aayushswami.dev@gmail.com** with "AeroTrace security" in the
subject. Include the steps to reproduce and the version (`aerotrace --version`).

You'll get a reply within 3 working days. We'll agree on a fix date with you before anything is made public.

## Supported versions

Until 1.0, only the latest release gets security fixes.
