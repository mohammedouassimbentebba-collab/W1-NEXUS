# Revision — Step 37: Identity & Account / Credential Broker

**الإصدار:** `0.1.0.dev37`  
**التاريخ:** 8 أغسطس 2026

## ما تغير

أضيف `credential_broker.py` كطبقة محلية ومحايدة للمزود لإدارة API credentials وحسابات OAuth. يدعم OS-native vault، Authorization Code + PKCE S256، Device Flow، refresh/revocation، multi-account، discovery، audit hash chain، ومراجع `w1-account:`/`w1-credential:`.

## قرارات الأمان

- لا تخزين plaintext secrets في SQLite أو ملفات workspace.
- لا fallback إلى ملف secret عند غياب vault أصلي.
- PKCE verifier وdevice code لا يدخلان قاعدة البيانات.
- state يحفظ كـhash فقط، وauthorization code أحادي الاستخدام ولا يُحفظ.
- CLI لا يضع API key أو authorization code في argv.
- provider metadata التي تحتوي حقولًا حساسة مباشرة مثل `client_secret` تُرفض.
- OAuth endpoints يجب أن تكون HTTPS، مع loopback HTTP صريح فقط عند الحاجة المحلية.
- audit events redaction-safe ومترابطة بالـSHA-256.

## التكامل

`build_providers()` وModel Access Fabric في CLI يستخدمان `WorkspaceCredentialSecretResolver`. لذلك يمكن لموصل موجود مسبقًا أن يطلب `w1-account:alice` ويحصل على token من الـvault في لحظة الاستدعاء، مع refresh عند الحاجة.

## القياس

أضيف deterministic credential-broker benchmark لا يستخدم شبكة حقيقية ولا native vault للمضيف. يقيس discovery، PKCE، state hashing، API-key vault resolution، code exchange، refresh، multi-account، غياب plaintext من SQLite، audit chain، ومنع cookie/session import.
