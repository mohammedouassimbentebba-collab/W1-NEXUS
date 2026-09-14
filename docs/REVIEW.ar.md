# Review — W1-CIP v0.1

## 1. الغرض

يمثل `Review` تقييمًا شموليًا لمخرج مهمة أو حزمة حل مثبتة بالإصدارات. يراجع الالتزام بعقد الهدف، وتغطية المتطلبات، والاتساق الداخلي، والمخاطر والقيود، وكفاية الأدلة، وكفاية عمليات التحقق.

لا يحل `Review` محل `Verification`. عندما يتضمن نطاق المراجعة ادعاءً قابلًا لاختبار محدد، لا يجوز اعتماد الحل اعتمادًا على رأي المراجع وحده؛ يجب أن توجد `Verification` ناجحة ومثبتة لذلك الادعاء.

معرف مخطط الحمولة القانونية:

```text
urn:w1-cip:entity-payload:0.1:review
```

## 2. المرجع القانوني

يخزن كل تشغيل للمراجعة داخل `VersionedEntity` من النوع:

```yaml
entity_type: review
```

كل تشغيل جديد للمراجعة ينشئ هوية `Review` جديدة. لا يعاد تشغيل التقييم وتغيير النتيجة داخل الهوية القديمة. يسمح فقط بإصدار لاحق يسحب السجل، مع بقاء محتوى التشغيل الأصلي ثابتًا.

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

reviewed_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-reviewer-001
  entity_version: 1

reviewed_at: 2026-08-04T15:21:30Z

scope:
  kind: solution_package
  targets:
    - entity_type: contribution
      entity_id: proposal-driver-selection-001
      entity_version: 1
    - entity_type: contribution
      entity_id: claim-candidate-a-startup-001
      entity_version: 1
```

## 4. نطاق المراجعة

القيم المسموحة لـ`scope.kind`:

- `task_output`: مخرج أو مخرجات مهمة محددة.
- `solution_package`: حزمة مترابطة من المقترحات والادعاءات والأدلة والتحققات والاعتراضات.
- `entity_set`: مجموعة كيانات مثبتة يحددها عقد المراجعة صراحة.

كل هدف داخل `scope.targets` يستخدم `EntityRef` مثبتًا بالإصدار. لا يسمح بمرجع متحرك من نوع «أحدث إصدار» داخل سجل المراجعة.

إذا ظهر إصدار دلالي أحدث من هدف المراجعة قبل قبول السجل، يرفض المرجع القديم. أما التصحيح التحريري المعتمد ذو `substantive_effect: none` فيجوز تتبعه من دون تغيير الدلالة المثبتة.

## 5. معايير التقييم الإلزامية

يجب أن تحتوي كل مراجعة على تقييم واحد فقط لكل معيار من المعايير الستة:

```text
goal_alignment
requirement_coverage
internal_consistency
risk_and_limitations
evidence_sufficiency
verification_sufficiency
```

بنية التقييم:

```yaml
criterion: requirement_coverage
outcome: not_satisfied
severity: critical
rationale: The startup-current requirement is not satisfied.
related_refs:
  - entity_type: verification
    entity_id: verification-startup-current-001
    entity_version: 1
```

نتائج المعيار:

```text
satisfied
partially_satisfied
not_satisfied
not_applicable
```

إذا كان المعيار `satisfied` أو `not_applicable` تكون شدته `informational`. تمثل الشدة أثر القصور، لا درجة ثقة المراجع.

## 6. المراجع المستخدمة

تسجل المراجعة مراجع مثبتة إلى:

- `evidence`: الأدلة التي فحصها المراجع.
- `verifications`: عمليات التحقق التي اعتمد عليها.
- `challenges`: الاعتراضات المفتوحة أو المعالجة ذات الصلة.

وجود المرجع لا يعني أن المراجع تبناه بلا نقد. تشرح `criteria_assessments` و`summary` كيفية استعماله.

## 7. نتائج المراجعة

القيم المسموحة:

```text
approved
revision_required
rejected
escalation_required
```

### 7.1 approved

تعني أن النطاق المثبت اجتاز معايير المراجعة ضمن القيود المسجلة. يشترط:

- عدم وجود معيار `partially_satisfied` أو `not_satisfied`.
- عدم وجود اعتراض حرج غير محسوم بين الاعتراضات المرجعية.
- عدم وجود تحقق مرجعي نتيجته غير `passed`.
- وجود تحقق ناجح لكل ادعاء قابل للاختبار داخل النطاق.
- عدم وجود `required_actions`.

لا تعني `approved` اعتماد القرار النهائي؛ سلطة القرار مستقلة وتطبق لاحقًا في `Decision` و`Approval`.

### 7.2 revision_required

تعني وجود قصور قابل للتصحيح. يجب أن يوجد معيار ناقص واحد على الأقل وإجراء مطلوب واحد على الأقل:

```yaml
required_actions:
  - action_code: replace_candidate
    description: Replace the incompatible candidate.
    targets:
      - entity_type: contribution
        entity_id: proposal-driver-selection-001
        entity_version: 1
