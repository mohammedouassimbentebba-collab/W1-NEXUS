# GoalContract — W1-CIP v0.1

## الحالة

`GoalContract` هو الحمولة القانونية المصدرة التي تثبت الهدف قبل تشكيل الفريق أو بدء التنفيذ. يحفظ داخل `VersionedEntity` من النوع `goal_contract`، ويعد المصدر الملزم للهدف والنطاق والمخرجات والقيود ومعايير النجاح والمحظورات وسياسة المخاطر وسلطة القرار.

معرف مخطط الحمولة:

```text
urn:w1-cip:entity-payload:0.1:goal-contract
```

## المبدأ الحاكم

لا يكفي وصف المهمة بلغة عامة. يجب أن يستطيع النظام الإجابة آليًا عن الأسئلة الآتية:

- ما المطلوب إنتاجه؟
- ما الذي يدخل النطاق وما الذي يخرج منه؟
- ما القيود التي يجب احترامها؟
- كيف يحكم على النجاح؟
- ما الأفعال أو النتائج المحظورة؟
- ما مستوى المخاطر وحدود الاستقلال؟
- من يملك اعتماد كل فئة قرار؟

لا يجوز لـ`TeamPlan` أو `Task` أو `Decision` توسيع الهدف أو تجاوز محظوراته. إذا احتاج العمل إلى تغييرها، ينشأ إصدار جديد كامل من `GoalContract` وتنتشر إعادة التقييم عبر الاعتماديات المسجلة.

## البنية القانونية

مثال مختصر:

```yaml
title: Select a compatible motor driver
objective: Select one candidate that satisfies the supplied electrical constraints.
scope:
  in_scope:
    - Evaluate only the synthetic candidates in the fixture.
  out_of_scope:
    - Real product certification.
deliverables:
  - deliverable_id: driver-selection-decision
    description: An approved compatible-driver decision.
    kind: decision
    decision_subject: motor_driver_selection
    required: true
constraints:
  - constraint_id: startup-current
    statement: Peak capability must cover 18 A for 0.8 s.
    criticality: critical
    verifiability: deterministic
success_criteria:
  - criterion_id: compatible-driver-selected
    statement: The selected candidate passes the required checks.
    applies_to: [driver-selection-decision]
    verifies_constraints: [startup-current]
    verifiability: deterministic
    acceptance_effect: required_for_completion
prohibitions:
  - prohibition_id: no-real-product-claim
    statement: Do not present the fixture result as real certification.
risk_policy:
  risk_level: medium
  error_cost: material
  autonomy_mode: human_approval_required
  risk_acceptance:
    allowed: false
    max_unresolved_challenge_severity: none
decision_policy:
  - decision_subject: motor_driver_selection
    authority:
      mode: team_plan
      required_role: decision_authority
    human_approval_required: false
    independent_from_roles: [executor]
status: active
```

## المخرجات

كل عنصر في `deliverables` يحمل معرفًا جلسيًا داخل العقد ووصفًا ونوعًا وحقل `required`.

الأنواع هي:

```text
decision | report | artifact | dataset | code | other
```

إذا كان النوع `decision` يجب تسجيل `decision_subject`، ويجب أن يوجد الموضوع نفسه داخل `decision_policy`. يمنع هذا وجود مخرج قرار لا يعرف النظام من يملك اعتماده.

كل مخرج مطلوب يجب أن تغطيه على الأقل قاعدة نجاح ذات `acceptance_effect: required_for_completion`.

## القيود ومعايير النجاح

درجات القيد هي:

```text
required | critical
```

وتصنيفات قابلية التحقق المستخدمة في العقد هي:

```text
deterministic | empirical | source_based | human_judgment
```

يحدد معيار النجاح المخرجات التي يطبق عليها عبر `applies_to`، والقيود التي يفحصها عبر `verifies_constraints`. يجب أن يغطي كل قيد حرج معيار نجاح مطلوب؛ فلا يجوز إعلان اكتمال العقد مع قيد حرج لا توجد طريقة مسجلة لتقييمه.

قيمة `acceptance_effect` هي:

- `required_for_completion`: يجب حسم المعيار وفق السياسة قبل وصف النتيجة بأنها مكتملة.
- `informational`: يظهر في النتيجة لكنه لا يكفي وحده لمنع الاكتمال.

لا تعني كتابة `method_hint` أن طريقة التحقق أصبحت صالحة؛ طريقة `Verification` وإصدارها وأدلتها تبقى كيانات مستقلة.

## المحظورات وقبول المخاطر

`prohibitions` محظورات صلبة في `v0.1`. لا تحتوي حقلًا مثل `waivable`، ولا يستطيع `accept_risk` أو `human_owner` تجاوزها. إذا لم تعد مناسبة، يجب إصدار نسخة جديدة من العقد قبل مواصلة العمل.

