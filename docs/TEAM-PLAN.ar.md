# TeamPlan — W1-CIP v0.1

## 1. الغرض

يمثل `TeamPlan` القرار القانوني لتشكيل الفريق قبل بدء التنفيذ. يثبت إصدارًا محددًا من `GoalContract`، والتقييم الذي بني عليه التشكيل، والميزانية، والحد الأدنى للأدوار، وإسنادات الأدوار الفعلية، وقواعد الاستقلال، وسلطات القرار التي أجّلها عقد الهدف.

يحفظ العقد داخل `VersionedEntity` من النوع `team_plan`، ولا يكرر معرف الخطة أو رقم إصدارها داخل الحمولة القانونية.

معرف مخطط الحمولة:

```text
urn:w1-cip:entity-payload:0.1:team-plan
```

## 2. المرجع إلى عقد الهدف

يحمل الحقل `goal` مرجع `EntityRef` مثبتًا إلى `goal_contract`:

```yaml
goal:
  entity_type: goal_contract
  entity_id: goal-pump-driver-001
  entity_version: 1
```

إذا ظهر إصدار دلالي أحدث من العقد، تحتاج الخطة إلى إعادة تقييم. أما سلسلة تصحيحات تحريرية معتمدة ذات `substantive_effect: none` فلا تبطل الخطة وحدها. يجب أن يبقى عقد الهدف الفعال بالحالة `active`.

## 3. تقييم التشكيل

يسجل `planning_assessment` الأساس المختصر الذي اختار المنظم الفريق على أساسه:

```yaml
planning_assessment:
  risk_level: medium
  error_cost: material
  task_complexity: bounded
  verifiability: mixed
```

يجب أن يطابق `risk_level` و`error_cost` سياسة المخاطر في عقد الهدف. تستخرج قابلية التحقق من القيود ومعايير النجاح: إذا كان لها نوع واحد يسجل ذلك النوع، وإذا تعددت الأنواع تستخدم `mixed`.

قيم تعقيد المهمة في `v0.1` هي:

```text
bounded | multi_step | open_ended
```

## 4. الميزانية

```yaml
budget:
  max_model_calls: 8
  max_review_cycles: 2
  max_tool_calls: 6
  max_elapsed_seconds: 1800
```

`max_model_calls` و`max_review_cycles` إلزاميان. إذا احتوت الخطة دور مراجع، يجب ألا تكون دورات المراجعة صفرًا. تعد هذه الحدود سقوفًا ملزمة لحلقة التشغيل وليست تقديرات وصفية.

## 5. متطلبات الأدوار والإسنادات الفعلية

تفصل الخطة بين الحد الأدنى المطلوب وبين الإسنادات التي ملأت الفتحات:

```yaml
role_requirements:
  - role: executor
    minimum_count: 1
  - role: reviewer
    minimum_count: 1

assignments:
  - slot_id: executor-primary
    role: executor
    role_assignment:
      entity_type: role_assignment
      entity_id: role-assignment-executor-001
      entity_version: 1
    selection_basis:
      - domain_match
      - tool_access
      - role_eligibility
    responsibilities:
      - Prepare the candidate-selection proposal.
```

كل إسناد يجب أن:

1. يشير إلى أحدث إصدار من `RoleAssignment`.
2. يكون بالحالة `active` وساريًا لحظة تقييم الخطة.
3. يحمل الدور نفسه المعلن في فتحة الفريق.
4. يثبت بطاقة وكيل صالحة عند كون صاحبه وكيلًا.
5. يظهر سبب الاختيار ومسؤوليات الفتحة بدل الاكتفاء باسم الطرف.

لا يجوز استعمال مرجع إسناد واحد لملء فتحتين مستقلتين داخل الخطة.

## 6. سياسة الحد الأدنى الحتمية

تطبق `v0.1` خط الأساس الآتي:

- كل خطة تحتاج منفذًا واحدًا على الأقل.
- المخاطر المتوسطة أو العالية تحتاج مراجعًا.
- وجود أكثر من قيد في عقد الهدف يحتاج مراجعًا.
- تكلفة الخطأ `material` أو `severe` مع قابلية تحقق مناسبة تحتاج متحققًا.
- وجود مراجع يفرض استقلاله عن المنفذ.
- وجود متحقق يفرض قاعدة تفصله عن مؤلف الادعاء الذي يتحقق منه.
- المخاطر العالية تبقى `advisory_only` وفق عقد الهدف، وتخضع سلطات القرار لمتطلبات الموافقة البشرية.

هذه سياسة مرجعية تجريبية، وليست ادعاءً بأنها التشكيل الأمثل لجميع المجالات.

## 7. قواعد الاستقلال

### 7.1 فصل الأدوار

```yaml
- rule_id: reviewer-independent-from-executor
  type: role_separation
  role_a: reviewer
  role_b: executor
```

يفحص التنفيذ الأطراف الفعلية داخل إسنادات الأدوار. اختلاف أسماء الفتحات أو الأدوار لا يكفي إذا كان `PrincipalRef` نفسه يشغل الطرفين.

### 7.2 الفصل عن مؤلف الأثر

