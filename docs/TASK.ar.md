# Task — W1-CIP v0.1

## 1. الغرض

يمثل `Task` وحدة عمل واحدة محددة المرحلة داخل رسم المهام. ترتبط المهمة بإصدار مثبت من `GoalContract` وإصدار مثبت من `TeamPlan`، وتحدد مسؤولها واعتمادياتها ومخرجاتها المطلوبة وميزانيتها الجزئية وحالتها التشغيلية.

معرف مخطط الحمولة:

```text
urn:w1-cip:entity-payload:0.1:task
```

يحفظ كل إصدار داخل `VersionedEntity` من النوع `task`.

## 2. قرار المرحلة الثابتة

في `v0.1` تمثل كل مهمة **مرحلة عمل واحدة ثابتة**:

```text
planning | execution | review | verification
| revision | approval | synthesis
```

لا يتغير `phase` عبر إصدارات المهمة. عندما ينتقل سير العمل من التنفيذ إلى المراجعة أو التحقق، تنشأ مهمة تابعة جديدة بمرحلتها ومسؤولها وميزانيتها. يمنع ذلك تحويل المهمة نفسها إلى مسؤوليات مختلفة يصعب تتبعها.

التوزيع الافتراضي للأدوار:

| المرحلة | الأدوار المقبولة |
|---|---|
| `planning` | `orchestrator` أو `human_owner` |
| `execution` | `executor` |
| `review` | `reviewer` |
| `verification` | `verifier` |
| `revision` | `executor` |
| `approval` | `decision_authority` أو `human_owner` |
| `synthesis` | `synthesizer` |

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

title: Prepare the motor-driver selection proposal
description: Evaluate the supplied candidates and produce one structured proposal.
phase: execution
status: created

assigned_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-executor-001
  entity_version: 1

dependencies: []

required_outputs:
  - output_id: candidate-proposal
    entity_type: contribution
    description: A structured candidate-selection proposal.
    required: true

budget_allocation:
  max_model_calls: 2
  max_tool_calls: 2
  max_elapsed_seconds: 600

required_context_fields:
  - available_supply_voltage
  - user_skill_level
```

## 4. الحالة التشغيلية

الحالات:

```text
created | ready | assigned | in_progress
| blocked | completed | failed | cancelled
```

المسار الاعتيادي:

```text
created → ready → assigned → in_progress → completed
```

الانتقالات المسموحة:

```text
created     → ready | cancelled
ready       → assigned | blocked | cancelled
assigned    → in_progress | blocked | cancelled
in_progress → blocked | completed | failed | cancelled
blocked     → ready | assigned | in_progress | failed | cancelled
```

يجوز إنشاء إصدار جديد مع بقاء الحالة نفسها لتسجيل تعديل قانوني مسموح، لكن لا يجوز الخروج من `completed` أو `failed` أو `cancelled`.

## 5. الحجب والفشل والإلغاء

عند `blocked` يجب تسجيل `block_reason` و`status_reason`.

أسباب الحجب:

```text
dependency | evidence_required | revision_required
| awaiting_approval | awaiting_user | resource_unavailable
```

`failed` و`cancelled` يحتاجان سببًا مسجلًا. أما `completed` فيحتاج `completion_summary` والمخرجات المنتجة.

## 6. الاعتماديات

تستخدم كل اعتمادية مرجع `Task` مثبتًا:

```yaml
dependencies:
  - task:
      entity_type: task
      entity_id: task-input-fixture-001
      entity_version: 1
    required_status: completed
```

القواعد:

1. لا تعتمد المهمة على نفسها، حتى عبر إصدار آخر من الهوية نفسها.
2. يجب أن توجد المهمة التابعة وأن تكون من العقد والخطة نفسيهما.
3. يجب أن يكون المرجع هو الإصدار الحالي عند التقييم.
4. لا تنتقل المهمة إلى `ready` أو ما بعدها قبل اكتمال اعتمادياتها.
5. رسم اعتماديات المهام لا دوري، ويرفض بـ`task_dependency_cycle`.

## 7. المخرجات

تحدد `required_outputs` العقد المحلي للمهمة. كل عنصر يحمل معرفًا ونوع كيان متوقعًا ووصفًا، ويمكن ربطه بمعرف مخرج في `GoalContract` بواسطة `goal_deliverable_id`.

عند `completed` يجب أن تغطي `produced_outputs` كل مخرج موسوم `required: true`، وأن يطابق نوع الكيان النوع المعلن:

```yaml
produced_outputs:
  - output_id: candidate-proposal
    entity:
      entity_type: contribution
      entity_id: proposal-driver-001
      entity_version: 1
