# مذكرة الخطوة 35 — Governed Computer Use Foundation

**التاريخ:** 2026-08-07  
**الإصدار:** `0.1.0.dev35`

## القرار

إضافة أول سطح Computer Use إلى W1 Nexus من خلال `ActionRuntime` بدل بناء طبقة أتمتة مستقلة تتجاوز الحوكمة الموجودة.

## ما أضيف

- `w1cip.computer_use`.
- `WindowsNativeComputerDriver` خالٍ من الاعتماديات الإضافية.
- `VirtualComputerDriver` للاختبارات والـbenchmarks.
- `computer.observe`.
- `computer.pointer.move`.
- `computer.pointer.click`.
- `computer.scroll`.
- `computer.key.press` لمجموعة تنقّل مغلقة.
- موافقة digest-bound أحادية الاستخدام لكل إدخال افتراضيًا.
- أوامر CLI تحت `w1 computer`.
- benchmark حتمي لا يلمس سطح المكتب الحقيقي.
- إصلاح اختيار schema directory داخل `evaluate_w1_nexus()` عند تشغيل التقييم من الحزمة المثبتة دون `project_root`.

## قرار أمني مهم

لم تتم إضافة كتابة النص الحر. سجل Action Runtime الحالي مصمم للتدقيق ويحتفظ بطلبات الأفعال، ولذلك فإن تمرير أسرار أو كلمات مرور في `parameters` سيجعل التصميم غير مناسب للبيانات الحساسة. يجب أولًا إنشاء Secret Input Channel لا يدوّن المحتوى الخام.

## الأثر على التقييم

يرتفع محور `Governed computer use` من `0.0/3` إلى `2.1/3` عندما ينجح benchmark الجديد. النتيجة المرجعية المستهدفة لـdev35 هي:

- Verified Backend Capability: `93.7%`.
- Full W1 Nexus Completeness: `88.5%`.

تبقى النسبة مقيدة لأن screenshots وAccessibility Tree والكتابة الحرة الآمنة وComputer Use متعدد المنصات لم تكتمل بعد.
