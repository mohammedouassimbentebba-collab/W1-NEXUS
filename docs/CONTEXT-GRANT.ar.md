# ContextGrant — W1-CIP v0.1

## 1. الغرض

يمثل `ContextGrant` إذنًا قانونيًا صريحًا ومحدودًا يسمح لإسناد دور واحد بالوصول إلى حقول سياق محددة من أجل مهمة وغرض محددين. عضوية الطرف في `TeamPlan` لا تمنحه حق الوصول تلقائيًا.

يحفظ المنح داخل `VersionedEntity` من النوع `context_grant`. لا تخزن قيم السياق نفسها داخل المنحة؛ بل تسجل أسماء الحقول وحدود استعمالها فقط، كي لا يتحول سجل التدقيق إلى نسخة إضافية من البيانات الحساسة.

معرف مخطط الحمولة:

```text
urn:w1-cip:entity-payload:0.1:context-grant
```

## 2. البنية الأساسية

```yaml
recipient_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-executor-001
  entity_version: 1

team_plan:
  entity_type: team_plan
  entity_id: team-plan-pump-001
  entity_version: 1

task:
  entity_type: task
  entity_id: task-driver-selection-001
  entity_version: 1

fields:
  - available_supply_voltage
  - user_skill_level

purpose:
  code: adapt_component_explanation
  description: Use only the listed fields to evaluate compatibility and adapt the explanation.

classification: internal
processing_mode: external_allowed
onward_sharing: prohibited
valid_from: 2026-08-04T15:12:00Z
expires_at: 2026-08-04T16:00:00Z
revocable: true
usage_logging: required
reliance_policy: historical_reliance_allowed
status: active
```

## 3. المستلم وخطة الفريق

يستخدم `recipient_role_assignment` مرجعًا مثبتًا إلى `RoleAssignment`. عند اعتماد المنحة يجب أن يكون الإسناد:

1. موجودًا وأحدث إصدار فعّال.
2. بالحالة `active` وساريًا زمنيا.
3. عضوًا فعليًا في `TeamPlan` المثبتة.
4. مدعومًا ببطاقة وكيل صالحة إذا كان صاحبه من النوع `agent`.

إذا ظهر إصدار دلالي أحدث من `TeamPlan` تحتاج المنحة إلى إعادة تقييم. أما التصحيح التحريري المعتمد ذي `substantive_effect: none` فلا يبطل المنحة وحده.

## 4. ارتباط المهمة

كل منحة ترتبط بمرجع مثبت إلى [Task](TASK.ar.md) موجودة. يتحقق النظام من ارتباط المهمة بالخطة نفسها، ومن أن المستلم هو المسؤول الحالي المثبت للمهمة، ومن أن الحقول الممنوحة مجموعة جزئية من `required_context_fields` التي أعلنتها المهمة.

تبقى المنحة مثبتة إلى إصدار المهمة الذي عرّف نطاقها، بينما يفحص المدقق أحدث إصدار لمعرفة الحالة والمسؤول الحاليين. لا يبطل انتقال الحالة العادي المنحة تلقائيًا، لكن `completed` أو `failed` أو `cancelled` توقف استعمالها. كما يرفض الاستعمال إذا تغير المسؤول عن مستلم المنحة.

لا يجوز استعمال منحة مرتبطة بمهمة في مهمة أخرى حتى لو كانت الحقول والغرض متشابهين.

## 5. تقليل البيانات والغرض

`fields` قائمة سماح مغلقة. يمنع Runtime أي حقل غير مدرج قبل بناء مدخل الوكيل. لا تسمح المنحة بالوصول إلى «السياق كله» أو إلى مسار متحرك غير مسجل.

يحمل `purpose`:

- `code`: معرفًا آليًا ثابتًا للغرض.
- `description`: وصفًا بشريًا يوضح لماذا يحتاج المستلم هذه الحقول.

عند الاستعمال يجب أن يطابق الغرض المسجل. لا يجوز إعادة استخدام الحقول لغرض آخر اعتمادًا على المنحة نفسها.

## 6. التصنيف وسياسة AgentCard

التصنيفات المدعومة:

```text
public | internal | confidential | restricted
```

يجب أن يوجد التصنيف داخل `AgentCard.data_policy.accepted_classifications` للمستلم. كما يجب أن تحتوي البطاقة على:

```yaml
permissions:
  data_read:
    - granted_context
```

`processing_mode` هو:

```text
local_only | external_allowed
```

إذا كانت بطاقة الوكيل تعلن `external_processing: true` فلا يجوز استعمال المنحة إلا إذا كانت `processing_mode: external_allowed`. هذا الإذن لا يغير سياسة المزود ولا يثبت أمانه؛ بل يمنع إرسال بيانات إلى معالجة خارجية دون تصريح صريح.

## 7. إعادة المشاركة

القيم المسموحة:

```text
prohibited | separate_grant_required
```

