# نموذج حوكمة W1-CIP v0.1

## 1. الغرض والنطاق

تحدد هذه الوثيقة القواعد المعيارية التي تضبط تكوين الفريق، وتبادل السياق، وتصنيف الأدلة، والتحقق، والمراجعة، والاعتراض، والقرار، وسجل الأحداث في `W1-CIP v0.1`. وتطبق معها [الدلالات التأسيسية](FOUNDATIONAL-SEMANTICS.ar.md) بوصفها مرجعًا ملزمًا للهوية والإصدارات والترتيب والتزامن والمراجع المثبتة.

في هذه الوثيقة:

- **يجب** تعني قاعدة إلزامية للتوافق.
- **ينبغي** تعني قاعدة موصى بها يجوز مخالفتها مع تسجيل السبب.
- **يجوز** تعني خيارًا تنفيذيًا.

لا تحاول النسخة الأولى إثبات هوية الوكلاء تشفيريًا أو إنشاء نظام ثقة بين المجالات. تلك وظائف مؤجلة، أما الصلاحيات هنا فتطبق داخل جلسة واحدة بواسطة المنظم المرجعي.

## 2. الأدوار وسلطة القرار

الدور صفة داخل الجلسة، وليس هوية دائمة للطرف. يمثل `role_assignment_id` إسناد الدور إلى `PrincipalRef` داخل جلسة، مع `authority_scope` ومدة صلاحية محددين. يجوز للطرف أن يحمل أكثر من دور إذا سمح مستوى المخاطر بذلك، لكن الأدوار المتعارضة يجب أن تبقى مستقلة عندما يشترط `TeamPlan` ذلك.

يجب فصل `actor` و`on_behalf_of` و`authorized_by` و`recorded_by` وفق الدلالات التأسيسية؛ فلا ينسب الإجراء إلى المنظم لمجرد أنه سجله. ويكون `recorded_by` Runtime موثوقًا، بينما تحدد الصلاحية للطرف الفعلي عبر `authorized_by`.

الأدوار الأساسية:

| الدور | المسؤولية | ما لا يملكه تلقائيًا |
|---|---|---|
| `orchestrator` | إدارة الدورة وتطبيق القواعد وإسناد المهام | اعتماد كل القرارات |
| `executor` | إعداد المقترح أو تنفيذ المهمة | اعتماد عمله في القرارات المقيدة |
| `reviewer` | تقييم الحل والقيود والاكتمال | إثبات ادعاء تقني دون طريقة تحقق |
| `verifier` | فحص ادعاء محدد بطريقة مسجلة | مراجعة الحل كله تلقائيًا |
| `decision_authority` | اعتماد القرار ضمن نطاق مفوض | تجاوز عقد الهدف أو الموافقات البشرية |
| `synthesizer` | صياغة النتيجة من الحالة المعتمدة | تغيير القرار أو إخفاء اعتراض |
| `human_owner` | منح الموافقات وقبول المخاطر والتصعيد | لا ينطبق |

يجب أن يحدد `GoalContract` أو `TeamPlan` صاحب الصلاحية لكل فئة قرار. إذا لم توجد سلطة صالحة، ينتقل العمل إلى:

```yaml
phase: approval
status: blocked
block_reason: awaiting_user
```

لا يعد المنظم سلطة قرار إلا إذا منحه العقد هذا الدور صراحة. وفي القرار الذي يمس قيدًا حرجًا، يجب ألا يكون المنفذ هو صاحب الاعتماد الوحيد.

### 2.1 إسناد الدور الأول

قبل وجود أي `RoleAssignment` لا يمكن اشتقاق الصلاحية من `authorized_by`. لذلك ينشئ Runtime محلي موثوق حدث `session.bootstrap.completed` وفق الدلالات التأسيسية.

يقتصر الحدث على إنشاء إسناد الدور الأول بهذه القيود:

- الكيان الناتج من النوع `role_assignment` والإصدار `1`.
- الدور الأول هو `human_owner`.
- شرط الإنشاء هو `expected_absent: true`.
- الحدث هو `sequence: 1` ولا يحمل `authorized_by`.
- كائن `bootstrap` أحادي الاستخدام ونطاقه الوحيد `create_initial_role_assignment`.

لا يمنح Runtime نفسه دور `human_owner` لمجرد تسجيل الحدث. المالك هو `PrincipalRef` من النوع `human` المحدد بوصفه `subject` داخل حمولة إسناد الدور، بينما يكون Runtime نفسه `actor` و`recorded_by` لواقعة التأسيس المحلية. وبعد قبول الحدث لا يجوز استعمال سلطة التأسيس لإنشاء أدوار أو قرارات إضافية.

عند وجود `on_behalf_of` يجب أن يكون سند `authorized_by` صالحًا للطرف المُمثَّل، لا للوسيط التقني فقط. وإذا غاب الحقل، يطابق سند السلطة `actor`. لا يمنح نوع الطرف دورًا تلقائيًا؛ فالنوع `human` أو `agent` يحدد الهوية، بينما يحدد `RoleAssignment` الدور والنطاق.

### 2.2 عقد RoleAssignment

اعتمد مخطط [RoleAssignment](ROLE-ASSIGNMENT.ar.md) بوصفه الحمولة القانونية لإسناد الدور. يحمل `subject` و`role` و`authority_scope` و`status` ونافذة الصلاحية وسبب الإسناد. لا تمنح تسمية الدور أي عملية تلقائيًا؛ يجب أن تكون العمليات والقدرات وموضوعات القرار مسجلة صراحة.

