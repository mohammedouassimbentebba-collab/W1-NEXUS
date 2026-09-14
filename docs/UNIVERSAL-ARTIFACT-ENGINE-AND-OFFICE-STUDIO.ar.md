# W1 Universal Artifact Engine + Office Studio

## الغرض

توحّد هذه الطبقة المستندات والجداول والعروض تحت نموذج JSON واحد قابل للتحقق والمراجعة. لا يرتبط النموذج بمزود ذكاء اصطناعي أو خادم W1، ويمكن لأي نموذج داخل Portfolio اقتراح تغييرات محددة بدل استبدال الملف كاملًا.

## النموذج الموحّد

كل Artifact تحتوي على:

- `schema_version`: الإصدار الدلالي لنموذج W1.
- `artifact_id`: معرف ثابت لا يمكن تعديله عبر Patch.
- `kind`: أحد `document` أو `spreadsheet` أو `presentation`.
- `title` و`locale` و`metadata`.
- `content`: بنية متخصصة حسب النوع.

### المستند

يدعم كتلًا منظمة: العناوين، الفقرات، القوائم، الجداول، Callouts وفواصل الصفحات.

### جدول البيانات

يدعم عدة Sheets، قيمًا وصيغًا، صفوف عناوين، عرض الأعمدة، ومواصفات رسوم بيانية. يُستعمل `artifact_tool` عند توفره وسلامته، ويعود W1 تلقائيًا إلى مُصدّر OOXML داخلي عند تعذر عملية WASM/RPC.

### العرض

يدعم Slides وعناصر نصية وجداول وأشكال ورسومًا بيانية مع هندسة قابلة للتحديد.

## التعديلات القابلة للمراجعة

يطبق W1 مجموعة آمنة من RFC 6902:

```json
[
  {"op":"test","path":"/kind","value":"document"},
  {"op":"add","path":"/content/blocks/-","value":{"type":"paragraph","text":"New paragraph"}}
]
```

العمليات المدعومة هي `add` و`replace` و`remove` و`test`. يمنع تعديل `schema_version` و`artifact_id` و`kind`، ويعاد التحقق من النموذج كاملًا بعد كل Patch.

## الإصدارات والمراجعات

كل تغيير ينشئ Revision جديدة تحمل:

- بصمة النموذج.
- بصمة الأب.
- المؤلف والتوقيت.
- JSON Patch الأصلية.
- سلسلة أحداث مترابطة بالبصمات.

لا يستطيع مؤلف Revision اعتمادها كمراجع مستقل. التصدير يحتاج مراجعة `approved` تطابق بصمة Revision الحالية؛ فإذا تغيرت Artifact بعد المراجعة تصبح الموافقة القديمة غير صالحة للنسخة الجديدة.

## التصدير المحكوم

التنسيقات الحالية:

| النوع | التنسيقات |
|---|---|
| Document | DOCX، PDF، JSON |
| Spreadsheet | XLSX، PDF، JSON |
| Presentation | PPTX، PDF، JSON |

تُنشأ البايتات محليًا ثم توضع مؤقتًا بصلاحيات مقيدة داخل:

```text
.w1nexus/office-staging/
```

ويحتوي Action Runtime على المسار والبصمة فقط، لا محتوى الملف. ينفذ `file.publish_staged` ما يلي:

1. التحقق من أن الملف المرحلي داخل المسار المخصص.
2. التحقق من SHA-256.
3. أخذ نسخة احتياطية من الهدف.
4. كتابة ذرية للملف الثنائي.
5. حذف الملف المرحلي.
6. تمكين `undo` عند الحاجة.

## CLI

```bash
w1 office validate --spec artifact.json
w1 office create --spec artifact.json --created-by author
w1 office list
w1 office show ARTIFACT_ID
w1 office history ARTIFACT_ID
w1 office patch ARTIFACT_ID --patch patch.json --created-by model-a --expected-parent-hash HASH
w1 office review ARTIFACT_ID --version 2 --reviewer verifier --outcome approved --rationale "Verified"
w1 office export ARTIFACT_ID --format docx --output exports/report.docx --exported-by owner --approve-action
w1 office verify
w1 office demo --output-dir office-demo
```

## Desktop Shell

تعرض الواجهة قائمة Office Artifacts، النموذج الحالي، سجل الإصدارات، المراجعة والتصدير. تبقى العمليات معطلة في الوضع الافتراضي للقراءة فقط، ولا تصبح متاحة إلا مع `--allow-operations`. لا تتجاوز الواجهة Action Runtime أو قواعد المراجعة المستقلة.

## التحقق

يتحقق W1 بنيويًا من:

- ZIP وParts المطلوبة في DOCX/XLSX/PPTX.
- رأس PDF وعدد الصفحات الممكن استخراجه.
- JSON وبصمة المحتوى.
- سلسلة Revision والأحداث والمراجعات والتصديرات.
- تطابق الملف المنشور مع بصمة البايتات المولدة.

## الحدود الحالية

- لا يوجد استيراد كامل يحافظ على كل تفاصيل ملفات Office عشوائية موجودة مسبقًا.
- لا يضمن Round-trip كاملًا للـMacros أو SmartArt أو التعليقات المتقدمة أو Themes الخاصة.
- مُصدّر XLSX الداخلي لا يولد الرسوم البيانية الغنية؛ يتطلب ذلك Backend الاختياري السليم.
- لا توجد مؤشرات تحرير جماعي حي أو Merge دلالي للخلايا والشرائح بعد.
- PDF يدعم التوليد والتحقق، وليس نظام Annotation احترافيًا كاملًا.
- لا توجد مثبتات Desktop موقعة بعد.

هذه الحدود مدرجة في تقييم الاكتمال ولا تُخفى تحت نجاح التصدير المرجعي.
