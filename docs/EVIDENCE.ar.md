# Evidence — W1-CIP v0.1

## 1. الغرض

يمثل `Evidence` عنصرًا مسجلًا يربط مصدرًا مثبتًا بإصدار محدد من مساهمة من النوع `claim`. لا يعني إنشاء الدليل أن الادعاء صحيح أو متحقق؛ فهو يصف علاقة المصدر بالادعاء ويثبت منشأه وسلامته المعروفة وقيوده.

يحفظ الدليل داخل `VersionedEntity` من النوع:

```yaml
entity_type: evidence
```

ومخطط حمولته القانونية:

```text
urn:w1-cip:entity-payload:0.1:evidence
```

## 2. الفرق بين المصدر والدليل والتحقق

- **المصدر** هو الملف أو السجل أو الملاحظة أو المرجع الذي نشأت منه المعلومة.
- **الدليل** هو سجل يثبت المصدر ويربطه بادعاء محدد بعلاقة `supports` أو `refutes` أو `contextualizes`.
- **التحقق** عملية لاحقة تطبق طريقة معلنة على الادعاء ومدخلاته وأدلته وتنتج `passed` أو `failed` أو نتيجة غير حاسمة.

لا يحول الرابط أو رأي نموذج أو رقم داخل نص إلى دليل مقاس أو محسوب تلقائيًا.

## 3. البنية الأساسية

```yaml
goal:
  entity_type: goal_contract
  entity_id: goal-pump-driver-001
  entity_version: 1

team_plan:
  entity_type: team_plan
  entity_id: team-plan-pump-001
  entity_version: 1

task:
  entity_type: task
  entity_id: task-startup-evidence-001
  entity_version: 4

registered_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-verifier-001
  entity_version: 1

evidence_type: measured_result
relation: refutes

targets:
  - entity_type: contribution
    entity_id: claim-candidate-a-startup-001
    entity_version: 1

summary: >
  The pinned capture reports an 18 A startup peak for 0.8 s,
  exceeding candidate-a's declared 15 A peak limit.

source:
  uri: artifact://fixtures/startup-current-capture.csv
  source_version: 1
  description: Synthetic startup-current capture.
  classification: internal
  capture:
    kind: measurement
    performed_by:
      principal_type: tool
      principal_id: test-bench-01
    performed_at: 2026-08-04T15:20:00Z
    procedure_ref: fixture://procedures/startup-current-v1

collected_at: 2026-08-04T15:20:00Z

integrity:
  status: verified
  basis: artifact version matches the immutable session fixture record
  assessed_by_role_assignment:
    entity_type: role_assignment
    entity_id: role-assignment-verifier-001
    entity_version: 1
  assessed_at: 2026-08-04T15:20:01Z

classification: internal
status: active
limitations:
  - single synthetic capture
```

## 4. أنواع الأدلة

القيم المعتمدة:

```text
user_provided
document_source
tool_observation
measured_result
computed_result
model_inference
external_reference
human_confirmation
```

يرتبط كل نوع بـ`source.capture.kind` محدد:

| evidence_type | capture.kind |
|---|---|
| `user_provided` | `user_submission` |
| `document_source` | `document_lookup` |
| `tool_observation` | `tool_invocation` |
| `measured_result` | `measurement` |
| `computed_result` | `calculation` |
| `model_inference` | `model_inference` |
| `external_reference` | `external_lookup` |
| `human_confirmation` | `human_confirmation` |

يحتاج `tool_observation` إلى أداة ومعرف استدعاء. ويحتاج `measured_result` إلى مرجع إجراء. ويحتاج `computed_result` إلى الإجراء والمدخلات المثبتة. ولا يجوز تسجيل `model_inference` كقياس أو حساب لمجرد احتوائه على أرقام.

## 5. الأهداف والعلاقة

القيم المسموحة للعلاقة:

```text
supports | refutes | contextualizes
```

يجب أن يكون كل هدف:

