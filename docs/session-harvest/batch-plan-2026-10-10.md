# Batch plan for uncovered MUST / SHOULD / CHEAP rows (2026-10-10)
Source: reports/Right-sized triage.md (433 rows parsed). Uncovered = no PR or session named in its Reason column. Not yet re-checked against merged code after 61439a42.
Counts uncovered: MUST 8, SHOULD 24, CHEAP 122. DEFER 120 + SKIP 63 not scheduled. CLI rows (#1123) dropped.

B1 DATA-LOSS (Opus; after #1068 + #1073 merge, i.e. after gate 1) files: workspace_job_service, backup/restore services, db
 MUST #1116 H1,H2 (+CHEAP L11,L16), #1088 M12 (=H2): CAS on the long writers only
 MUST #1088 H1,H2,H3: sources.db in auto-backup (SQLite backup API), carry live copy, migrate staged copy, quick_check
 SHOULD #1088 14 (vanished file; already in small-must-fixes-oct10, skip duplicate)
 CHEAP #1088 4,5,6,18,20,21; #1124 M3,L8; #1098 L7
B2 TRANSLATION ENGINES (Sonnet; after #1174 and #883 merge) files: engine_backends/*, translate_engines, bulk translate
 MUST #1096 H2 (max_tokens, keep complete pairs); SHOULD H3, M9, #1102 M8, #1103 H1 (after #910, merged: unblocked)
 CHEAP #1096 M8,M10,M14,M15,M16,18,21; #1106 M2; Lead B2,B3,B4,B6,C4,D-M2,D-L2
B3 ASR/CORE (Sonnet; after #1099 and #891) files: audio slicing, subtitle import, hallucination filter, hardsub OCR
 SHOULD #1094 H3 (hard-sub PNG crop/scale), 7, 10, 12 (12 in small-must-fixes), 18
 CHEAP #1094 1,4,5,6,9,11,19,22,23,24; #1116 14
B4 INSTALL/DIAG/JOBS SMALL (Sonnet; after #1126 #1131 #1083 #1085 merge)
 CHEAP #1098 H5 (query strip only),M5,L2,L4,L5; #1119 M6; #1086 M3,L2,L6,L7; Lead 1.5,3.3,4.3,8.2,9.1,A7,A12,A14,A15,B1/D-L1,B7,C1
B5 SERVER/ROUTERS (Sonnet; no blockers)
 SHOULD #1106 M3 (style learning private samples filter), #1116 M3 (atomic export via storage.move_into_place), M9 (locale/style in fix-flagged), M6/M7 need owner rule (pick: keep+flag)
 CHEAP #1118 L2,L3,L5,L6; #1106 M10; #1116 M10 = docstring fix only (owner said)
B6 FRONTEND SMALL (Sonnet; after #1071 #883 #891 + fix-comic #1171)
 CHEAP #1101 M5,14-18; #1102 M5,M16,M19,M20,22-25; #1103 13,15-20,22,25
B7 SCANLATE/EXTENSION (Sonnet; after #1170 + #1087)
 MUST #1119 H2 (Redo blanking); CHEAP H1,M2,L4,L7,L8,L11
B8 DOCS PASS (Sonnet; after merge wave) #1105 H1-3,H6,H11,M12,M13,M19-M23,M25,M26,M29,M32,33-40; #1086 L10; #1123 L22; half-page trust note; device-token wording
B9 TEST TIDY (Sonnet; small) #1121 H2 (pytest warnings as errors for job threads),M10,M18,23; #947 importorskip; #1143 bare join(10)
Already in flight (do not relaunch): small-must-fixes (tsc, .env, GBK, backup vanish), fix-comic-reader (#1103 H3,M4,M6), #1174 (#1096 H4,H5,M12-14), #1115 (drama->title sweep, covers Lead A8, #1102 23), #1169 (regex), #1085/#1083/#1100/#1093.
Owner-only: #1116 M6 rule; #1119 M7 check in browser; #1121 C2 re-transcribe timeout (PC check).
Harvest additions (merged-PR notes, 45 rows in fix-queue-from-reviews.md): treat as CHEAP only if they are bugs: #1143 _verified-after-failure, #1141 409 conflict offers only Reload, #1140 stale doc line, #1107 Windows smoke test (owner checklist).