```

### 7.3 rejected

تعني أن النطاق المراجع غير صالح للاستمرار بصورته الحالية، ويجب أن يسجل قصورًا حرجًا من نوع `not_satisfied`. لا تعني حذف السجل أو منع إنشاء حل جديد.

### 7.4 escalation_required

تعني أن المراجع لا يملك سلطة الحسم أو أن المخاطرة تحتاج جهة أعلى. يجب تسجيل:

```yaml
escalation:
  reason: The unresolved issue requires human ownership.
  required_role: human_owner
  basis_refs: []
```

القيم المسموحة للدور المطلوب هي `decision_authority` و`human_owner`.

## 8. الاستقلال والصلاحية

لا تقبل المراجعة إلا عندما:

1. تكون المهمة في مرحلة `review` وحالتها `in_progress`.
2. يكون `reviewed_by_role_assignment` هو المسؤول المثبت للمهمة.
3. يحمل إسناد الدور دور `reviewer` ويكون حديثًا ونشطًا وساريًا.
4. يغطي نطاقه `review.create` أو `review.next_version`.
5. تسمح `AgentCard.permissions.data_write` بكتابة `review`.
6. يختلف الطرف الفعلي للمراجع عن مؤلف المساهمات المستهدفة.

لا يكفي اختلاف معرفات الأدوار إذا كان الوكيل أو الإنسان نفسه وراء الدورين.

## 9. العلاقة مع Verification

المراجعة تقييم للحل ككل، أما `Verification` فتطبق طريقة مسجلة على ادعاء محدد. لذلك:

- لا تتحول المراجعة البشرية إلى تحقق حتمي.
- لا يجوز اعتماد ادعاء `deterministic` أو `empirical` أو `source_based` داخل نطاق معتمد من دون `Verification` ناجحة تغطي إصداره المثبت.
- فشل التحقق أو عدم حسمه يمنع `approved` للنطاق الذي يعتمد عليه.
- يمكن أن تؤدي المراجعة إلى إنشاء `Challenge` بدل تغيير الادعاء أو التحقق.

## 10. الاعتراضات

وجود اعتراض حرج في الحالات الآتية يمنع `approved`:

```text
open
acknowledged
evidence_requested
escalated
reopened
disclosed_unresolved
```

لا تحل المراجعة الاعتراض تلقائيًا. تستخدم دورة `Challenge` وصلاحيات الحل المحددة فيها.

## 11. التصنيف

تصنيف المراجعة:

```text
public | internal | confidential | restricted
```

لا يجوز أن يكون أقل تقييدًا من أي هدف أو دليل أو تحقق أو اعتراض استُعمل في المراجعة. يمنع خفض التصنيف في إصدار لاحق.

## 12. دورة الحياة

```text
active | withdrawn
```

- يبدأ الإصدار الأول بـ`active`.
- يتطلب `withdrawn` سببًا.
- السحب نهائي.
- لا يجوز تغيير النطاق أو التقييمات أو النتيجة أو المراجع أو الملخص أثناء إصدار السحب.
- إعادة المراجعة بعد تعديل الحل تنشئ `Review` جديدة، لا تعديلًا للنتيجة القديمة.

## 13. مخرجات المهمة

يمكن للمراجعة تحقيق مخرج مهمة عبر:

```yaml
fulfills_output_id: startup-current-review
```

وعند إكمال المهمة يتحقق النظام من أن المخرج يشير إلى `Review` موجودة ونشطة، مرتبطة بالمهمة نفسها، ومطابقة لمعرف المخرج المطلوب.

## 14. المثال المرجعي

المثال المرجعي ينتج:

```text
result: revision_required
```

لأن الدليل المقاس كافٍ، والتحقق الحتمي فشل بصورة حاسمة، واعتراضًا حرجًا فُتح ضد ادعاء توافق المرشح الأول. لذلك يجب تعديل المقترح بدل اعتماده.