```

لا يثبت وجود المرجع وحده جودة المخرج أو اعتماده؛ تفحص الكيانات اللاحقة، مثل `Contribution` و`Decision`، حالتها وقواعدها الخاصة.

## 8. الميزانية الجزئية

`budget_allocation` حصة للمهمة من ميزانية `TeamPlan`، وليست عداد الاستعمال الفعلي. يمكن أن تشمل:

```text
max_model_calls
max_review_cycles
max_tool_calls
max_elapsed_seconds
```

لا يجوز أن يتجاوز أي حد الحد المقابل في خطة الفريق، كما لا يجوز أن يتجاوز مجموع حصص `max_model_calls` و`max_tool_calls` و`max_review_cycles` لجميع المهام المرتبطة بالخطة ميزانيتها الكلية. لا تجمع مدد `max_elapsed_seconds` لأنها قد تعمل بالتوازي، لكن حصة كل مهمة لا تتجاوز حد الخطة. تجمع الاستعمالات الفعلية في أحداث تشغيلية، ويمنع Runtime تجاوز الحصة المسجلة.

## 9. المسؤول وخطة الفريق

إذا كان `assigned_role_assignment` موجودًا، فيجب أن يكون:

- موجودًا وأحدث إصدار.
- نشطًا وساريًا زمنيا.
- عضوًا فعليًا في `TeamPlan`.
- مناسب الدور لمرحلة المهمة.
- مدعومًا بـ`AgentCard` صالحة عندما يكون صاحبه وكيلًا.

تحتاج الحالات `assigned` و`in_progress` و`completed` و`failed` إلى مسؤول مثبت. يجوز وضع مسؤول مخطط منذ `created`، لكن لا تعني حالة `created` أن الإسناد بدأ تنفيذه.

## 10. سياق المستخدم

`required_context_fields` تحدد أقصى أسماء الحقول التي تبررها المهمة. لا تمنح الحقول نفسها أي وصول. يجب إصدار `ContextGrant` مستقلة للمستلم والمهمة والغرض والمدة.

يرفض النظام منحة تحتوي حقلًا لا يظهر في `required_context_fields`. ويوقف استعمال المنحة عندما تصبح المهمة `completed` أو `failed` أو `cancelled`، أو إذا تغير المسؤول الحالي عن مستلم المنحة.

تبقى منحة السياق مثبتة إلى إصدار المهمة الذي عرّف نطاقها، بينما يستخدم المدقق أحدث إصدار للتحقق من الحالة الحالية والمسؤول. لذلك لا يبطل كل انتقال حالة المنحة تلقائيًا، لكن نهاية المهمة تمنع الاستعمال.

## 11. ثوابت الإصدارات

تبقى الحقول الآتية ثابتة عبر إصدارات المهمة:

- `goal`
- `team_plan`
- `phase`
- `dependencies`
- `required_outputs`
- `required_context_fields`

إذا تغير أحدها تنشأ مهمة جديدة. يمكن تعديل الوصف والميزانية والمسؤول والحالة عبر إصدار تالٍ صحيح، ضمن قواعد الخطة والسلطة.

## 12. السلطة

إنشاء المهمة:

```text
task.create
```

مقصور على `orchestrator` أو `human_owner` مع نطاق صريح.

تحديث المهمة:

```text
task.next_version
```

يجوز للمنظم أو المالك البشري، أو لإسناد المسؤول المثبت للمهمة إذا كان نطاقه يسمح بالعملية. لا يستطيع منفذ تحديث مهمة طرف آخر لمجرد امتلاكه اسم العملية.

## 13. رموز أخطاء مختارة

```text
duplicate_task_output_id
task_produced_output_unknown
task_produced_output_type_mismatch
task_required_output_missing
task_initial_status_not_created
task_status_transition_not_allowed
task_terminal_status_final
task_phase_changed
task_budget_exceeds_team_plan
task_assignee_not_in_team
task_assignee_role_phase_mismatch
task_dependency_not_completed
task_dependency_cycle
task_update_requires_assignee_or_manager
```

## 14. التكامل

ترتيب التشغيل المرجعي:

```text
GoalContract
→ TeamPlan
→ Task created
→ ContextGrant issued for the Task
→ Task ready/assigned/in_progress
→ required outputs recorded
→ Task completed
```

اعتمد [Contribution](CONTRIBUTION.ar.md) لتسجيل المقترحات والادعاءات والافتراضات والنتائج والأسئلة والتوصيات التي تنتجها المهام. وتتحقق المهمة المكتملة من أن مخرجاتها من نوع `contribution` تشير إلى مساهمات موجودة ونشطة ومطابقة لمعرف المخرج. اعتمد بعد ذلك `Evidence` و`Verification` و`Challenge` و`Review` لربط مخرجات مهام الإثبات والتحقق والاعتراض والمراجعة بمراجع مثبتة. الخطوة التالية هي `Approval`.


## الحجب بسبب الموارد

عند تعذر جميع الموارد الآمنة، تنتقل المهمة عبر إصدار جديد إلى `blocked` مع `block_reason: resource_unavailable`، أو تنتظر إعادة ضبط الحصة أو قرار المستخدم وفق `ExecutionResourcePlan`. لا يفقد النظام المخرجات السابقة، ولا يعيد الاستدعاء بصورة غير محدودة.

## مهمة التركيب النهائية

ينشأ `FinalResult` من مهمة ثابتة المرحلة `synthesis`. يكون مخرجها من النوع `final_result`، ولا تكتمل قبل نشر الإصدار الأول وربطه بـ`produced_outputs`.