- `EntityRef` مثبتًا.
- من النوع `contribution`.
- مساهمة من النوع الداخلي `claim`.
- نشطًا.
- من عقد الهدف وخطة الفريق نفسيهما.

لا يقبل النظام استهداف مقترح أو سؤال أو توصية بوصفه ادعاءً. وإذا ظهر إصدار دلالي أحدث من الادعاء، يجب إنشاء دليل جديد أو إعادة ربطه بالإصدار الحالي. يسمح ببقاء المرجع القديم فقط إذا كانت الإصدارات اللاحقة تصحيحات تحريرية معتمدة ذات `substantive_effect: none`.

## 6. المصدر والتثبيت

يجب أن يسجل المصدر:

- `uri`.
- `source_version` صريحًا.
- وصفًا.
- تصنيفًا.
- كيفية التقاطه ومن قام بذلك ووقت الالتقاط.

لا تستخدم قيمة متحركة مثل «أحدث ملف». وإذا تعذر الاحتفاظ بمحتوى المصدر الحساس، يحتفظ السجل بمرجع وإصدار وبيانات حجب مجردة وفق سياسة الجلسة، ولا ينسخ المحتوى الحساس إلى سجل التدقيق.

## 7. سلامة الدليل

حالات السلامة:

```text
unverified | verified | disputed | unavailable
```

تعني السلامة نسبة العنصر إلى مصدره وثبات النسخة، لا صحة الادعاء أو الاستنتاج.

- `verified` يحتاج أساسًا مسجلًا، ومقيّمًا مخولًا، ووقت تقييم.
- `disputed` يحتاج سبب الخلاف.
- `unavailable` يحتاج سبب عدم الإتاحة.
- لا يجوز أن يسبق تقييم السلامة وقت جمع المصدر.

صلاحية تقييم السلامة هي:

```text
evidence.assess_integrity
```

## 8. المهمة والمُسجل

يجب أن يكون مسجل الدليل:

- مسؤول المهمة المثبت.
- صاحب `RoleAssignment` نشطة وسارية.
- مخولًا بـ`evidence.create` أو `evidence.next_version`.
- مؤهلًا عبر `AgentCard` عند كونه وكيلًا.
- قادرًا على كتابة `evidence` وفق سياسة البطاقة.

تقبل الأدلة في مهام مراحل:

```text
planning | execution | review | verification | revision
```

ولا تقبل في `approval` أو `synthesis`. ويجب أن تكون المهمة `in_progress`.

## 9. التصنيف

التصنيفات:

```text
public | internal | confidential | restricted
```

لا يمكن أن يكون تصنيف سجل الدليل أقل تقييدًا من تصنيف المصدر. ولا يسمح بخفض التصنيف في إصدار لاحق؛ فك التصنيف يحتاج آلية مستقلة مؤجلة.

## 10. دورة الحياة والإصدارات

الحالات:

```text
active | withdrawn
```

- يبدأ الإصدار الأول بـ`active`.
- السحب يحتاج `withdrawal_reason`.
- السحب نهائي.
- لا يجوز تغيير محتوى الدليل بالتزامن مع سحبه.
- تبقى هوية المصدر والأهداف والعلاقة والمهمة والمُسجل ووقت الجمع ثابتة عبر إصدارات الدليل.
- تغيير المصدر أو الهدف أو نوع الدليل ينشئ هوية دليل جديدة، لا إعادة تعريف للدليل السابق.

## 11. ما لا يثبته Evidence

لا يثبت `Evidence` بمفرده:

- أن الادعاء صحيح.
- أن طريقة القياس أو الحساب صالحة للنطاق.
- أن الاستنتاج المستخرج من المصدر صحيح.
- أن القرار يمكن اعتماده.
- أن الاعتراض أغلق.

تتولى `Verification` صلاحية الطريقة ونتيجتها، وتستخدم `Review` و`Challenge` و`Decision` الدليل وفق دوراتها المستقلة.