تقبل العمليات أسماء دقيقة بلا wildcards. وتشتق العملية المطلوبة للأوامر والأحداث التي تغير كيانًا من نوع الهدف وشرط التزامن: `create` يصبح `<entity_type>.create` و`next_version` يصبح `<entity_type>.next_version`. لذلك لا تمنح الرسالة العامة `entity.version.created` سلطة على جميع الكيانات.

لا يسند الدور في `v0.1` مباشرة إلى `runtime` أو `tool`. ويكون `human_owner` إنسانًا. ولا يجوز منح عمليتي `role_assignment.create` و`role_assignment.next_version` إلا لإسناد يحمل دور `human_owner`.

القدرات الخاصة الحالية هي `classify_editorial_correction` و`accept_risk`. تقيد الأولى بأدوار المراجعة أو التحقق أو المالك البشري، وتبقى استقلالية صاحبها عن مؤلف التغيير شرطًا إضافيًا. وتقيد الثانية بالمالك البشري.

### 2.3 صلاحية سند الدور

عند قبول غلاف يستند إلى `RoleAssignment` يجب أن يتحقق المنظم من أن المرجع موجود ومثبت إلى أحدث إصدار مقبول، وأن `subject` يطابق الطرف الفعلي، وأن الحالة `active`، وأن لحظة القبول تقع ضمن نافذة الصلاحية، وأن العملية المطلوبة مدرجة في `authority_scope`.

تقيم الأحداث عند `recorded_at`. أما الأوامر فيمرر لها Runtime لحظة القبول المقترحة صراحة، ولا يعتمد التحقق على وقت محلي ضمني. وتعتمد مقارنة أحدث إصدار على لقطة الحالة السابقة مباشرة للغلاف الجاري أثناء إعادة التشغيل التسلسلي. وإذا كان الإسناد `suspended` أو `revoked` أو منتهيًا فلا يجوز استعماله ولو بقي الإصدار التاريخي محفوظًا.

### 2.4 دورة حياة إسناد الدور

الحالات هي `active` و`suspended` و`revoked`. تحتاج الحالتان الأخيرتان إلى سبب مسجل، و`revoked` نهائية. لا يجوز أن يبدأ الإصدار الأول قبل `recorded_at` للحدث المنشئ. تبقى هوية `subject` واسم `role` و`valid_from` ثابتة عبر إصدارات الإسناد؛ أما النطاق وتاريخ النهاية والحالة فيجوز تغييرها بإصدار تالٍ كامل.

## 3. بطاقة الوكيل

تمثل `AgentCard` تصريحًا قانونيًا مثبتًا يصف الوكيل قبل اختياره أو منحه دورًا. تحفظ داخل `VersionedEntity` من النوع `agent_card`، ويطابق `entity_id` معرف `PrincipalRef` للوكيل. لا تكرر الحمولة `agent_id` أو رقم الإصدار.

يجب أن تسجل البطاقة على الأقل:

```yaml
display_name: Electronics Agent
provider:
  name: example-provider
  model: example-model
capabilities:
  domains: [electronics]
  operations: [reasoning, structured_output]
tools: [calculator]
eligible_roles: [executor, reviewer]
prohibited_uses: [final_safety_approval]
permissions:
  data_read: [task_input, granted_context]
  data_write: [contribution]
  actions: []
data_policy:
  accepted_classifications: [public, internal]
  external_processing: true
  onward_sharing: false
autonomy:
  level: propose_only
  human_approval_required_for: [external_action]
cost:
  class: medium
  unit: model_call
latency:
  class: normal
limitations: [no_physical_measurement]
status: active
```

لا يجوز للمنظم افتراض قدرة أو أداة أو صلاحية غير معلنة. كما لا تعد البطاقة إثباتًا للأداء أو منحة سلطة؛ الصلاحية تأتي من `RoleAssignment`، والوصول إلى السياق يأتي من `ContextGrant`.

عند إسناد دور إلى طرف من النوع `agent` يجب أن يثبت `RoleAssignment.basis` إصدارًا واحدًا من بطاقة تحمل المعرف نفسه. يجب أن تكون البطاقة نشطة وأن تدرج الدور ضمن `eligible_roles`. يمنع `human_owner` من أدوار الوكلاء.

إذا ظهر إصدار دلالي أحدث من البطاقة يحتاج الإسناد إلى إعادة تقييم وتثبيت الإصدار الجديد. أما سلسلة تصحيحات تحريرية معتمدة ذات `substantive_effect: none` فلا تبطل الإسناد. وتعليق البطاقة أو تقاعدها يمنع استعمال إسناد الدور المدعوم بها.

الحالات هي `active` و`suspended` و`retired`، وتعد `retired` نهائية. يشرح [مخطط AgentCard](AGENT-CARD.ar.md) الحقول وقواعد الإصدارات والربط بإسناد الدور.

## 4. عقد الهدف

يمثل `GoalContract` المصدر القانوني للهدف قبل تشكيل الفريق أو بدء التنفيذ. يحفظ داخل `VersionedEntity` من النوع `goal_contract`، ويثبت الهدف والنطاق والمخرجات والقيود ومعايير النجاح والمحظورات وسياسة المخاطر وسلطة القرار.

يجب أن يغطي كل مخرج مطلوب معيار نجاح من نوع `required_for_completion`، وأن يغطي كل قيد `critical` معيار مطلوب يشير إليه صراحة. وكل مخرج من نوع `decision` يجب أن يحدد `decision_subject` موجودًا داخل سياسة القرار.

