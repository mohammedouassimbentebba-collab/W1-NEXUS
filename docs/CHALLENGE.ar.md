# Challenge — W1-CIP v0.1

## 1. الغرض

يمثل `Challenge` اعتراضًا معياريًا على إصدار مثبت من كيان داخل الجلسة. لا يعدل الاعتراض الكيان المستهدف، ولا يثبت خطأه تلقائيًا؛ بل يسجل طبيعة الخلل المزعوم، شدته، الإجراء المطلوب، من يملك الحسم، وكيف أغلقت الحالة أو أعيد فتحها.

يستطيع الاعتراض استهداف أي كيان معياري عبر `EntityRef`، مثل:

- مساهمة من النوع `claim`.
- دليل `Evidence`.
- تحقق `Verification` أو طريقته أو نتيجته.
- مهمة أو خطة فريق أو منحة سياق.
- إسناد دور أو موافقة أو قرار أو نتيجة نهائية عند اعتماد مخططاتها.

## 2. المخطط

معرف المخطط:

```text
urn:w1-cip:entity-payload:0.1:challenge
```

يخزن داخل `VersionedEntity` ذي:

```yaml
entity_type: challenge
```

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
  entity_id: task-review-startup-001
  entity_version: 4

raised_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-reviewer-001
  entity_version: 1
raised_at: 2026-08-04T15:21:00Z

last_transition_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-reviewer-001
  entity_version: 1
last_transition_at: 2026-08-04T15:21:00Z

target:
  entity_type: contribution
  entity_id: claim-candidate-a-startup-001
  entity_version: 1

category: constraint_violation
severity: critical
status: open
reason: The declared peak limit is below the measured startup-current requirement.
fulfills_output_id: startup-current-challenge

required_resolution:
  - action_code: replace_candidate
    description: Replace the incompatible candidate.
  - action_code: provide_counter_evidence
    description: Provide valid counter-evidence that changes the basis.

resolution_policy:
  allowed_roles: [reviewer, human_owner]
  challenger_may_accept_correction: true
  target_author_may_resolve_alone: false

classification: internal
limitations:
  - synthetic fixture only
```

## 4. الفئات والشدة

الفئات المعتمدة:

```text
claim_correctness
evidence_integrity
verification_method
verification_conclusion
constraint_violation
procedure
authority
context_use
completeness
other
```

درجات الشدة:

```text
informational | minor | major | critical
```

لا تعني شدة الاعتراض أنه صحيح؛ إنها تحدد أثر بقائه دون حل وسياسة التصعيد وإعادة التقييم.

## 5. دورة الحياة

```text
open → acknowledged | rejected | escalated
acknowledged → evidence_requested | resolved | rejected | escalated | disclosed_unresolved
evidence_requested → acknowledged | resolved | rejected | escalated | disclosed_unresolved
escalated → acknowledged | evidence_requested | resolved | rejected | disclosed_unresolved
resolved | rejected | disclosed_unresolved → reopened
reopened → acknowledged | evidence_requested | escalated
```

- `open`: اعتراض جديد لم يبدأ التعامل معه بعد.
- `acknowledged`: تم استلامه والاعتراف بوجوب معالجته.
- `evidence_requested`: يتطلب الحسم دليلًا أو تحققًا إضافيًا.
- `resolved`: عولج الخلل أو قبل المعترض الإجراء التصحيحي.
- `rejected`: قررت جهة مستقلة مخولة أن الاعتراض غير صالح أو خارج النطاق.
- `escalated`: أحيل إلى جهة أعلى؛ لا تعني الحالة حل الاعتراض.
- `disclosed_unresolved`: بقي الاعتراض دون حل مع إفصاح وقبول مخاطر بشري صالح.
- `reopened`: أعيد فتح حالة مغلقة بسبب أساس جديد مسجل.

كل انتقال ينشئ إصدارًا جديدًا كاملًا من `Challenge`، ويثبت منفذ الانتقال ووقته. لا تعدل الإصدارات السابقة.

## 6. سلطة الإنشاء والتحديث

الإصدار الأول يحتاج:

```text
challenge.create
```

والإصدارات التالية تحتاج:

```text
challenge.next_version
```

يجب أن يطابق `authorized_by` الحقل:

```text
last_transition_by_role_assignment
```

وفي الإصدار الأول يجب أن يتطابق أيضًا مع `raised_by_role_assignment` ومسؤول مهمة الاعتراض.

لا تمنح أسماء الأدوار سلطة ضمنية؛ يجب أن يسجل `RoleAssignment.authority_scope.operations` العملية صراحة.

## 7. قواعد الحل والاستقلال

لا يستطيع مؤلف الكيان المستهدف إغلاق الاعتراض وحده، حتى إذا حصل على اسم عملية التحديث.

يقبل `resolved` بإحدى طريقتين:

1. يقبل المعترض الإجراء التصحيحي، إذا سمحت السياسة بـ`challenger_may_accept_correction`.
2. تحسم جهة مستقلة تحمل دورًا مدرجًا في `resolution_policy.allowed_roles`.

يتطلب `rejected` جهة مستقلة مخولة؛ لا يستطيع المعترض رفض اعتراضه بنفسه، ولا يستطيع مؤلف الهدف فعل ذلك منفردًا.

يتطلب `disclosed_unresolved`:

- منفذ انتقال بدور `human_owner`.
- مرجع `Approval` مثبتًا في `risk_acceptance_approval`.
- إفصاحًا صريحًا عن بقاء الاعتراض دون حل.

لا يحول قبول المخاطر الحالة إلى `resolved`.

## 8. إعادة الفتح

يتطلب `reopened` كائنًا يحدد:

```yaml
reopening:
  reason: procedural_defect
  reopened_by_role_assignment:
    entity_type: role_assignment
    entity_id: role-assignment-reviewer-001
    entity_version: 1
  reopened_at: 2026-08-04T15:25:00Z
  basis_refs:
    - entity_type: verification
      entity_id: verification-startup-current-001
      entity_version: 1
