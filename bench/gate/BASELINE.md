# Gate baseline

AeroTrace code measured: `fd0c688`. Cases: 89. This file is generated from `baseline.json`. How to read it: see `README.md`.

| id | build | patch before | patch | git before | git | failing site |
|---|---|---|---|---|---|---|
| a1-asm | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | helper |
| a1-dlsym | passes | 0 correct | 0 correct | 0 correct | 2 false unknown |  |
| a1-enum_inactive | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| a1-ext_cxx | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| a1-ext_def | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| a1-ext_hh | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| a1-ext_inc | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| a1-ext_ipp | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| a1-field_inactive | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| a1-field_outside | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | extra.c |
| a1-macro_inactive | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| a1-macrohdr | fails | 0 false safe | 1 correct | 1 correct | 1 correct | main.c |
| a1-macrohdr_unused | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| a1-method_inactive | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.cpp |
| a1-method_outside | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.cpp |
| a1-param_inactive | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| a1-paste | fails | 0 false safe | 1 correct | 1 correct | 1 correct | main.c |
| a1-paste2 | fails | 0 false safe | 0 false safe | 0 false safe | 2 honest unknown | main.c |
| a1-renamed_fn | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| a1-rettype_inactive | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| a1-typedef_outside | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | extra.c |
| a2-const_rm | fails | 0 false safe | 1 correct | 0 false safe | 2 honest unknown | main.cpp |
| a2-ext_cxx | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | helper |
| a2-ext_def | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | helper |
| a2-ext_hh | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | helper |
| a2-ext_inc | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | helper |
| a2-ext_ipp | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | helper |
| a2-knr | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| a2-noexcept_rm | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.cpp |
| a2-ok_add_param | passes | 0 correct | 0 correct | 0 correct | 2 false unknown |  |
| a2-ok_paramname | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| a2-ok_rename_fn | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| a2-ok_rename_global | passes | 0 correct | 2 false unknown | 1 false alarm | 1 false alarm |  |
| a2-ptrconst | fails | 1 correct | 1 correct | 1 correct | 1 correct | main.c |
| a2-static_macro | fails | 1 correct | 1 correct | 0 false safe | 2 honest unknown | helper |
| a2-static_prevline | fails | 1 correct | 1 correct | 0 false safe | 2 honest unknown | helper |
| cases1-build_file | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| cases1-def_table | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c, ops.def |
| cases1-def_table2 | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| cases1-enum | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| cases1-h07 | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| cases1-ifdef | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| cases1-ifdef2 | fails | 0 false safe | 0 false safe | 0 false safe | 0 false safe | main.c |
| cases1-l01 | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| cases1-l01b | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | f |
| cases1-macro_sa | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| cases1-newfile_dup | fails | 0 false safe | 1 correct | 0 false safe | 2 honest unknown | calc |
| cases1-override | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.cpp |
| cases1-override2 | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.cpp |
| cases1-static_r7 | fails | 1 correct | 1 correct | 0 false safe | 2 honest unknown | helper |
| cases1-struct_sa | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| cases1-struct_sa_c | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| cases1-table_r2 | fails | 1 weak | 1 weak | 0 false safe | 2 honest unknown | op_sub |
| cases1-typedef | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| cases2-h07b | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| cases3-blank | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| cases3-body | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| cases3-filecomment | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| cases3-fncomment | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| cases3-hdrcomment | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| cases3-include | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| cases3-local | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| cases3-newstatic | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| cases3-reformat | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| odd-ctor_outside | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | X::X |
| odd-fnptr_rename | fails | 1 correct | 1 correct | 1 weak | 1 weak | main.c |
| odd-multi_decl_remove | fails | 1 correct | 1 correct | 1 correct | 1 correct | main.c |
| odd-multi_decl_rename_first | passes | 0 correct | 2 false unknown | 1 false alarm | 1 false alarm |  |
| odd2-fnptr_rename_all | passes | 0 correct | 2 false unknown | 1 false alarm | 1 false alarm |  |
| r81-c12 | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| r81-c13 | passes | 0 correct | 0 correct | 0 correct | 2 false unknown |  |
| r81-c14 | passes | 0 correct | 0 correct | 0 correct | 2 false unknown |  |
| r81-c4 | passes | 1 false alarm | 1 false alarm | 1 false alarm | 1 false alarm |  |
| r81-c4d | passes | 1 false alarm | 1 false alarm | 1 false alarm | 1 false alarm |  |
| r81b-a8 | passes | 0 correct | 2 false unknown | 0 correct | 0 correct |  |
| r81b-c1 | passes | 0 correct | 2 false unknown | 0 correct | 0 correct |  |
| r81b-c2 | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| r81b-n0 | passes | 1 false alarm | 1 false alarm | 1 false alarm | 1 false alarm |  |
| r82-comment | passes | 0 correct | 2 false unknown | 0 correct | 2 false unknown |  |
| r82-dmove | passes | 0 correct | 0 correct | 0 correct | 2 false unknown |  |
| r82-dstatic | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| r82-overload | passes | 0 correct | 0 correct | 0 correct | 0 correct |  |
| repro-r1 | fails | 0 false safe | 1 correct | 1 correct | 1 correct | main.c |
| repro-r2 | fails | - | - | 0 false safe | 2 honest unknown | op_sub |
| repro-r3 | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | user.c |
| repro-r4 | fails | 0 false safe | 2 honest unknown | 0 false safe | 2 honest unknown | main.c |
| repro-r5 | fails | 0 false safe | 1 correct | 0 false safe | 1 correct | calc |
| repro-r6 | n/a | 0 false safe | 2 correct | - | - |  |
| repro-r7 | fails | - | - | 0 false safe | 2 honest unknown | helper |

```text
patch mode (87 cases)
  correct:        29
  honest unknown: 34
  false safe:     2
  weak:           1
  false alarm:    21 (of which false unknown: 18)
  before (older build):
    correct 40, honest unknown 0, false safe 43, weak 1, false alarm 3 (false unknown 0)

git mode (88 cases)
  correct:        19
  honest unknown: 43
  false safe:     1
  weak:           1
  false alarm:    24 (of which false unknown: 18)
  before (older build):
    correct 36, honest unknown 0, false safe 45, weak 1, false alarm 6 (false unknown 0)

false safe ids: a1-paste2, cases1-ifdef2
```