المحظورات صلبة في `v0.1` ولا يستطيع قبول المخاطر تجاوزها. يحدد قبول المخاطر فقط شدة الاعتراضات غير المحسومة التي يمكن الإفصاح عنها في نتيجة مقيدة. أما العقد عالي المخاطر فيستخدم `advisory_only` ويحتاج موافقة بشرية لكل فئة قرار.

تحدد سلطة القرار إما بواسطة `RoleAssignment` مثبتة تحمل دور `decision_authority` ونطاق الموضوع، أو تؤجل إلى `TeamPlan` مع وجوب حسمها قبل التنفيذ. إذا لم توجد سلطة صالحة، ينتقل العمل إلى `approval/blocked/awaiting_user`.

إنشاء `GoalContract` أو إصدار نسخة تالية منه مقصور على `human_owner`. يبدأ الإصدار الأول بالحالة `active`، وتعد `fulfilled` و`cancelled` حالتين نهائيتين. يشرح [مخطط GoalContract](GOAL-CONTRACT.ar.md) البنية والاختبارات وقواعد التتبع.

## 5. تشكيل الفريق

يمثل `TeamPlan` القرار القانوني لتشكيل الفريق قبل بدء التنفيذ. يحفظ داخل `VersionedEntity` من النوع `team_plan`، ويثبت إصدار `GoalContract` الذي بني عليه، والتقييم التخطيطي، والميزانية، ومتطلبات الأدوار، وإسناداتها الفعلية، وقواعد الاستقلال، وسلطات القرار المؤجلة.

```yaml
goal:
  entity_type: goal_contract
  entity_id: goal-pump-driver-001
  entity_version: 1
planning_assessment:
  risk_level: medium
  error_cost: material
  task_complexity: bounded
  verifiability: mixed
budget:
  max_model_calls: 8
  max_review_cycles: 2
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
    selection_basis: [domain_match, tool_access, role_eligibility]
    responsibilities:
      - Prepare the candidate-selection proposal.
independence_rules:
  - rule_id: reviewer-independent-from-executor
    type: role_separation
    role_a: reviewer
    role_b: executor
decision_authorities:
  - decision_subject: motor_driver_selection
    role_assignment:
      entity_type: role_assignment
      entity_id: role-assignment-decision-001
      entity_version: 1
fallback:
  missing_required_role: awaiting_user
  invalidated_assignment: replan
status: active
```

يجب أن يعتمد تشكيل الفريق على تعقيد المهمة، ومستوى المخاطر وتكلفة الخطأ، وقابلية التحقق، والميزانية والوقت، والحاجة إلى استقلال الآراء، وملاءمة المجال والأدوات، وتوافق سياسة البيانات.

تستخدم `v0.1` سياسة حتمية بسيطة:

| الحالة | الحد الأدنى |
|---|---|
| كل خطة | منفذ واحد على الأقل |
| مخاطر متوسطة أو عالية، أو عقد يحتوي قيودًا متعددة | منفذ ومراجع مستقل |
| خطأ مادي أو شديد مع قابلية تحقق مناسبة | منفذ ومراجع ومتحقق |
| قرار أجله العقد إلى الخطة | سلطة قرار مثبتة ومخولة للموضوع |
| قرار يحتاج موافقة بشرية | سلطة القرار إنسان |

يجب أن تكون إسنادات الأدوار حديثة ونشطة وسارية، وأن تطابق الأدوار المعلنة، وأن تثبت بطاقات الوكلاء عند الحاجة. يفحص فصل الأدوار على `PrincipalRef` الفعلي؛ فلا يكفي اختلاف أسماء الفتحات إذا كان الطرف نفسه يشغل دورين متعارضين.

كل موضوع قرار استخدم `authority.mode: team_plan` في عقد الهدف يجب أن يحسم مرة واحدة في الخطة. ويجب أن تكون سلطة القرار عضوًا فعليًا في الفريق، بدور `decision_authority`، وبنطاق يشمل الموضوع، ومستقلة عن الأدوار التي حددها العقد.

إنشاء الخطة أو إصدار نسخة تالية منها مقصور على `orchestrator` أو `human_owner` مع نطاق عملية صريح. يبدأ الإصدار الأول بالحالة `active`، وتعد `completed` و`cancelled` حالتين نهائيتين. يشرح [مخطط TeamPlan](TEAM-PLAN.ar.md) البنية والاختبارات وقواعد إعادة التخطيط.

## 6. مشاركة سياق المستخدم

يطبق البروتوكول مبدأ **السياق المرتبط بالمهمة فقط** (`Task-Relevant Context Only`). لا يجوز إرسال سياق شخصي أو مؤسسي إلى طرف اعتمادًا على عضويته في الفريق وحدها.

يمثل `ContextGrant` إذنًا قانونيًا مستقلًا يحفظ داخل `VersionedEntity` من النوع `context_grant`. يثبت:

- `recipient_role_assignment` بوصفه إسناد الدور المستلم.
- إصدار `TeamPlan` الذي ينتمي إليه المستلم.
- مهمة مثبتة بالإصدار.
- قائمة سماح مغلقة للحقول.
- غرضًا آليًا ووصفًا بشريًا.
- التصنيف وسياسة المعالجة الخارجية وإعادة المشاركة.
- نافذة زمنية وحالة واعتمادًا بعد السحب.

