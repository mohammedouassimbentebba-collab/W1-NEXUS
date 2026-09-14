# Approval — W1-CIP v0.1

## الحالة

اعتمد `Approval` بوصفه سجلًا قانونيًا مثبت الإصدار لقرار موافقة أو رفض محدود الغرض. لا يمنح اسم الكيان سلطة عامة؛ بل تحدد الحمولة نوع الموافقة وأهدافها وصاحبها ونافذة سريانها وشروطها.

معرف مخطط الحمولة:

```text
urn:w1-cip:entity-payload:0.1:approval
```

## أنواع الموافقة

يدعم `v0.1` أربعة أنواع:

- `operation_authorization`: تفويض عملية واحدة دقيقة على هدف واحد ولمرة واحدة.
- `decision_approval`: موافقة أو رفض قرار مثبت؛ يستهلكه مخطط `Decision` لاحقًا.
- `risk_acceptance`: قبول بشري صريح لمخاطر اعتراضات غير محسومة ضمن حدود `GoalContract`.
- `editorial_correction`: اعتماد أن فرقًا محددًا غير دلالي، مع فحص تصنيف مثبت.

## البنية المرجعية

```yaml
approval_kind: operation_authorization
decision: approved
approved_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-decision-001
  entity_version: 1
decided_at: 2026-08-04T15:26:00Z
effective_from: 2026-08-04T15:26:00Z
expires_at: 2026-08-04T15:36:00Z
targets:
  - entity_type: contribution
    entity_id: proposal-driver-selection-001
    entity_version: 1
granted_to_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-executor-001
  entity_version: 1
authorization:
  operation: contribution.next_version
  target_condition:
    mode: next_version
    target:
      entity_type: contribution
      entity_id: proposal-driver-selection-001
      entity_version: 1
  usage_mode: one_time
conditions:
  - The revision must preserve the recorded evidence trail.
classification: internal
status: active
```

## التفويض التشغيلي

يجب أن يكون تفويض العملية:

1. مثبتًا لإسناد دور المستفيد، لا لمعرف طرف حر.
2. مطابقًا للعملية المشتقة من `ProtocolEnvelope`.
3. مطابقًا تمامًا لهوية هدف الإنشاء أو مرجع الإصدار السابق.
4. محدودًا زمنيًا.
5. أحادي الاستعمال في `v0.1`.

لا يجوز استعمال `operation_authorization` لتفويض إدارة:

- إسنادات الأدوار.
- عقد الهدف.
- خطة الفريق.
- منح السياق.
- الموافقات نفسها.

هذه العمليات تبقى تحت سلطة `RoleAssignment` مباشرة، كي لا تنشأ سلسلة تفويض ذاتية أو توسع غير مضبوط للحوكمة.

تتحقق طبقة الحالة من عدم استهلاك الموافقة سابقًا، ومن تطابق الطرف الفعلي للرسالة مع صاحب إسناد الدور المستفيد.

## الموافقة على القرار

`decision_approval` تسجل موافقة أو رفضًا لمرجع `Decision` مثبت. لا تجعل القرار معتمدًا وحدها؛ يجب على مخطط `Decision` لاحقًا التحقق من:

- موضوع القرار.
- سلطة صاحب الموافقة.
- شروط العقد.
- حالة الموافقة وسريانها.

## قبول المخاطر

`risk_acceptance` لا يسمح إلا للمالك البشري الذي يحمل قدرة `accept_risk`.

يجب أن:

- يستهدف اعتراضات مثبتة بالإصدار.
- يذكر الشدة المقبولة والتبرير والمخاطر المتبقية.
- يطابق سياسة `GoalContract.risk_policy.risk_acceptance`.
- لا يتجاوز الحد الأقصى للشدة المسموحة.
- لا يتجاوز أي محظور في عقد الهدف.

أصبحت حالة `Challenge.status: disclosed_unresolved` تحتاج موافقة `risk_acceptance` موجودة وحديثة وفعالة وتستهدف الاعتراض نفسه.

## التصحيح التحريري

عند استخدام:

```yaml
change_reason: editorial_correction
substantive_effect: none
```

يجب أن تكون `classification_approval` من النوع `editorial_correction`، وأن تطابق:

- الإصدار السابق المستهدف.
- `diff_artifact` نفسه.
- `classification_check` نفسه.

لا تكفي موافقة عامة أو موافقة على فرق آخر.

## جهة الإصدار والمهمة

ينشأ `Approval` من مهمة في مرحلة `approval` وحالة `in_progress`. يجب أن يكون `approved_by_role_assignment` هو مسؤول المهمة، وأن يملك:

```text
approval.create
approval.next_version
```

قواعد الأدوار:

- `operation_authorization` و`decision_approval`: `decision_authority` أو `human_owner`.
- `risk_acceptance`: `human_owner` مع `accept_risk`.
- `editorial_correction`: `reviewer` أو `verifier` أو `human_owner` مع `classify_editorial_correction`.

إذا كان صاحب الموافقة وكيلًا، فيجب أن تسمح `AgentCard.permissions.data_write` بكتابة `approval`.

## دورة الحياة

```text
active → revoked
```

- قرار الموافقة أو الرفض ومجاله وأهدافه وشروطه ثابتة عبر الإصدارات.
- السحب يصدر في إصدار تالٍ ولا يعدل السجل السابق.
- `revoked` نهائية.
- الرفض قرار نهائي ولا يتحول في الهوية نفسها إلى موافقة؛ يصدر قرار جديد بهوية جديدة.
- انتهاء `expires_at` مشتق زمنيًا ولا يحتاج تعديل السجل.

## الفصل بين الموافقة والاستهلاك

السجل القانوني يثبت قرار الموافقة. أما استعمال تفويض أحادي الاستخدام فتسجله حالة الجلسة وسجل الأحداث. لا يعدل إصدار الموافقة عند كل استعمال، لكن لا يقبل المخزن استعمالًا ثانيًا للمرجع نفسه.

## اختبارات التوافق

تشمل الاختبارات:

- مطابقة العملية والهدف والطرف الفعلي.
- انتهاء الصلاحية وعدم بدء السريان قبل القرار.
- منع إعادة الاستعمال.
- منع تفويض عمليات الحوكمة الحساسة.
- مطابقة جهة الإصدار للمهمة والدور والنطاق.
- تطبيق سياسة قبول المخاطر.
- مطابقة موافقة التصحيح التحريري للفرق والفحص.
- ثبات القرار ونهائية السحب.
