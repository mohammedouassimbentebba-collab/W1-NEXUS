# Contribution — W1-CIP v0.1

## 1. الغرض

يمثل `Contribution` مساهمة قانونية واحدة كتبها عضو محدد من الفريق أثناء تنفيذ مهمة محددة. لا تعني المساهمة أن محتواها صحيح أو معتمد؛ فهي تسجل ما اقترحه أو ادعاه أو افترضه أو أبلغ عنه المؤلف، مع مصدر المسؤولية والسياق والقيود.

يحفظ `Contribution` داخل `VersionedEntity` من النوع:

```text
contribution
```

وتحدد الحمولة `contribution_type` واحدة من القيم:

```text
proposal | claim | assumption | measured_result
| tool_result | question | recommendation
```

في `v0.1` لا ينشأ كيان مستقل من النوع `claim` أو `proposal`. فالادعاء مثلًا هو:

```yaml
entity:
  entity_type: contribution
  entity_id: claim-startup-current-001
  entity_version: 1
legal_payload:
  contribution_type: claim
```

قد تبقى ملفات `claim-*` القديمة داخل اختبارات العقود التأسيسية بوصفها سجلات عامة لاختبار الإصدارات، لكنها ليست الشكل المعياري لمساهمات W1-CIP بعد اعتماد هذا المخطط.

## 2. البنية المشتركة

كل مساهمة تثبت:

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
  entity_id: task-driver-selection-001
  entity_version: 4

authored_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-executor-001
  entity_version: 1

contribution_type: proposal
status: active
classification: internal
content: {}
limitations: []
```

لا تعتمد نسبة المساهمة على `actor` وحده. يجب أن يطابق `authorized_by` إسناد الدور الموجود في `authored_by_role_assignment`، وأن يكون ذلك الإسناد هو المسؤول المثبت للمهمة.

## 3. أنواع المحتوى

### 3.1 Proposal

يسجل خيارًا أو حلًا مقترحًا مع مبرره والبدائل التي نوقشت:

```yaml
contribution_type: proposal
content:
  subject: Select candidate-b
  description: Candidate-b satisfies the supplied startup constraint.
  rationale: Candidate-a has a 15 A peak limit below the required 18 A.
  alternatives_considered:
    - candidate-a
```

المقترح ليس قرارًا، حتى لو كان عالي الثقة أو كان صاحبه منفذ المهمة.

### 3.2 Claim

يسجل عبارة قابلة للتقييم ويصرح بنوع قابليتها للتحقق:

```yaml
contribution_type: claim
content:
  statement: candidate-a does not support the required startup current
  verifiability: deterministic
  submission_verification_status: unverified
  verification_requirements:
    - Compare the supplied 15 A limit with the 18 A startup peak.
```

القيم المسموحة لـ`verifiability`:

```text
deterministic | empirical | source_based
| human_judgment | not_currently_verifiable
```

تثبت `submission_verification_status: unverified` حالة الادعاء عند تقديمه فقط. لا تعدل المساهمة عند إضافة دليل أو تحقق مستقل لاحقًا؛ تنشأ علاقات `Evidence` و`Verification` ككيانات مستقلة.

إذا كان النوع `human_judgment`، يجب تسجيل `judgment_criteria`. ولا يجوز تحويل الحكم البشري إلى تحقق حتمي لمجرد استخدام رقم أو مقياس.

### 3.3 Assumption

```yaml
contribution_type: assumption
content:
  statement: all current values use amperes
  basis: the fixture labels every current field with A
  impact_if_false: critical
  validation_needed: true
```

يجب أن يبقى الافتراض ظاهرًا في المراجعة والقرار إذا كان مؤثرًا ولم يختبر.

### 3.4 Measured result

```yaml
contribution_type: measured_result
content:
  metric: startup_current
  value: 18
  unit: A
  method: Read the supplied synthetic startup-current capture.
  conditions:
    - startup interval 0.8 s
  sample_count: 1
```

هذا سجل لما أبلغ عنه المؤلف، وليس `Evidence` تلقائيًا. كيان الدليل اللاحق يثبت المصدر وسلامته وعلاقته بادعاء محدد.

### 3.5 Tool result

```yaml
contribution_type: tool_result
content:
  tool:
    principal_type: tool
    principal_id: calculator-01
  operation: calculator.compare_values
  outcome: succeeded
  summary: 18 A exceeds 15 A by 3 A.
  result_data:
    difference_amperes: 3
```

يسجل `tool_result` نتيجة أبلغ عنها مؤلف المساهمة. ولا تتحول النتيجة إلى `computed_result` أو دليل متحقق لمجرد أنها خرجت من أداة.

### 3.6 Question

```yaml
contribution_type: question
content:
  text: Is continuous operation required after startup?
  blocking: false
  requested_roles:
    - human_owner
```

يجوز إنشاء سؤال أثناء مهمة `in_progress`، ويجوز إنشاء سؤال حاجب عندما تكون المهمة `blocked`.

### 3.7 Recommendation

```yaml
contribution_type: recommendation
content:
  statement: Use candidate-b
  rationale: candidate-a fails the supplied peak-current constraint
  recommended_action: advance candidate-b to independent review
  decision_subject: motor_driver_selection
  urgency: normal