لا تخزن قيم السياق داخل المنحة أو سجل التدقيق. قبل بناء مدخل الوكيل، يجب أن يطابق الاستعمال المستلم والمهمة والغرض والحقول والوقت. ويجب أن يتوافق التصنيف مع `AgentCard.data_policy.accepted_classifications`، وأن تحتوي البطاقة إذن قراءة `granted_context`. إذا كانت البطاقة تعالج البيانات خارجيًا فلا بد من `processing_mode: external_allowed`.

إعادة المشاركة إما `prohibited` أو `separate_grant_required`؛ ولا توجد مشاركة عامة ضمنية. كل مستلم تالٍ يحتاج منحة مستقلة.

الحالات هي `active` و`suspended` و`revoked`. يبدأ الإصدار الأول بـ`active`، ويكون السحب نهائيًا للمنحة نفسها. كما يوقف `expires_at` الاستعمال حتى إذا بقيت الحالة المخزنة `active`. يجب ألا تبدأ المنحة قبل إسناد المستلم أو تنتهي بعده.

تفصل `reliance_policy` بين منع الوصول المستقبلي وبين أثر السحب في القرارات السابقة:

- `historical_reliance_allowed`: لا يبطل السحب القرارات السابقة تلقائيًا.
- `continuous_validity_required`: قد يولد انتهاء المنحة أو سحبها إعادة تقييم عبر الاعتماديات المسجلة.

إدارة المنح مقصورة في `v0.1` على `human_owner` مع نطاق عملية صريح. ويسجل كل استعمال أو رفض بأثر تدقيقي مجرد لا يعيد كشف القيم الحساسة. يشرح [مخطط ContextGrant](CONTEXT-GRANT.ar.md) البنية ودورة الحياة واختبارات الاستعمال.

## 7. المساهمات

يمثل `Contribution` ما يقدمه عضو محدد من الفريق أثناء مهمة محددة، ولا يعني أن المحتوى صحيح أو معتمد. تحفظ جميع الأنواع السبعة داخل `VersionedEntity` من النوع `contribution`، ويحدد `contribution_type` واحدة من القيم:

- `proposal`
- `claim`
- `assumption`
- `measured_result`
- `tool_result`
- `question`
- `recommendation`

يجب أن تثبت المساهمة `GoalContract` و`TeamPlan` و`Task` وإسناد الدور المؤلف. ويجب أن يطابق `authorized_by` ذلك الإسناد، وأن يكون الإسناد هو المسؤول الحالي للمهمة ويمتلك `contribution.create` أو `contribution.next_version`.

يسجل الادعاء `verifiability` وحالة `unverified` عند التقديم. لا تعدل المساهمة عند إضافة دليل أو تحقق؛ تنشأ العلاقات ككيانات مستقلة. كما لا يعد `measured_result` أو `tool_result` دليلًا تلقائيًا لمجرد احتوائه على قياس أو ناتج أداة.

إذا استعمل المؤلف سياقًا ممنوحًا، تسجل المساهمة المنحة والحقول والغرض في `context_usage` من دون نسخ القيم. يجب أن يحمل الناتج تصنيفًا لا يقل تقييدًا عن المنحة، ولا يسمح بخفض التصنيف في إصدار لاحق. ويمكن ربطها بمخرج مهمة عبر `fulfills_output_id`. لا تكتمل المهمة بمخرج من نوع `contribution` إلا إذا أشارت إلى مساهمة موجودة ونشطة ومطابقة للمهمة ومعرف المخرج.

الحالات هي `active` و`withdrawn`. يبدأ الإصدار الأول بـ`active`، ويكون السحب نهائيًا ولا يجوز تغيير المحتوى بالتزامن معه. يشرح [مخطط Contribution](CONTRIBUTION.ar.md) الأنواع والربط بالمهمة والسياق ودورة الحياة.

## 8. الأدلة

### 8.1 الفرق بين المصدر والدليل

المصدر يبين منشأ المعلومة. أما `Evidence` فهو سجل قانوني يثبت نسخة المصدر ويربطها بإصدار محدد من مساهمة `claim` بعلاقة `supports` أو `refutes` أو `contextualizes`. لا يجعل وجود المصدر الادعاء متحققًا تلقائيًا.

### 8.2 الأنواع والمصدر

أنواع `Evidence` هي:

- `user_provided`
- `document_source`
- `tool_observation`
- `measured_result`
- `computed_result`
- `model_inference`
- `external_reference`
- `human_confirmation`

يجب أن يسجل المصدر `uri` وإصدارًا ثابتًا ووصفًا وتصنيفًا وطريقة الالتقاط ومن قام بها ووقتها. ويمنع تصنيف `model_inference` كـ`measured_result` أو `computed_result` لمجرد احتوائه على أرقام.

### 8.3 الأهداف والسلامة

يجب أن يكون كل هدف `EntityRef` من النوع `contribution` وأن تكون المساهمة المستهدفة من النوع `claim`. حالات سلامة المصدر هي `unverified` و`verified` و`disputed` و`unavailable`. وتشير السلامة إلى نسبة العنصر إلى مصدره وثبات نسخته، لا إلى صحة الاستنتاج المبني عليه.

يرتبط الدليل بمهمة ومسجل مخول، ولا يقبل إذا كان تصنيفه أقل تقييدًا من المصدر. ويكون السحب نهائيًا، وتبقى هوية المصدر والأهداف والعلاقة ثابتة عبر الإصدارات. ترد التفاصيل الملزمة في [مخطط Evidence](EVIDENCE.ar.md).

## 9. التحقق والمراجعة

### 9.1 التحقق