```yaml
- rule_id: verifier-independent-from-claim-author
  type: author_separation
  reviewing_role: verifier
  target_entity_types: [contribution]
  target_contribution_types: [claim]
```

أصبح مؤلف الادعاء الفعلي ظاهرًا في `Contribution`. تسجل الخطة الاستقلال بنوع الكيان `contribution` والنوع الفرعي `claim`، ويطبق `Verification` المقارنة النهائية عند فحص الادعاء.

## 8. حسم سلطات القرار المؤجلة

كل فئة قرار استخدمت في `GoalContract`:

```yaml
authority:
  mode: team_plan
```

يجب أن تظهر مرة واحدة في `decision_authorities`:

```yaml
decision_authorities:
  - decision_subject: motor_driver_selection
    role_assignment:
      entity_type: role_assignment
      entity_id: role-assignment-decision-001
      entity_version: 1
```

ويجب أن يكون الإسناد:

- عضوًا فعليًا في `assignments`.
- بدور `decision_authority`.
- ساريًا ونشطًا وحديث الإصدار.
- مخولًا صراحة لموضوع القرار.
- مستقلًا عن الأدوار التي حددها عقد الهدف.
- مسندًا إلى إنسان إذا اشترطت فئة القرار `human_approval_required: true`.

لا يجوز للخطة إعادة تعريف سلطة قرار ثبتها عقد الهدف مسبقًا بطريقة `pinned_assignment`.

## 9. أساس الاختيار وخطة التعذر

قيم أساس الاختيار هي:

```text
domain_match | tool_access | data_policy | role_eligibility
| independence | cost | latency | availability
```

ويجب أن تحمل الخطة `selection_rationale` نصيًا يشرح لماذا يناسب هذا التشكيل الهدف والميزانية والمخاطر.

```yaml
fallback:
  missing_required_role: awaiting_user
  invalidated_assignment: replan
```

إذا غاب دور مطلوب، تطبق السياسة المسجلة بدل إسقاط الدور أو دمجه ضمنيًا مع طرف آخر. وإذا بطل إسناد بعد اعتماد الخطة، تمنع مواصلة الاستعمال غير المقيد ويطبق `replan` أو الحجب أو انتظار المستخدم.

## 10. الصلاحية ودورة الحياة

إنشاء `TeamPlan` أو إنشاء إصدار تالٍ منه يحتاج إسنادًا يحمل دور:

```text
orchestrator | human_owner
```

مع العملية الدقيقة `team_plan.create` أو `team_plan.next_version`. لا يستطيع منفذ أو مراجع منح نفسه خطة فريق لمجرد أن نطاقه النصي يحتوي العملية.

الحالات هي:

```text
active | suspended | completed | cancelled
```

يبدأ الإصدار الأول بـ`active`. تحتاج بقية الحالات `status_reason`. وتعد `completed` و`cancelled` حالتين نهائيتين لا تنشأ بعدهما نسخة تشغيلية جديدة من الخطة نفسها.

## 11. طبقات التحقق

### JSON Schema

يفرض البنية، والمراجع المثبتة، والقيم المسموحة، ومنع الخصائص المجهولة.

### التحقق الدلالي المحلي

يفرض فردية معرفات الفتحات والقواعد والقرارات، وملء الحد الأدنى للأدوار، وسياسة الحد الأدنى، وتسجيل قواعد الاستقلال، وربط سلطة القرار بعضو في الفريق.

### تحقق الحالة

يفحص الإصدار الفعلي لعقد الهدف وإسنادات الأدوار، وحالتها ومدتها، ومطابقة تقييم المخاطر، والتغطية الكاملة للقرارات المؤجلة، والاستقلال على مستوى `PrincipalRef`.

## 12. التكامل

ترتيب التشغيل هو `TeamPlan → Task → ContextGrant`. أصبحت المهمة كيانًا معتمدًا، وتصدر المنحة بعد وجود إصدار مثبت منها.

لا يمنح الانضمام إلى الفريق حق الوصول إلى سياق المستخدم. أصبح [ContextGrant](CONTEXT-GRANT.ar.md) عقدًا مستقلاً يثبت المستلم الفعلي من أعضاء الخطة، والمهمة والغرض والحقول والتصنيف والمدة. أصبح [Task](TASK.ar.md) و[Contribution](CONTRIBUTION.ar.md) عقدين معتمدين؛ واعتمد بعدهما `Evidence` و`Verification` و`Challenge` و`Review` و`Approval` و`Decision` و`ProtocolEvent` و`ExecutionResourcePlan` و`FinalResult`.


## العلاقة مع ExecutionResourcePlan

`TeamPlan` يحدد من يشغل الأدوار ولماذا، بينما `ExecutionResourcePlan` يحدد أي مورد نموذجي من الأعضاء المؤهلين يُستعمل لمجموعة مهام معينة بحسب الحصة والملاءمة والاحتياطي. تغيير المورد الفعلي لا يعيد كتابة خطة الفريق؛ لكنه قد يحتاج إصدارًا جديدًا من خطة الموارد وإصدارًا جديدًا من المهمة أو إسناد الدور إذا تغير الطرف المسؤول.