```

التوصية ليست قرارًا، ولا تمنح مؤلفها سلطة اعتماد.

## 4. الارتباط بمخرجات المهمة

يمكن للمساهمة أن تعلن أنها تحقق مخرجًا مطلوبًا:

```yaml
fulfills_output_id: candidate-proposal
```

يجب أن يوجد المعرّف داخل `Task.required_outputs` وأن يكون نوعه `contribution`. وعند اكتمال المهمة يجب أن يشير `produced_outputs` إلى إصدار مساهمة موجود ونشط يعلن المعرّف نفسه.

لا يكفي تشابه النص أو معرف المساهمة لإثبات تحقيق المخرج.

## 5. الأساس والسياق

### 5.1 Basis

يجوز تسجيل مراجع مثبتة للكيانات التي اعتمد عليها المؤلف:

```yaml
basis:
  - entity_type: contribution
    entity_id: assumption-current-unit-001
    entity_version: 1
```

وجود مرجع في `basis` يثبت النسب والسياق، ولا يجعله دليلًا تلقائيًا. لا يجوز إدراج إصدارات متعددة من الهوية نفسها داخل القائمة.

### 5.2 Context usage

إذا استعمل المؤلف سياقًا ممنوحًا، تسجل المساهمة كل استعمال:

```yaml
context_usage:
  - grant:
      entity_type: context_grant
      entity_id: context-grant-executor-001
      entity_version: 1
    fields_used:
      - available_supply_voltage
    purpose_code: adapt_component_explanation
```

يتحقق Runtime من:

- وجود المنحة وإصدارها الحالي.
- مطابقة المستلم للمؤلف.
- تطابق هوية المهمة واستمرار نطاقها.
- صلاحية الوقت.
- أن الحقول ضمن قائمة السماح.
- تطابق الغرض.
- عدم وصول المهمة إلى حالة نهائية.

لا تسجل قيم السياق نفسها داخل المساهمة أو سجل التدقيق.

يجب أن تحمل المساهمة `classification` واحدة من `public` أو `internal` أو `confidential` أو `restricted`. إذا استعملت منحة سياق، لا يجوز أن يكون تصنيف المساهمة أقل تقييدًا من تصنيف المنحة. كما لا يسمح إصدار لاحق بخفض التصنيف؛ فك التصنيف يحتاج آلية مستقلة غير موجودة في `v0.1`.

## 6. توافق المرحلة والمؤلف

لا تقبل مساهمة إلا من إسناد الدور المسؤول عن المهمة. ويجب أن يملك الإسناد العملية الدقيقة:

```text
contribution.create
```

أو عند إنشاء إصدار تالٍ:

```text
contribution.next_version
```

كما يجب أن تسمح `AgentCard.permissions.data_write` بالقيمة `contribution` عندما يكون المؤلف وكيلًا.

الأنواع المسموحة حسب مرحلة المهمة في `v0.1`:

| المرحلة | الأنواع المسموحة |
|---|---|
| `planning` | proposal, assumption, question, recommendation |
| `execution` | جميع الأنواع السبعة |
| `review` | claim, assumption, question, recommendation |
| `verification` | claim, measured_result, tool_result, question, recommendation |
| `revision` | جميع الأنواع السبعة |
| `approval` | question, recommendation |
| `synthesis` | proposal, question, recommendation |

تقبل المساهمات العادية عندما تكون المهمة `in_progress`. والاستثناء هو سؤال من نوع `question` عندما تكون المهمة `blocked`.

## 7. القيود والثقة

يجب أن تسجل المساهمة `limitations` حتى لو كانت القائمة فارغة.

يمكن تسجيل ثقة وصفية اختيارية:

```yaml
confidence:
  level: high
  basis: bounded comparison over the supplied fixture
  calibrated: false
```

لا تمنح الثقة سلطة، ولا تعوض الدليل أو التحقق. ولا تقارن مستويات الثقة بين نماذج مختلفة كما لو كانت معايرة على مقياس واحد.

## 8. دورة الحياة والإصدارات

الحالات:

```text
active | withdrawn
```

- يبدأ الإصدار الأول بـ`active`.
- يحتاج `withdrawn` إلى `withdrawal_reason`.
- السحب نهائي للمساهمة نفسها.
- لا يجوز تغيير المحتوى في الإصدار نفسه الذي يسحب المساهمة؛ يسجل السحب فقط مع إبقاء المحتوى السابق كاملًا.
- تبقى الحقول التالية ثابتة عبر الإصدارات:
  - `goal`
  - `team_plan`
  - `task`
  - `authored_by_role_assignment`
  - `contribution_type`
  - `fulfills_output_id`

تصحيح نص المساهمة أو تغيير معناها ينشئ إصدارًا جديدًا وفق قواعد `VersionedEntity`.

## 9. ما لا يثبته Contribution

لا يثبت `Contribution` بمفرده:

- صحة الادعاء.
- سلامة مصدر القياس.
- صحة طريقة الأداة.
- كفاية المقترح.
- اعتماد التوصية.
- إغلاق اعتراض.

تتولى كيانات `Evidence` و`Verification` و`Review` و`Decision` هذه الوظائف في مراحلها المستقلة.