يركز `Verification` على مساهمة واحدة أو مجموعة مترابطة من النوع `claim`، ويطبق طريقة مصدّرة على أدلة مثبتة. أنواع الطرق هي `deterministic_rule` و`executable_test` و`calculation` و`source_cross_check` و`human_check`.

تسجل الطريقة معرفها وإصدارها ومصدر الإجراء ونطاق صلاحيتها وحالتها: `validated` أو `unvalidated` أو `disputed`. لا تعني حتمية الطريقة صحتها. ولا تكون النتيجة `conclusive` إلا إذا كانت الطريقة معتمدة وكانت سلامة الأدلة المستخدمة موثقة.

النتائج هي `passed` و`failed` و`inconclusive` و`not_run`، وتكون إما `conclusive` أو `qualified`. لا يجوز إعلان `passed` لطريقة متنازع عليها، ولا لادعاء `not_currently_verifiable`. ترد التفاصيل الملزمة في [مخطط Verification](VERIFICATION.ar.md).

### 9.2 المراجعة

يقيم `Review` حزمة حل أو مخرج مهمة مثبتًا بالإصدارات من حيث الالتزام بعقد الهدف، وتغطية المتطلبات، والاتساق الداخلي، والمخاطر والقيود، وكفاية الأدلة، وكفاية عمليات التحقق. يجب أن يسجل تقييمًا واحدًا لكل محور من هذه المحاور، مع النتيجة والمبرر والمراجع ذات الصلة.

نتيجة المراجعة هي `approved` أو `revision_required` أو `rejected` أو `escalation_required`. لا يجوز `approved` مع معيار ناقص، أو تحقق مرجعي غير ناجح، أو اعتراض حرج غير محسوم. كما لا يجوز اعتماد ادعاء قابل للاختبار داخل النطاق من دون `Verification` ناجحة تغطي إصداره المثبت.

يجب أن يكون المراجع مستقلًا فعليًا عن مؤلف المساهمات المستهدفة، لا مختلفًا عنه في معرف إسناد الدور فقط. ويمكن للمراجعة إنشاء `Challenge` أو طلب تعديل، لكنها لا تحل الاعتراض ولا تعتمد القرار النهائي تلقائيًا. ترد التفاصيل الملزمة في [مخطط Review](REVIEW.ar.md).

## 10. دورة الاعتراض

### 10.1 الحالات

الحالات هي `open` و`acknowledged` و`evidence_requested` و`resolved` و`rejected` و`escalated` و`disclosed_unresolved` و`reopened`.

الانتقالات ليست خطية. يجوز الانتقال من `open` مباشرة إلى `rejected` أو `escalated`، ومن `evidence_requested` إلى الحسم أو التصعيد أو الإفصاح المقيد. ولا تكون `escalated` طريقًا مسدودًا؛ تستطيع الجهة الأعلى إعادة الحالة إلى المعالجة أو طلب دليل أو حسمها. ويجوز إعادة فتح حالة مغلقة فقط بسبب دليل جديد، أو خلل إجرائي، أو تجاوز صلاحية، أو تغير جوهري مسجل. ترد مصفوفة الانتقالات الملزمة في [الدلالات التأسيسية](FOUNDATIONAL-SEMANTICS.ar.md).

### 10.2 قواعد الإغلاق

لا يستطيع مؤلف المساهمة المستهدفة إغلاق الاعتراض منفردًا. يعد الاعتراض محلولًا بإحدى الطرق الآتية:

1. يقبل المعترض الإجراء التصحيحي أو الدليل الجديد.
2. يحسم مراجع مستقل مفوض الخلاف مع تسجيل السبب والأدلة.
3. يقبل `human_owner` المخاطرة صراحة، فتتحول الحالة إلى `disclosed_unresolved` ولا توصف بأنها `resolved`.

تعني `rejected` أن جهة مستقلة مخولة قررت أن الاعتراض غير صالح أو خارج النطاق، مع تسجيل المبرر. ولا تعني تجاهل الاعتراض.

يخزن الاعتراض داخل `VersionedEntity` من النوع `challenge`، ومن أمثلته:

```yaml
goal:
  entity_type: goal_contract
  entity_id: goal-01
  entity_version: 1
team_plan:
  entity_type: team_plan
  entity_id: team-plan-01
  entity_version: 1
task:
  entity_type: task
  entity_id: task-review-01
  entity_version: 4
raised_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-reviewer-01
  entity_version: 1
raised_at: 2026-08-05T10:00:00Z
last_transition_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-reviewer-01
  entity_version: 1
last_transition_at: 2026-08-05T10:00:00Z
target:
  entity_type: contribution
  entity_id: claim-21
  entity_version: 2
category: constraint_violation
severity: critical
status: open
reason: declared peak limit is below measured startup peak
required_resolution:
  - action_code: replace_candidate
    description: replace the incompatible candidate
  - action_code: provide_counter_evidence
    description: provide valid counter-evidence
resolution_policy:
  allowed_roles: [reviewer, human_owner]
  challenger_may_accept_correction: true
  target_author_may_resolve_alone: false
classification: internal
limitations: []
```

درجات الشدة هي `informational` و`minor` و`major` و`critical`. وترد البنية ودورة الحياة وقواعد الحل الملزمة في [وثيقة Challenge](CHALLENGE.ar.md).


## 11. الموافقة

اعتمد مخطط [Approval](APPROVAL.ar.md) لتسجيل موافقة أو رفض محدود الغرض. لا تستنتج السلطة من اسم الموافقة؛ بل من نوعها وأهدافها وصاحبها ونافذة سريانها وشروطها.

