# AgentCard — W1-CIP v0.1

## الحالة

`AgentCard` هي الحمولة القانونية التي تصف وكيلاً تقنيًا داخل الجلسة قبل اختياره أو منحه دورًا. تسجل المزود والنموذج والقدرات والأدوات والأدوار المؤهلة والقيود وسياسة البيانات ومستوى الاستقلالية والتكلفة والزمن والحالة التشغيلية.

معرف مخطط الحمولة:

```text
urn:w1-cip:entity-payload:0.1:agent-card
```

تحفظ البطاقة داخل `VersionedEntity` عندما يكون `entity.entity_type: agent_card`. يكون `entity.entity_id` هو نفسه `principal_id` للطرف من النوع `agent`؛ لذلك لا تكرر الحمولة حقل `agent_id` أو `card_version`.

## البنية

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

## معنى الحقول

- `display_name`: اسم عرض، وليس هوية قانونية.
- `provider`: اسم مزود canonical ومعرف النموذج الخارجي كما يعلنه المزود؛ يحفظ معرف النموذج بدقته ولا يخضع لقاعدة lowercase الخاصة بهويات W1.
- `capabilities.domains`: المجالات التي يعلن الوكيل ملاءمته لها.
- `capabilities.operations`: أنواع المعالجة التي يعلن قدرته عليها، مثل الاستدلال أو الإخراج المنظم.
- `tools`: الأدوات التي يعلن إمكانية استخدامها؛ لا يعني ذكر الأداة أن الوصول إليها ممنوح بالفعل.
- `eligible_roles`: الأدوار التي يجوز للمنظم النظر في إسنادها إلى الوكيل.
- `prohibited_uses`: استخدامات يمنع إسنادها إلى الوكيل حتى إن بدت قدراته مناسبة.
- `permissions`: حدود القراءة والكتابة والأفعال التي يستطيع التنفيذ التقني إتاحتها.
- `data_policy`: تصنيفات البيانات المقبولة وهل تجري المعالجة خارجيًا وهل يسمح بإعادة المشاركة.
- `autonomy`: حد الاستقلالية والأفعال التي تبقى محتاجة إلى موافقة بشرية.
- `cost` و`latency`: فئات وصفية للتخطيط في `v0.1`، وليستا تعهدًا ماليًا أو زمنيًا دقيقًا.
- `limitations`: قيود معلنة يجب أن تبقى ظاهرة عند تشكيل الفريق.
- `status`: `active` أو `suspended` أو `retired`.

## البطاقة تصريح وليست إثباتًا

وجود قدرة أو أداة أو مجال داخل البطاقة لا يثبت أن الوكيل يحقق أداءً معينًا. البطاقة تسجل ما أعلن عنه المزود أو المشغل وما قبله مسجل الجلسة. الأدلة التجريبية، ونتائج التقييم، ومعايرة الأداء خارج هذه الحمولة أو تسجل لاحقًا ككيانات مستقلة مثبتة بالإصدار.

لا يجوز للمنظم:

- افتراض قدرة أو أداة أو إذن غير مصرح به.
- اعتبار `eligible_roles` إسنادًا فعليًا للدور.
- تحويل القدرة المعلنة إلى صلاحية؛ الصلاحية تأتي فقط من `RoleAssignment`.
- تجاوز `prohibited_uses` أو `data_policy` بسبب ارتفاع الثقة أو انخفاض التكلفة.

## العلاقة مع PrincipalRef

يمثل الوكيل في الرسائل هكذا:

```yaml
principal_type: agent
principal_id: agent-electronics-01
```

ولكي يرتبط هذا الطرف ببطاقة، يجب أن تكون البطاقة:

```yaml
entity_type: agent_card
entity_id: agent-electronics-01
```

تطابق المعرفين شرط دلالي. لا يحمل `PrincipalRef` إصدار البطاقة؛ يسجل الإصدار الذي اعتمد عليه الاختيار داخل `RoleAssignment.basis`.

## العلاقة مع RoleAssignment

عندما يكون `RoleAssignment.subject.principal_type: agent` يجب أن يحتوي `basis` على مرجع واحد فقط إلى `AgentCard`:

```yaml
basis:
  - entity_type: agent_card
    entity_id: agent-electronics-01
    entity_version: 1
  - entity_type: team_plan
    entity_id: team-plan-01
    entity_version: 1
```

ويتحقق المنظم من:

1. وجود البطاقة المشار إليها.
2. تطابق معرف البطاقة مع `subject.principal_id`.
3. كون البطاقة `active`.
4. إدراج الدور المطلوب في `eligible_roles`.
5. عدم وجود إصدار دلالي أحدث من البطاقة دون تحديث الإسناد.

