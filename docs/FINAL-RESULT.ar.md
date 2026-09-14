# FinalResult في W1-CIP v0.1

## 1. الغرض

`FinalResult` هو الإصدار المنشور الذي يلخص ما انتهت إليه الجلسة بالنسبة إلى `GoalContract`. لا يستبدل القرار أو الدليل أو التحقق أو المراجعة، ولا يعيد كتابة أي منها؛ بل يثبت مراجع إصداراتها ويعرض حالة الإنجاز والقيود وعدم اليقين ومسار الموارد وآخر نقطة موثوقة في سجل الأحداث.

المخطط القانوني:

```text
urn:w1-cip:entity-payload:0.1:final-result
```

وتأتي الهوية والإصدار والسلسلة من `VersionedEntity`.

## 2. حالة النتيجة وحالة النشر

يفصل الكيان بين نتيجتين مختلفتين:

### `result_status`

```text
succeeded
partially_succeeded
failed
blocked
cancelled
```

- `succeeded`: غطيت المخرجات المطلوبة ومعايير النجاح المطلوبة، ولا توجد دعاوى غير محسومة أو اعتراضات غير محلولة مخفية.
- `partially_succeeded`: تحقق جزء موثق من الهدف مع فجوات مصرح بها.
- `failed`: فشل مخرج أو معيار مطلوب بصورة صريحة.
- `blocked`: لم يتوفر مسار آمن لإكمال العمل، مثل نفاد جميع الموارد المؤهلة أو الحاجة إلى تدخل المستخدم.
- `cancelled`: أوقفت الجلسة قبل إكمال الهدف.

### `lifecycle_status`

```text
published
reassessment_required
superseded
withdrawn
```

حالة النشر لا تغير نتيجة الجلسة الأصلية. عند ظهور أساس مؤثر جديد، ينشأ إصدار تالٍ يحمل `reassessment_required` مع السبب والمحفز. أما النتيجة المعاد تركيبها فتكون هوية `FinalResult` جديدة، ثم يوسم الإصدار القديم `superseded` بمرجع النتيجة الجديدة.

## 3. تغطية عقد الهدف

يجب أن تحتوي النتيجة على تقييم واحد لكل:

- مخرج معلن في `GoalContract.deliverables`.
- معيار معلن في `GoalContract.success_criteria`.

### تقييم المخرج

```yaml
deliverable_id: driver-selection-decision
outcome: satisfied
rationale: The accepted decision selects candidate-b.
output_refs:
  - entity_type: decision
    entity_id: decision-motor-driver-001
    entity_version: 3
```

النتائج الممكنة:

```text
satisfied
partially_satisfied
unsatisfied
not_produced
not_applicable
```

### تقييم معيار النجاح

```yaml
criterion_id: compatible-driver-selected
outcome: satisfied
verification_refs:
  - entity_type: verification
    entity_id: verification-candidate-b-startup-001
    entity_version: 1
```

النتائج الممكنة:

```text
satisfied
partially_satisfied
not_satisfied
not_evaluated
```

لا يقبل `succeeded` إذا كان مخرج مطلوب غير مستوفى أو معيار نجاح غير مستوفى.

## 4. الادعاءات

يفصل `FinalResult` بين ثلاث فئات:

- `verified_claims`: ادعاءات يغطيها تحقق `passed + conclusive`.
- `refuted_claims`: ادعاءات يغطيها تحقق `failed + conclusive`.
- `unverified_claims`: ادعاءات لم تحسم، مع سبب مثل نقص الدليل أو عدم حسم التحقق أو عدم توفر المورد.

لا يجوز ظهور الادعاء نفسه في أكثر من فئة.

وجود ادعاء مدحوض لا يمنع نجاح الهدف إذا كان القرار النهائي لا يعتمد عليه، مثل دحض ملاءمة مرشح مرفوض.

## 5. المصادر المثبتة

يثبت الكيان إصدارات:

- القرارات المقبولة.
- المساهمات.
- الأدلة.
- عمليات التحقق.
- المراجعات.
- الاعتراضات المحلولة وغير المحلولة.
- الموافقات.

يجب أن تكون المصادر حالية وفعالة عند النشر. ولا يمكن أن يقل تصنيف النتيجة عن أعلى تصنيف بين مصادرها.

## 6. الموارد وتفاوت النماذج

يحتوي `resource_summary` على:

- إصدار `ExecutionResourcePlan` الذي حكم التوجيه.
- الحالة النهائية لحصة كل مورد.
- هل استعمل المورد أم لا.
- تحويلات المسار المعلنة.
- الحاجة إلى مراجعة إضافية.
- هل اكتمل كل العمل المطلوب.

مثال:

```yaml
routing_disclosures:
  - rule_id: material-execution-routing
    selected_resource_id: sol-resource
    fallback_from_resource_id: opus-resource
    activation_reason: quota_exhausted
    additional_review_required: true
```

لا يجوز إخفاء انتقال من نموذج قوي إلى بديل، أو وصف العمل بأنه مكتمل إذا أوقفته الحصة أو حد الجودة.

## 7. مرساة السجل

يثبت `log_anchor` آخر نقطة من السجل استعملت لبناء النتيجة:

```yaml
log_anchor:
  last_sequence: 39
  last_event_id: evt-execution-resource-plan-created-001
  event_count: 39
  projection_digest:
    algorithm: sha256
    value: <64 hex characters>
```

حدد [SessionStore](SESSION-STORE.ar.md) الترميز القانوني للأحداث وسلسلة SHA-256 والمعاملات الذرية. تبقى `projection_digest` بصمة للإسقاط المحدد وليست توقيعًا تشفيريًا؛ أما سلسلة المخزن فتكشف تغيير محتوى الأحداث أو ترتيبها من دون أن تثبت هوية الكاتب تشفيريًا.

مرساة المثال تستبعد حدث نشر `FinalResult` نفسه؛ فهي تصف السجل المصدر الذي بنيت منه النتيجة.

## 8. السلطة والمهمة

إنشاء أو تحديث حالة النشر يحتاج:

```text
final_result.create
final_result.next_version
```

والأدوار المسموحة:

```text
synthesizer
human_owner
```

يجب أن تكون المهمة في مرحلة `synthesis` وحالة `in_progress`، وأن يكون مولد النتيجة مسؤولها المثبت. يستطيع المركب التلخيص والإفصاح، لكنه لا يستطيع تغيير قرار مقبول أو تحويل تحقق فاشل إلى ناجح أو حذف قيد.

## 9. الإصدارات

تظل حمولة النشر الأصلية ثابتة عبر الإصدارات، بما فيها:

- حالة النتيجة والملخص.
- تقييمات المخرجات والمعايير.
- المصادر والقيود.
- ملخص الموارد.
- مرساة السجل.

يسمح الإصدار التالي فقط بتغيير حالة دورة النشر وإضافة بيانات إعادة التقييم أو الاستبدال أو السحب. إعادة حساب النتيجة بعد تغير جوهري تنشئ هوية جديدة.

## 10. الحالة المرجعية

النتيجة المرجعية للمضخة:

- اختارت `candidate-b` بقرار مقبول.
- ثبتت ادعاء ملاءمة `candidate-b`.
- دحضت ادعاء ملاءمة `candidate-a`.
- ثبتت الاعتراض الحرج المحلول.
- أفصحت عن انتقال التوجيه من المورد الأساسي بعد نفاد حصته.
- قيدت النتيجة بأنها تجربة تركيبية وليست اعتماد منتج حقيقي.