أنواع `v0.1` هي:

```text
operation_authorization
decision_approval
risk_acceptance
editorial_correction
```

يمنح `operation_authorization` عملية واحدة على هدف واحد ولمرة واحدة، ولا يستطيع تفويض إدارة الأدوار أو عقد الهدف أو خطة الفريق أو منح السياق أو الموافقات نفسها. ويجب أن يطابق المستفيد إسناد دور مثبتًا والطرف الفعلي للرسالة.

يقتصر `risk_acceptance` على `human_owner` مع قدرة `accept_risk`، ويلتزم بحدود `GoalContract`. لا يتجاوز قبول المخاطر المحظورات، ولا يحول الاعتراض غير المحسوم إلى اعتراض محلول.

تحتاج `disclosed_unresolved` موافقة مخاطر فعالة تستهدف الاعتراض نفسه. ويحتاج التصحيح التحريري غير المؤثر موافقة `editorial_correction` تطابق الإصدار السابق والفرق وفحص التصنيف.

تنشأ الموافقة من مهمة في مرحلة `approval`، ويكون قرارها ونطاقها ثابتين. يجوز سحب الموافقة في إصدار تالٍ، ولا يعاد تفعيلها بعد السحب.

## 12. دورة القرار

اعتمد مخطط [Decision](DECISION.ar.md) بوصفه الحكم المعياري الذي يجمع الإصدارات المثبتة من المساهمات والأدلة والتحققات والمراجعات والاعتراضات والموافقات.

### 12.1 الحالات

```text
proposed
  → under_review | needs_evidence | rejected

under_review
  → needs_evidence | accepted | rejected

needs_evidence
  → under_review | accepted | rejected

accepted
  → reassessment_required | superseded | revoked

reassessment_required
  → accepted | superseded | revoked
```

الحالات `rejected` و`superseded` و`revoked` نهائية داخل هوية القرار. والعودة من `reassessment_required` إلى `accepted` تحتاج إصدارًا جديدًا وإعادة تأكيد مسجلة.

### 12.2 شروط القبول

لا يقبل القرار إلا إذا:

1. كان موضوعه موجودًا في `GoalContract.decision_policy`.
2. طابقت سلطة الانتقال الإسناد الذي حسمه `TeamPlan` للموضوع.
3. كان صاحب السلطة `decision_authority` أو `human_owner` بإسناد نشط وساري ونطاق يغطي العملية والموضوع.
4. كان الخيار المحدد ضمن الخيارات المدروسة.
5. ارتبط القرار بمساهمات وأدلة وتحققات ومراجعات مثبتة وحديثة.
6. وجد تحقق حاسم ومراجعة نتيجتها `approved`.
7. عولجت الاعتراضات الحرجة، أو أفصح عن اعتراض `disclosed_unresolved` مع قبول مخاطر صالح وفق عقد الهدف.
8. سجل القرار قابلية التراجع وحدوده.
9. صدر الانتقال في حدث بروتوكولي مستقل.

لا تحسم الخلافات بالتصويت العددي، ولا تمنح الثقة سلطة أو تعوض غياب الدليل أو التحقق.

### 12.3 الاعتماديات وإعادة التقييم

يسجل كل اعتماد على قرار آخر بسياسة أثر صريحة:

```text
notify | reassess | invalidate | block
```

يجب أن يكون رسم اعتماديات القرارات لا دوريًا، ولا يسمح لاعتمادية حرجة بأن تستخدم `notify` وحدها في جميع تغيرات القرار السابق.

عند تغير دليل أو تحقق أو موافقة أو عقد هدف أو قرار معتمد عليه، أو عند إعادة فتح اعتراض حرج، ينتقل القرار المتأثر إلى `reassessment_required` وينتشر الأثر عبر الاعتماديات المسجلة فقط.

لا يجوز استعمال قرار في `reassessment_required` كأساس غير مقيد لقرار جديد.

## 13. المهمة: المرحلة والحالة

اعتمد مخطط [Task](TASK.ar.md) بوصفه الحمولة القانونية لوحدة عمل واحدة. ترتبط المهمة بإصدار مثبت من `GoalContract` وإصدار مثبت من `TeamPlan`، وتحدد مسؤولها واعتمادياتها ومخرجاتها وميزانيتها الجزئية.

تفصل المهمة بين:

- `phase`: نوع العمل الثابت الذي تمثله المهمة.
- `status`: الحالة التشغيلية المتغيرة عبر الإصدارات.
- `block_reason`: سبب التوقف المؤقت.

المراحل:

```text
planning | execution | review | verification
| revision | approval | synthesis
```

في `v0.1` لا تتغير المرحلة عبر إصدارات المهمة. ينشئ الانتقال من التنفيذ إلى المراجعة أو التحقق مهمة تابعة جديدة، كي تبقى المسؤولية والميزانية والاعتماديات قابلة للتتبع.

الحالات:

```text
created | ready | assigned | in_progress
| blocked | completed | failed | cancelled
```

المسار الاعتيادي:

```text
created → ready → assigned → in_progress → completed
```

أسباب الحجب:

```text
dependency | evidence_required | revision_required
| awaiting_approval | awaiting_user | resource_unavailable
```

يجب أن تكون اعتماديات المهام مثبتة بالإصدار ومن العقد والخطة نفسيهما، وأن يكون رسمها لا دوريًا. لا تنتقل المهمة إلى `ready` أو ما بعدها قبل اكتمال كل اعتمادية مطلوبة.

