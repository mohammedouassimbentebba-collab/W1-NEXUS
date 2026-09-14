# مذكرة Step 39 — Plugin & Adoption SDK Stabilization

**الإصدار:** `0.1.0.dev39`

تم تثبيت أول Compatibility Contract عام لـW1 Nexus عبر `w1cip.sdk 1.0.0` وPlugin API `1.0`. أضيف manifest fail-closed، registry محلي لكل Workspace، copy-on-install مع SHA-256 tree lock، permission grants وتمكين/تعطيل، subprocess host بعقد JSON وtimeout وsecret-environment scrubbing، conformance suite، scaffold، وCLI كامل.

تم ربط managed provider plugins بـMulti-Model Access عبر `w1-plugin:<id>` مع الحفاظ على legacy `module:callable` للتوافق الخلفي. لا يعد subprocess sandbox أمنيًا؛ permissions تحكم W1 host capabilities فقط. marketplace والتوقيع وdependency resolution وextension points الأوسع مؤجلة.
