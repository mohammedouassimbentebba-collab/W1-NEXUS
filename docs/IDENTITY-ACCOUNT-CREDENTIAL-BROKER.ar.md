# Identity & Account / Credential Broker — Step 37

## الهدف

يضيف Step 37 طبقة وصول حسابات وأسرار محلية أولًا إلى W1 Nexus من دون تحويل W1 إلى خادم هوية مركزي. المستخدم يبقى مالك الحساب والمفتاح، وW1 يحتفظ فقط بالبيانات الوصفية اللازمة للتنسيق والتدقيق.

## الحدود المعمارية

- لا تستورد W1 cookies أو session tokens من تطبيقات أخرى.
- لا يفترض W1 أن اشتراك تطبيق يعطي API access.
- لا يوجد fallback إلى ملف plaintext إذا تعذر OS-native vault.
- SQLite يحتفظ بمراجع الأسرار، الحسابات، الـscopes، تواريخ الانتهاء، وحالة التدقيق فقط؛ token/API-key plaintext يبقى في vault.
- معرفات المشاريع تُستخدم لاشتقاق namespace منفصل للـvault، لتقليل تصادم أو تسرب أسرار مشروع إلى آخر.

## OS-native vault

الـadapters الموجودة:

- Windows Credential Manager عبر `CredWriteW/CredReadW/CredDeleteW`.
- macOS Keychain عبر Security.framework مباشرة، بلا تمرير كلمة السر في command-line arguments.
- Linux Secret Service عبر `secret-tool` مع تمرير القيمة عبر stdin.

`MemoryCredentialVault` موجود للاختبارات فقط ولا يُستخدم كـproduction fallback.

## OAuth

يدعم الـbroker بصورة عامة:

1. Authorization Code flow.
2. PKCE S256 مع verifier محفوظ في الـvault فقط وstate مخزن في SQLite كـSHA-256 فقط.
3. Device Authorization flow مع device code محفوظ في الـvault.
4. Refresh token rotation/lifecycle.
5. Revocation عند إعلان المزود `revocation_endpoint`.
6. Multi-account لنفس المزود.
7. OAuth Authorization Server Metadata / OIDC discovery.
8. HTTPS إجباري لنقاط OAuth، مع استثناء loopback HTTP الصريح للتطبيقات المحلية والاختبارات.

لا يستخرج Step 37 هوية موثوقة من `id_token` غير متحقق منه. يمكن حفظ `id_token` كcredential material داخل الـvault، لكن إثبات OIDC identity cryptographically يبقى خطوة لاحقة.

## مراجع الأسرار

يمكن لطبقات W1 القديمة استخدام مرجع بدل secret value:

- `w1-credential:<credential_id>` لمفتاح API أو secret يملكه المستخدم.
- `w1-account:<account_id>` للحصول على access token للحساب، مع refresh تلقائي عند الحاجة.

`WorkspaceCredentialSecretResolver` يفهم هذه المراجع ثم يرجع للـEnvironmentSecretResolver للمراجع التقليدية. هذا يسمح لـProvider Connectors وModel Access بالعمل من دون تخزين السر في profile أو providers.json.

## CLI

السطوح الجديدة:

```text
w1 accounts list|show|vault|benchmark|audit
w1 accounts providers list|add|discover|capabilities
w1 accounts login|complete
w1 accounts device-start|device-poll
w1 accounts refresh|revoke|remove
w1 credentials list|set|remove
```

`w1 credentials set` لا يقبل secret كـargument. القيمة تدخل عبر hidden prompt أو stdin. وكذلك authorization code النهائي يدخل عبر hidden prompt أو stdin بدل وضعه في shell history.

## التدقيق

الأحداث تحفظ في `credential_audit` مع SHA-256 hash chain. التفاصيل تمر عبر redaction، ولا تحفظ access token أو refresh token أو code verifier أو authorization code أو client secret.

## ما لا ندعيه بعد

- لم تُجر live certification لكل OS-native vault backend في بيئة البناء الحالية.
- لا يوجد automatic loopback callback listener؛ إكمال Authorization Code يتم حاليًا بإدخال code مرة واحدة.
- لا يوجد تحقق OIDC ID-token/JWKS كامل بعد.
- لا توجد provider-specific OAuth presets مضمونة لكل مزود؛ الـbroker عام ويحتاج metadata/client registration يسمح بها المزود رسميًا.
- لا نحاول تجاوز سياسات المزود أو استخدام browser/session credentials غير المخصصة للـAPI.