يجب أن يكون المسؤول عضوًا فعليًا في `TeamPlan`، وأن يناسب دوره المرحلة. لا تتجاوز حصة المهمة الميزانية المقابلة في الخطة.

لا تنتقل المهمة إلى `completed` قبل تغطية جميع مخرجاتها الإلزامية بمراجع كيانات مثبتة وأنواع مطابقة. ويبقى فحص اعتماد القرار أو التحقق أو الاعتراضات تابعًا لمخططات تلك الكيانات عند اعتمادها.

تعلن المهمة `required_context_fields` عند حاجتها إلى سياق. لا تمنح هذه القائمة وصولًا؛ بل تحد أعلى نطاق يجوز لـ`ContextGrant` أن تصرح به. ينتهي استعمال المنحة عند اكتمال المهمة أو فشلها أو إلغائها.

إنشاء المهمة مقصور على `orchestrator` أو `human_owner`. ويجوز لمسؤول المهمة المثبت تحديثها ضمن عملية `task.next_version`، بينما لا يجوز له تحديث مهمة طرف آخر.

## 14. سجل الأحداث والحالة

سجل الجلسة من نوع `append-only`. تمثل الأحداث ما وقع، بينما تمثل العروض المشتقة الحالة الحالية.

الحد الأدنى للحدث:

```json
{
  "protocol": "w1-cip",
  "protocol_version": "0.1",
  "message_id": "evt-task-status-128",
  "session_id": "session-01",
  "kind": "event",
  "event_class": "protocol_event",
  "type": "task.status_changed",
  "actor": {
    "principal_type": "service",
    "principal_id": "orchestrator-01"
  },
  "on_behalf_of": {
    "principal_type": "agent",
    "principal_id": "executor-01"
  },
  "authorized_by": {
    "entity_type": "role_assignment",
    "entity_id": "role-assignment-44",
    "entity_version": 2
  },
  "recorded_by": {
    "principal_type": "runtime",
    "principal_id": "orchestrator-runtime-01"
  },
  "received_at": "2026-08-02T18:20:00Z",
  "recorded_at": "2026-08-02T18:20:00Z",
  "sequence": 128,
  "correlation_id": "run-01",
  "causation_id": "msg-task-status-127",
  "related_events": [],
  "entity": {
    "entity_type": "task",
    "entity_id": "task-7",
    "entity_version": 4
  },
  "precondition": {
    "mode": "next_version",
    "expected_entity_version": 3,
    "target": {
      "entity_type": "task",
      "entity_id": "task-7",
      "entity_version": 3
    }
  },
  "payload_schema": "urn:w1-cip:schema:0.1:versioned-entity",
  "payload": {
    "entity": {
      "entity_type": "task",
      "entity_id": "task-7",
      "entity_version": 4
    },
    "created_by_event_id": "evt-128",
    "legal_payload_schema": "urn:w1-cip:entity-payload:0.1:task",
    "legal_payload": {
      "phase": "verification",
      "status": "blocked",
      "block_reason": "evidence_required"
    },
    "previous_version": {
      "entity_type": "task",
      "entity_id": "task-7",
      "entity_version": 3
    },
    "change_metadata": {
      "change_reason": "semantic",
      "substantive_effect": "may_affect_dependents"
    }
  }
}
```

القواعد:

1. تحدد `session_id + sequence` الترتيب الرسمي، ولا يحسم الطابع الزمني تعارضًا معها.
2. تتزايد `sequence` داخل الجلسة من دون تكرار.
3. يميز السجل بين `occurred_at` و`received_at` و`recorded_at`، ويعين المنظم الوقتين الأخيرين. ويكون `recorded_by` مرجع Runtime موثوقًا.
4. لا يحذف حدث مقبول ولا يستبدل.
5. يصحح الخطأ بحدث تعويضي يشير إلى الحدث السابق.
6. يجب أن تكفي الأحداث المقبولة لإعادة بناء الحالة.
7. يسجل المنظم رفض رسالة غير صالحة أو تعديل ذي `expected_entity_version` قديم كحدث تشغيلي، من دون دمجه في حالة البروتوكول.
8. يكون سند `authorized_by` مثبتًا بإصدار وساريًا عند `recorded_at`.
9. يدخل `protocol_event` وحده في إعادة بناء الحالة؛ الأحداث التشغيلية والأمنية لا تغيرها مباشرة.
10. يحدد `causation_id` السبب المباشر، ولا تمنح `related_events` سببية أو ترتيبًا أو صلاحية.
11. لا تنسخ البيانات الحساسة إلى حمولة سجل التدقيق؛ تحفظ مراجع محدودة أو بيانات حجب مجردة.
12. التوقيع والتجزئة المتسلسلة مؤجلان، ولا يجوز وصف سجل `v0.1` بأنه غير قابل للعبث تشفيريًا.
13. الاستثناء الوحيد لغياب `authorized_by` هو حدث `session.bootstrap.completed` الأول؛ تتحقق طبقة الجلسة من أنه `sequence: 1` وأن Runtime المسجل موثوق وأنه أنشأ `role_assignment` الأول فقط.
14. يرفض أي استعمال لاحق لـ`bootstrap` أو أي حدث تأسيس ثانٍ داخل الجلسة.
15. يحمل حدث إنشاء الكيان أو الإصدار التالي `VersionedEntity` كاملًا، ويطابق كيان الغلاف والحدث المنشئ وشرط السلسلة.
16. لا يقبل مخزن الجلسة مفتاح إصدار سبق تثبيته، ولا يبني إصدارًا تاليًا على أساس قديم.

