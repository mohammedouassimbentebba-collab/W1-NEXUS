# Revision — Step 36: Computer Perception & Secure Input

**الإصدار:** `0.1.0.dev36`  
**التاريخ:** 7 أغسطس 2026

## ما تغير

وسع Step 36 أساس Governed Computer Use من dev35 بدل استبداله. أضيفت screenshots محكومة، Win32 accessibility-lite element discovery، selectors دلالية، fingerprint verification قبل الأفعال، قناة نص ephemeral أحادية الاستخدام، وملء file chooser بمسار ملف محصور داخل workspace.

## قرارات الأمان

- لا يدخل plaintext إلى ActionRequest أو ActionResult أو SQLite.
- لا يوجد `--text` في CLI؛ الإدخال عبر prompt مخفي أو stdin.
- UI tree الكامل لا يستمر في journal؛ يحفظ count/hash فقط.
- Screenshot bytes لا تدخل SQLite؛ تحفظ في state قصير العمر مع SHA-256 وTTL.
- selector action يرفض zero/multiple matches.
- click/type/file choose يرفض fingerprint قديمًا.
- file choose يقبل فقط ملفًا موجودًا تحت workspace.
- يمكن فرض allowlist/denylist لعناوين النوافذ.

## النطاق غير المدعى

Win32 element discovery ليس تطبيقًا كاملاً لـMicrosoft UI Automation. كما لا توجد بعد adapters أصلية لـmacOS/Linux أو visual grounding/redaction للنماذج. لذلك ارتفع محور Computer Use إلى 95% من وزنه الداخلي وليس 100%.