أما `risk_acceptance` فيحدد فقط هل يمكن الإفصاح عن اعتراضات غير محسومة ضمن نتيجة مقيدة، وأقصى شدة تسمح بها السياسة. عندما تكون `allowed: false` يجب أن تكون الشدة القصوى `none`.

## سياسة المخاطر والاستقلال

مستويات المخاطر:

```text
low | medium | high
```

تكلفة الخطأ:

```text
negligible | minor | material | severe
```

أنماط الاستقلال:

```text
bounded_autonomy | human_approval_required | advisory_only
```

العقد عالي المخاطر يجب أن يستخدم `advisory_only`، وأن يجعل `human_approval_required: true` لكل فئة قرار. يستطيع الوكلاء إعداد التحليل والأدلة، لكن لا يعتمدون النتيجة بأنفسهم.

## سلطة القرار

لكل `decision_subject` واحد من نمطين:

### سلطة مثبتة

```yaml
authority:
  mode: pinned_assignment
  required_role: decision_authority
  role_assignment:
    entity_type: role_assignment
    entity_id: role-assignment-decision-001
    entity_version: 1
```

يجب أن يكون الإسناد موجودًا وحديثًا ونشطًا وصالحًا زمنيا، وأن يحمل دور `decision_authority` وأن يدرج الموضوع في `authority_scope.decision_subjects`.

### يحسمها TeamPlan

```yaml
authority:
  mode: team_plan
  required_role: decision_authority
```

يعني ذلك أن العقد حدد فئة السلطة المطلوبة، لكن `TeamPlan` يجب أن يثبت الإسناد الفعلي قبل بدء التنفيذ. إذا بقيت السلطة غير محسومة ينتقل العمل إلى `approval/blocked/awaiting_user`.

`independent_from_roles` يعلن متطلبات الاستقلال، لكن فحص أن الشخص نفسه لا يحمل دورًا متعارضًا يحتاج حالة جميع إسنادات الجلسة ويطبق أثناء اعتماد `TeamPlan` أو القرار.

## دورة الحياة والإصدارات

الحالات:

```text
active | suspended | fulfilled | cancelled
```

- يجب أن يبدأ الإصدار الأول بحالة `active`.
- الحالات غير النشطة تحتاج `status_reason`.
- `fulfilled` و`cancelled` نهائيتان؛ لا ينشأ بعدهما إصدار جديد للعقد نفسه.
- أي تغيير في الهدف أو النطاق أو المخرجات أو القيود أو المعايير أو المحظورات أو المخاطر أو سلطة القرار هو تغيير في الحمولة القانونية ويحتاج إصدارًا جديدًا.
- يبقى كل قرار ومهمة مرتبطًا بإصدار العقد الذي اعتمد عليه.

## الصلاحية

عمليتا:

```text
goal_contract.create
goal_contract.next_version
```

مقصورتان على `RoleAssignment` يحمل دور `human_owner`. يجوز للـRuntime أو المنظم تسجيل الإجراء نيابة عن المالك، لكن `on_behalf_of` و`authorized_by` يجب أن يثبتا أن تغيير الهدف صدر بسلطته.

## قواعد خارج JSON Schema

ينفذ المدقق الدلالي:

- منع تكرار معرفات المخرجات والقيود والمعايير والمحظورات وموضوعات القرار.
- التحقق من مراجع المخرجات والقيود داخل معايير النجاح.
- إلزام كل مخرج مطلوب بمعيار نجاح مطلوب.
- إلزام كل قيد حرج بمعيار نجاح مطلوب.
- ربط مخرجات القرار بموضوعات القرار المعلنة.
- فرض `advisory_only` والموافقة البشرية للعقود عالية المخاطر.
- التحقق من إسنادات القرار المثبتة.
- منع إعادة فتح عقد حالته `fulfilled` أو `cancelled`.

## ما لا يثبته العقد

العقد لا يثبت أن الهدف ممكن، ولا أن القيود صحيحة، ولا أن معيار النجاح مر بالفعل. تلك النتائج تأتي من الأدلة والتحقق والمراجعة والقرار و`FinalResult`.

## 13. التكامل مع TeamPlan

كل فئة قرار تستخدم `authority.mode: team_plan` يجب أن يحسمها [TeamPlan](TEAM-PLAN.ar.md) مرة واحدة قبل التنفيذ. تتحقق الخطة من أن الإسناد بدور `decision_authority`، وأن نطاقه يشمل موضوع القرار، وأنه مستقل عن الأدوار المحددة في `independent_from_roles`. إذا اشترط العقد موافقة بشرية، يجب أن يكون صاحب سلطة القرار من النوع `human`.