## 15. النتيجة النهائية

يفصل `FinalResult` بين حالة الإنجاز وحالة نشر السجل.

حالة الإنجاز:

- `succeeded`: غطيت المخرجات ومعايير النجاح المطلوبة ولا توجد فجوة مخفية.
- `partially_succeeded`: تحقق جزء من الهدف مع قيود أو نواقص مصرح بها.
- `failed`: فشل مخرج أو معيار مطلوب.
- `blocked`: لا يوجد مسار آمن للإكمال أو يلزم تدخل المستخدم.
- `cancelled`: أوقفت الجلسة قبل الإكمال.

حالة النشر:

- `published`
- `reassessment_required`
- `superseded`
- `withdrawn`

ويجب أن يتضمن الكيان:

- تقييمًا لكل مخرج ومعيار نجاح في `GoalContract`.
- القرارات المعتمدة.
- الادعاءات المثبتة والمدحوضة وغير المحسومة.
- الأدلة والتحققات والمراجعات والقيود.
- الاعتراضات المعالجة وغير المعالجة.
- الموافقات وقبول المخاطر.
- إصدار `ExecutionResourcePlan` وحالات الموارد وتحويلات التوجيه.
- مرساة آخر تسلسل وحدث استعملا لبناء النتيجة.

لا يجوز للمركب تغيير حالة قرار أو نتيجة تحقق أو حذف قيد. لا تعاد كتابة نتيجة منشورة عند تغير الأساس؛ تسجل إعادة التقييم في إصدار تالٍ، وتنتج إعادة التركيب هوية `FinalResult` جديدة. راجع [وثيقة FinalResult](FINAL-RESULT.ar.md).

## 16. ثوابت التوافق

تطبق [الثوابت التأسيسية](FOUNDATIONAL-SEMANTICS.ar.md#13-الثوابت-التأسيسية) كاملة. ويعد التطبيق غير متوافق مع `W1-CIP v0.1` إذا سمح بأي مما يأتي:

- اعتماد قرار من جهة غير مخولة.
- إغلاق الاعتراض بواسطة الوكيل المستهدف وحده.
- إعلان تحقق ناجح من دون طريقة ودليل.
- إرسال سياق إلى وكيل من دون منحة صالحة.
- حذف حدث مقبول أو تعديل تسلسله.
- إخفاء اعتراض حرج داخل نتيجة مكتملة.
- الخلط بين سلامة مصدر الدليل وصحة الاستنتاج.
- استعمال مرجع غير مثبت بإصدار داخل قرار أو اعتراض أو تحقق محفوظ.
- استبدال إصدار مقبول من كيان أو دليل بصمت.
- قبول تعديل لا يطابق `expected_entity_version`.
- ترتيب الأحداث بالطابع الزمني خلافًا لـ`sequence`.
- مقارنة درجات ثقة غير معايرة كما لو كانت على مقياس واحد.
- استعمال قرار حالته `reassessment_required` كأساس غير مقيد لقرار جديد.
- تعديل إصدار مقبول في مكانه ولو طابق `expected_entity_version`.
- إنشاء كيان بهوية مستخدمة داخل الجلسة.
- إنشاء دورة في اعتماديات القرارات.
- اعتماد `editorial_correction` غير مؤثر من دون صلاحية مستقلة وفحص مسجل.
- إدخال حدث تشغيلي أو أمني مباشرة في الحالة المعيارية.
- نسخ محتوى حساس داخل سجل التدقيق.
- إعلان `FinalResult.succeeded` مع مخرج مطلوب غير مكتمل أو ادعاء غير محسوم مخفي.
- حذف تحويل مورد أو خفض قدرة أو نفاد حصة من ملخص الموارد المنشور.
- إعادة كتابة حمولة نتيجة منشورة بدل إنشاء إعادة تقييم أو نتيجة بديلة.

## 14.1 ProtocolEvent المعياري

اعتمدت حمولة `ProtocolEvent` لتسجيل الآثار المعيارية المشتقة مثل إعادة التقييم والإبطال والحجب وتعطيل السلطة أو السياق. لكل حدث إصدار واحد فقط، ويطابق معرف كيانه `message_id` في الغلاف.

لا يعدل الحدث الكيان المستهدف؛ بل يسجل التزامًا تشغيليًا يظل حاجبًا إلى أن ينشأ إصدار قانوني تالٍ أو حدث تعويضي صالح. التعويض لا يحذف الحدث، بل يزيل أثره من الإسقاط النشط.

يكون `sequence` هو الترتيب الرسمي، ويجب أن تشير السببية والعلاقات إلى أحداث سابقة. ويعيد التنفيذ المرجعي بناء الإصدارات والآثار النشطة والتعويضات من السجل نفسه. التفاصيل في [وثيقة ProtocolEvent](PROTOCOL-EVENT.ar.md).



## إدارة تفاوت النماذج وحصص الاستخدام

لا تعامل الحوكمة النماذج كأصوات متساوية. يحدد `ExecutionResourcePlan` ملاءمة كل مورد للمهمة، وحد الجودة، والحصة المتبقية والاحتياطي وترتيب البدائل. إذا نفدت حصة المورد الأساسي، يسجل إصدار جديد التحويل أو تحجب المهمة؛ ولا يسمح باستبدال صامت. تبقى سلطة القرار مستمدة من `RoleAssignment` ومدعومة بالأدلة والتحقق، لا من عدد النماذج المؤيدة. راجع [ExecutionResourcePlan](EXECUTION-RESOURCE-PLAN.ar.md).