- `prohibited`: لا يجوز للمستلم تمرير الحقول إلى طرف آخر.
- `separate_grant_required`: لا تمنح مشاركة عامة؛ يجب إنشاء منحة مستقلة لكل مستلم تالٍ، بمهمته وغرضه وحقوله ومدته.

تظل سياسة `AgentCard.onward_sharing` قيدًا إضافيًا. القيمة الأكثر تقييدًا بين البطاقة والمنحة هي التي تطبق.

## 8. المدة ودورة الحياة

يجب أن يكون:

```text
expires_at > valid_from
```

ولا يجوز أن تبدأ المنحة قبل بداية صلاحية إسناد المستلم، أو تنتهي بعد `valid_until` لذلك الإسناد.

الحالات:

```text
active | suspended | revoked
```

- يبدأ الإصدار الأول بـ`active`.
- `suspended` توقف مؤقت يحتاج `status_reason`.
- `revoked` سحب نهائي؛ لا يعاد تنشيط المنحة نفسها بعده.
- `revocable` ثابتته `true` في `v0.1`.
- انتهاء `expires_at` يوقف الاستعمال حتى لو بقي آخر إصدار بالحالة `active`.

تبقى هوية المستلم والخطة والمهمة والغرض و`valid_from` ثابتة عبر إصدارات المنحة. إذا تغير أحدها ينشأ `ContextGrant` جديد بدل إعادة تعريف المنحة القديمة.

## 9. الاعتماد على البيانات بعد السحب

`reliance_policy` لها قيمتان:

```text
historical_reliance_allowed
continuous_validity_required
```

- `historical_reliance_allowed`: يمنع السحب الوصول المستقبلي، لكنه لا يبطل تلقائيًا قرارات سابقة استعملت البيانات بصورة مشروعة.
- `continuous_validity_required`: استمرار صلاحية المنحة شرط مسجل للاعتماد؛ تعليقها أو سحبها أو انتهاءها قد يولد `decision.reassessment_required` عبر الاعتماديات المسجلة.

لا يعدل القرار القديم أو الحدث القديم بصمت. يسجل السحب وإعادة التقييم بأحداث بروتوكولية جديدة.

## 10. سجل الاستعمال

`usage_logging: required` قاعدة ثابتة. كل استعمال ناجح أو مرفوض يجب أن ينتج أثرًا تسجيليًا لا يحتوي القيم الحساسة نفسها، مثل:

- مرجع المنحة.
- مرجع المهمة.
- المستلم.
- أسماء الحقول أو ملخصًا مجردًا بحسب التصنيف.
- وقت الاستعمال ونتيجته.

سيثبت نوع حدث الاستعمال التفصيلي مع مسار التنفيذ المرجعي. لا يسمح غياب نوع الحدث النهائي بتجاوز تسجيل الاستعمال.

## 11. صلاحية إدارة المنح

عمليتا:

```text
context_grant.create
context_grant.next_version
```

مقصورتان في `v0.1` على دور `human_owner` مع نطاق عملية صريح. يستطيع Runtime أو خدمة منظم تسجيل الفعل نيابة عن الإنسان، لكن يجب أن يطابق `authorized_by` الطرف الفعلي الممثل.

هذا القرار محافظ في النسخة الأولى. تفويض المنظم تلقائيًا لإصدار منح ضمن سياسة مسبقة يمكن بحثه لاحقًا بعد وجود كتالوج بيانات وسياسة موافقات مستقلة.

## 12. تحقق الاستعمال

قبل إدخال أي سياق إلى وكيل، يفحص المدقق:

1. وجود المنحة وإصدارها الحالي.
2. حالتها ونافذة الزمن.
3. تطابق `recipient_role_assignment`.
4. تطابق `task`.
5. تطابق `purpose.code`.
6. كون الحقول المطلوبة مجموعة جزئية من `fields`.
7. توافق التصنيف والمعالجة مع `AgentCard`.
8. عضوية المستلم في `TeamPlan`.
9. وجود `Task` وتطابق خطتها ومسؤولها وحقول السياق المطلوبة وحالتها الحالية.

من رموز الرفض:

```text
context_grant_not_found
context_grant_version_not_current
context_grant_inactive
context_grant_not_yet_valid
context_grant_expired
context_grant_recipient_mismatch
context_grant_task_mismatch
context_grant_purpose_mismatch
context_grant_field_not_allowed
context_grant_classification_not_accepted
context_grant_external_processing_not_allowed
context_grant_task_not_found
context_grant_field_not_required_by_task
context_grant_task_recipient_mismatch
context_grant_task_terminal
```

## 13. التكامل

ترتيب **إنشاء الكيانات أثناء التشغيل** هو:

```text
GoalContract
→ TeamPlan
→ Task created
→ ContextGrant issued for that Task
→ Task assigned or executed with granted context
```

فالمنحة تحمل مرجعًا مثبتًا إلى مهمة موجودة، ولا يجوز إنشاء مرجع وهمي إلى مهمة مستقبلية. أصبح هذا الترتيب مفروضًا الآن بواسطة فحوص `TaskState`.
