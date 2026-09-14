# Governed Computer Use في W1 Nexus

## الحالة الحالية — dev36

بدأت الطبقة في Step 35 كسطح إدخال محدود ومحكوم. توسعها Step 36 إلى **Computer Perception & Secure Input** مع الحفاظ على القاعدة الأساسية: لا ينفذ W1 فعلًا حساسًا اعتمادًا على إحداثيات أو نص سري غير محكوم.

السطح الحالي يجمع بين:

- `computer.observe`: هندسة الشاشة، المؤشر، والنافذة النشطة؛ قراءة أساسية لا تحتاج موافقة.
- `computer.screenshot`: التقاط PNG محلي محكوم، يحتاج موافقة افتراضيًا، ويخزن في حالة W1 قصيرة العمر بدل وضع البايتات في SQLite.
- `computer.elements.list`: فحص عناصر UI محليًا؛ النتيجة التفصيلية تعاد للطلب الحالي فقط، بينما الـjournal يحتفظ بالعدد و`tree_hash` ولا يحتفظ بأسماء/نصوص الشجرة كاملة.
- `computer.element.click`: نقرة على عنصر يتم حله بواسطة selector ويجب أن يطابق `expected_fingerprint` الذي رآه المستخدم/الوكيل أثناء الفحص.
- `computer.element.type`: كتابة نص عبر `EphemeralInputVault` أحادي الاستخدام؛ النص نفسه لا يدخل `ActionRequest` ولا `ActionResult` ولا SQLite.
- `computer.file.choose`: إدخال مسار ملف موجود داخل workspace إلى حقل ملف موثق بالبصمة، مع `Enter` اختياري.
- إدخال الإحداثيات القديم (`move/click/scroll/key`) ما زال موجودًا للتوافق، لكنه ليس المسار المفضل عندما يوجد عنصر دلالي قابل للفحص.

## القناة السرية المؤقتة

عند تسجيل نص للكتابة، يولد W1:

1. `input_handle` عشوائيًا.
2. `input_binding` على شكل HMAC غير قابل للمقارنة بين العمليات.
3. `text_length` فقط.

النص يبقى في process memory داخل `bytearray`، يستهلك مرة واحدة ثم تتم محاولة مسح البايتات. هذه آلية **عدم استمرارية** وليست ادعاءً بأن Python يوفر محو ذاكرة ماديًا مضمونًا ضد مهاجم يملك وصولًا كاملاً للعملية.

لا توفر CLI خيار `--text "..."` عمدًا حتى لا يظهر السر في shell history. تستخدم prompt مخفيًا أو stdin، ويجب أن تتم الموافقة والتنفيذ في العملية نفسها لأن الـhandle لا يعيش بعد انتهائها.

## Screenshots

الـScreenshot يحتاج موافقة حسب السياسة `require_approval_for_computer_perception`. تحفظ الصورة في:

`.w1nexus/computer-captures/`

ويحمل الـresult فقط: الحجم، الأبعاد، SHA-256، المسار، وTTL. ينظف runtime الملفات الأقدم من `computer_capture_ttl_seconds` عند التقاط صورة جديدة. لا تخزن PNG bytes في قاعدة التدقيق.

## عناصر UI وSelectors

يدعم selector الحالي الحقول:

- `element_id`
- `role`
- `name` / `name_glob`
- `class_name`
- `control_id`
- `process_id`
- `window_title` / `window_glob`

الفعل الدلالي يجب أن يحل إلى **عنصر واحد فقط**. إذا لم يوجد عنصر، أو وجد أكثر من عنصر، يرفض التنفيذ. كما أن click/type/file choose تتطلب `expected_fingerprint` من 64 خانة. البصمة تشمل هوية العنصر الدلالية والهندسة؛ لذلك تغير موضع/هوية الهدف يؤدي إلى رفض stale action بدل replay أعمى.

يمكن للسياسة كذلك تقييد النوافذ باستخدام `computer_window_allowlist` و`computer_window_denylist`.

## Windows backend

`WindowsNativeComputerDriver` خالٍ من dependencies الإضافية ويستخدم Win32:

- `user32` للمؤشر، النوافذ، child controls، والنص Unicode.
- `gdi32` + `BitBlt/GetDIBits` لالتقاط screenshot وتحويله محليًا إلى PNG.
- `EnumWindows/EnumChildWindows` + class/control/window metadata لبناء **Win32 accessibility-lite surface**.

هذا مهم: السطح الحالي **ليس Microsoft UI Automation كاملًا**. تطبيقات Electron/Chromium/custom canvas قد لا تكشف عناصرها الداخلية كلها عبر child HWNDs، لذلك لا ندعي أن dev36 يفهم كل واجهة Windows دلاليًا.

## CLI

```bash
w1 computer status
w1 computer benchmark
w1 computer observe
w1 computer screenshot
w1 computer elements --window-title "Example" --approve
```

بعد الحصول على fingerprint:

```bash
w1 computer click-element --role button --name "Save" --fingerprint <sha256> --approve
w1 computer type --role textbox --name "Password" --fingerprint <sha256> --approve
w1 computer choose-file ./report.pdf --role textbox --name "File name:" --fingerprint <sha256> --approve
```

## Benchmark الحتمي

`VirtualComputerDriver` يختبر دون لمس host desktop:

- PNG screenshot + SHA-256.
- UI element discovery وselector resolution.
- stale fingerprint rejection.
- digest-bound approval للقراءات الحساسة والإدخال.
- selector-bound click.
- ephemeral text execution مع التحقق أن plaintext غير موجود في SQLite journal.
- عدم حفظ element payload الكامل في result المخزن.
- bounded pointer/scroll/navigation compatibility.

## حدود dev36

المتبقي قبل اعتبار Computer Use ناضجًا بالكامل:

1. Microsoft UI Automation provider كامل وخصائص AutomationId/patterns بدل Win32 accessibility-lite فقط.
2. adapters أصلية لـmacOS وLinux.
3. visual grounding فوق screenshots مع redaction/policy قبل إرسال أي صورة إلى نموذج سحابي.
4. semantic replay متعدد الخطوات مع pre/postconditions وتحليل تغير الحالة، بدل إعادة تنفيذ عمياء.
5. تكامل Browser/DOM اختياري ومحكوم عندما يكون الوصول الدلالي عبر DOM أدق من الشاشة.