إذا ظهرت إصدارات أحدث مصنفة كلها `editorial_correction` مع `substantive_effect: none` يبقى الإسناد صالحًا؛ لأنها لا تغير الأساس الدلالي. أما أي إصدار أحدث `may_affect_dependents` فيجعل الإسناد يحتاج إلى بطاقة مثبتة أحدث وإعادة تقييم.

إذا تغيرت البطاقة إلى `suspended` أو `retired` يمنع استعمال إسناد الدور المدعوم بها، حتى لو بقي إسناد الدور نفسه `active`.

## الأدوار المؤهلة

القيم المسموحة للوكيل:

```text
orchestrator | executor | reviewer | verifier
| decision_authority | synthesizer
```

لا يجوز إدراج `human_owner`؛ فهو دور بشري جذري. ولا تعني أهلية `decision_authority` أن الوكيل حصل على سلطة قرار؛ يجب أن يمنحه `RoleAssignment` نطاقًا صريحًا، وقد تمنع سياسة المهمة الاعتماد الآلي أصلًا.

## سياسة البيانات

تصنيفات `v0.1`:

```text
public | internal | confidential | restricted
```

أصبح تطابق سياسة البطاقة مع `ContextGrant` مطبقًا: يجب قبول التصنيف، ووجود إذن `granted_context`، والتصريح بالمعالجة الخارجية عندما تعلن البطاقة استخدامها. لا يسمح `accepted_classifications` وحده بالوصول؛ الاستعمال يحتاج منحة صالحة ومطابقة للمهمة والغرض والحقول.

`external_processing: true` يفصح عن أن البيانات قد تغادر بيئة التشغيل المحلية. ولا يعني `onward_sharing: false` أن المزود أثبت تقنيًا عدم المشاركة؛ إنها سياسة معلنة يجب أن يفرضها الموصل أو العقد الخارجي.

## الحالة ودورة الحياة

```text
active | suspended | retired
```

- `active`: يجوز استعمال البطاقة في اختيار الوكيل إذا استوفت بقية الشروط.
- `suspended`: يمنع الاختيار والاستعمال مؤقتًا، ويلزم `status_reason`.
- `retired`: سحب نهائي لهوية البطاقة، ويلزم `status_reason`.

`retired` نهائية؛ لا ينشأ إصدار لاحق للهوية نفسها. إذا عاد التنفيذ تحت هوية تشغيلية جديدة، ينشأ `agent_card` جديد بمعرف جديد.

كل تغيير في البطاقة ينشئ إصدارًا جديدًا كاملًا. تغيير النموذج أو القدرات أو الأدوات أو الأدوار المؤهلة أو القيود أو سياسة البيانات أو الاستقلالية أو الحالة تغيير دلالي عادةً ويستخدم `may_affect_dependents`. تصحيح اسم العرض فقط قد يصنف تحريريًا وفق شروط `VersionedEntity` الصارمة.

## التكامل مع VersionedEntity

إذا كان نوع الكيان `agent_card` يفرض `VersionedEntity`:

```text
legal_payload_schema: urn:w1-cip:entity-payload:0.1:agent-card
```

وبالعكس لا يجوز استعمال مخطط الحمولة هذا مع نوع كيان آخر.

## رموز الأخطاء الحالية

- `agent_card_basis_required`
- `agent_card_basis_ambiguous`
- `agent_card_subject_mismatch`
- `agent_card_basis_for_non_agent`
- `agent_card_not_found`
- `agent_card_lineage_incomplete`
- `agent_card_version_not_current`
- `agent_card_payload_schema_mismatch`
- `agent_card_inactive`
- `agent_role_not_eligible`
- `agent_card_retired_final`

## ما يؤجل

- إثبات قدرات الوكيل تجريبيًا أو تشفيريًا.
- سجل عالمي للوكلاء.
- اكتشاف الأدوات ومصافحة القدرات بين المجالات.
- تسعير رقمي موحد أو ضمانات زمن استجابة.
- مطابقة `permissions` و`data_policy` مع `ContextGrant` و`TeamPlan` و`Task` أصبحت منفذة، بما في ذلك توقف المنحة عند نهاية المهمة أو تغير مسؤولها.


## الحصة ليست جزءًا ثابتًا من AgentCard

بطاقة الوكيل تصف النموذج والسياسات والقدرات المعلنة، لكن حد الاستخدام المتبقي خاص بالحساب والزمن. لذلك يسجل داخل `ExecutionResourcePlan`. وقد يملك النموذج نفسه حصصًا مختلفة لمستخدمين مختلفين. كما أن المقارنة بين النماذج في خطة الموارد خاصة بالمهمة ولا تعد ترتيبًا عالميًا ثابتًا.