```

الأسباب المسموحة:

```text
new_evidence
procedural_defect
authority_invalidated
material_target_change
```

لا يسمح بإعادة فتح النقاش من دون أساس جديد مثبت.

إذا أعيد فتح اعتراض `critical`، ينتج المدقق إشارة إلزامية لإعادة تقييم القرارات التي تعتمد فعليًا على هدفه عبر الاعتماديات المسجلة. تنفيذ نشر الأثر على رسم القرارات يكتمل مع مخطط `Decision`.

## 9. الهدف والتصنيف

يجب أن يكون الهدف موجودًا ومثبت الإصدار. عند إنشاء اعتراض جديد، يرفض استهداف إصدار تجاوزه تغيير دلالي. يمكن إبقاء مرجع أقدم إذا كانت الإصدارات اللاحقة تصحيحات تحريرية معتمدة ذات `substantive_effect: none`.

لا يجوز أن يكون تصنيف الاعتراض أقل تقييدًا من تصنيف الهدف إذا كان الهدف يحمل تصنيفًا معياريًا.

تتحقق بعض الفئات من نوع الهدف، مثل:

- `claim_correctness` يستهدف `Contribution` من النوع `claim`.
- `evidence_integrity` يستهدف `Evidence`.
- `verification_method` و`verification_conclusion` تستهدفان `Verification`.

## 10. ربط مخرجات المهمة

يمكن للاعتراض أن يحمل:

```yaml
fulfills_output_id: startup-current-challenge
```

عندما يكون الاعتراض مخرجًا مطلوبًا من المهمة. ويتحقق النظام عند إكمال المهمة من وجود إصدار الاعتراض المشار إليه، وارتباطه بالمهمة نفسها، ومطابقة معرف المخرج.

لا يصبح كل اعتراض مخرجًا إلزاميًا؛ يبقى الحقل اختياريًا لأن بعض الاعتراضات قد تنشأ كأثر جانبي أثناء إنتاج `Review` أو `Verification`.

## 11. الفصل عن الكيانات الأخرى

- `Challenge` لا يثبت الادعاء أو ينفيه؛ ذلك دور `Verification` والأدلة.
- `Review` قد ينشئ اعتراضات، لكنه يقيم الحل ككل.
- `Approval` يسجل قبول المخاطر أو الموافقة البشرية، ولا يحول الاعتراض غير المحسوم إلى محلول.
- `Decision` يجب أن يبين الاعتراضات المعالجة وغير المحسومة، وأن يستجيب لإعادة فتح الاعتراضات الحرجة التابعة لأساسه.

## 12. رموز أخطاء رئيسية

```text
challenge_initial_status_not_open
challenge_status_transition_invalid
challenge_target_not_found
challenge_target_version_not_current
challenge_target_author_cannot_close_alone
challenge_rejection_not_independent
challenge_resolver_not_authorized
challenge_risk_acceptance_requires_human_owner
challenge_classification_below_target
challenge_transition_actor_scope_denied
challenge_authority_ref_mismatch
```

## موافقة المخاطر الفعلية

لا يكفي وجود مرجع `risk_acceptance_approval` بنيويًا. تتحقق طبقة الحالة من أن الموافقة موجودة وحديثة وفعالة ومن النوع `risk_acceptance`، وأنها تستهدف الاعتراض نفسه وتطابق عقد الهدف وخطة الفريق.
