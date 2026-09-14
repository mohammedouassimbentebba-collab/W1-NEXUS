# VersionedEntity — W1-CIP v0.1

## الحالة

`VersionedEntity` هو العقد التأسيسي الرابع في `W1-CIP v0.1`. يمثل إصدارًا مقبولًا وغير قابل للتعديل من كيان معياري، ولا يمثل أمر التعديل أو فرقًا مختصرًا بين نسختين.

معرف المخطط:

```text
urn:w1-cip:schema:0.1:versioned-entity
```

يستخدم المخطط JSON Schema Draft 2020-12 ويعتمد [EntityRef](ENTITY-REF.ar.md) للهوية المثبتة والإصدارات السابقة.

## البنية القانونية

يحمل كل سجل:

```yaml
entity:
  entity_type: contribution
  entity_id: claim-21
  entity_version: 2
created_by_event_id: evt-claim-versioned-002
legal_payload_schema: urn:w1-cip:entity-payload:0.1:contribution
legal_payload:
  goal: {entity_type: goal_contract, entity_id: goal-01, entity_version: 1}
  team_plan: {entity_type: team_plan, entity_id: team-plan-01, entity_version: 1}
  task: {entity_type: task, entity_id: task-01, entity_version: 4}
  authored_by_role_assignment:
    {entity_type: role_assignment, entity_id: role-assignment-17, entity_version: 2}
  contribution_type: claim
  status: active
  classification: internal
  content:
    statement: candidate-a does not support the required startup current
    verifiability: deterministic
    submission_verification_status: unverified
  limitations: []
previous_version:
  entity_type: contribution
  entity_id: claim-21
  entity_version: 1
change_metadata:
  change_reason: semantic
  substantive_effect: may_affect_dependents
```

- `entity`: المرجع المثبت للإصدار الحالي.
- `created_by_event_id`: معرف `ProtocolEnvelope.message_id` للحدث الذي قبل هذا الإصدار.
- `legal_payload_schema`: مخطط الحمولة القانونية الخاصة بنوع الكيان.
- `legal_payload`: الحمولة القانونية الكاملة، وليست فرقًا عن الإصدار السابق.
- `previous_version`: الإصدار السابق المباشر؛ يوجد فقط بدءًا من الإصدار `2`.
- `change_metadata`: سبب التغيير وأثره؛ يوجد فقط بدءًا من الإصدار `2`.

لا يحمل السجل `session_id` لأن مخزن الجلسة أو الغلاف الحاوي يحدد سياقه، كما لا يحمل بيانات العرض أو الفهرسة المشتقة.

## الإصدار الأول والإصدارات التالية

### الإصدار الأول

إذا كان `entity.entity_version: 1`:

- يمنع `previous_version`.
- يمنع `change_metadata`.
- يجب أن يقبل عبر شرط إنشاء `expected_absent: true` في الغلاف.

### الإصدار التالي

إذا كان الإصدار أكبر من `1`:

- يجب وجود `previous_version` بالهوية نفسها.
- يجب أن يكون رقم الإصدار السابق هو الحالي ناقص واحد.
- يجب وجود `change_metadata`.
- يجب أن يطابق الإصدار السابق السجل الفعلي الأحدث عند القبول؛ وإلا يرفض بـ`version_conflict`.

لا يسمح بإنشاء إصدار جديد ذي حمولة قانونية ومخطط حمولة مطابقين تمامًا للإصدار السابق؛ فالتغييرات المشتقة أو العرضية تحفظ خارج هذا العقد.

## الحمولة القانونية والـDiff

كل إصدار يخزن حمولة قانونية كاملة مستقلة. لا يعاد بناء المصدر القانوني من سلسلة فروق.

يجوز أن يسجل `diff_artifact` كأثر مشتق للمراجعة:

```yaml
diff_artifact:
  uri: artifact://diffs/claim-21-v1-v2
  artifact_version: 1
```

المرجع البرمجي الحالي يتحقق من وجود المرجع وبنيته، أما التحقق من أن محتوى الأثر يطابق الفرق الحقيقي بين الحمولتين فيحتاج محلل آثار ومخزن جلسة لاحقًا.

## تصنيف التغيير

القيم المسموحة:

```text
change_reason:
  semantic | editorial_correction

substantive_effect:
  may_affect_dependents | none
```

القيمة المحافظة الافتراضية هي `may_affect_dependents`. ولأن `VersionedEntity` يمثل سجلًا مقبولًا قابلًا لإعادة الإنتاج، يجب أن تخزن القيمة صراحة. يجوز لمعالج الأمر أن يملأ الافتراضي قبل قبول الحدث.

لا يسمح لـ`semantic` باستخدام `substantive_effect: none`.

## التصحيح التحريري غير المؤثر

لا يقبل `substantive_effect: none` إلا مع:

1. `change_reason: editorial_correction`.
2. `diff_artifact` مثبت الإصدار.
3. `classification_approval` من نوع `approval` مثبت الإصدار.
4. `classification_check` من نوع `review` أو `verification` مثبت الإصدار.
5. بقاء `legal_payload_schema` مطابقًا للإصدار السابق.

مثال:

```yaml
change_metadata:
  change_reason: editorial_correction
  substantive_effect: none
  diff_artifact:
    uri: artifact://diffs/claim-21-v2-v3
    artifact_version: 1
  classification_approval:
    entity_type: approval
    entity_id: approval-editorial-003
    entity_version: 1
  classification_check:
    entity_type: review
    entity_id: review-editorial-003
    entity_version: 1
```

لا يثبت المخطط وحده استقلال الجهة. اعتمد `RoleAssignment` وأصبح بالإمكان التحقق من امتلاك الطرف قدرة `classify_editorial_correction` وحالة الإسناد ومدته؛ ويبقى فحص الاستقلال عن مؤلف التغيير وربط `Approval` نفسه للمرحلة اللاحقة.

عندما يكون التصحيح التحريري موسومًا `may_affect_dependents` لا يحتاج بوابة الاستثناء هذه؛ يعامل محافظًا كتغيير قد يستدعي إعادة التقييم. ويجوز مع ذلك إرفاق `classification_approval` و`classification_check` لأغراض التدقيق. أما التغيير `semantic` فلا يستخدم حقول التصنيف التحريري.

## التكامل مع ProtocolEnvelope

عند قبول إنشاء كيان أو إصدار تالٍ، يستخدم الحدث البروتوكولي:

```text
payload_schema: urn:w1-cip:schema:0.1:versioned-entity
```

ويحمل `payload` سجل `VersionedEntity` كاملًا. يفرض الغلاف عندئذ أن يكون:

- `kind: event`.
- `event_class: protocol_event`.
- `payload.entity` مطابقًا لـ`envelope.entity`.
- `payload.created_by_event_id` مطابقًا لـ`message_id`.
- `payload.previous_version` مطابقًا لهدف شرط `next_version`.

أما الأوامر فتبقى حمولتها مخططات الطلبات الخاصة بالكيانات، ولا تحمل سجلًا مقبولًا قبل نجاح التحقق والتسجيل.

## طبقات التحقق

### JSON Schema

يفرض:

- الحقول المطلوبة ومنع الخصائص المجهولة.
- صحة `EntityRef` ومخططات URI.
- فصل الإصدار الأول عن الإصدارات التالية.
- بوابة `substantive_effect: none`.
- أنواع مراجع الموافقة والفحص.

### التحقق الدلالي المحلي

يفرض:

- تطابق هوية `previous_version` مع الكيان الحالي.
- أن يكون الإصدار السابق مباشرًا.
- تطابق السجل مع غلاف الحدث المنشئ.

### تحقق الانتقال

يقارن السجل بالإصدار السابق الفعلي، ويرفض:

- فقدان السجل السابق.
- اختلاف المرجع المعلن عن السجل السابق.
- إعادة استعمال الحمولة القانونية نفسها دون تغيير.
- تغيير `legal_payload_schema` مع `substantive_effect: none`.

### تكامل حمولات الكيانات

إذا كان نوع الكيان `role_assignment` يفرض المخطط `legal_payload_schema: urn:w1-cip:entity-payload:0.1:role-assignment` ويتحقق من الحمولة بمخطط [RoleAssignment](ROLE-ASSIGNMENT.ar.md). ويمنع استعمال هذا المخطط مع نوع كيان آخر.

### تحقق المخزن

يفرض:

- `entity_already_exists` عند محاولة إنشاء هوية موجودة.
- `version_conflict` عند البناء على إصدار غير أحدث إصدار.
- `immutable_version` عند محاولة قبول مفتاح إصدار سبق تثبيته.

## رموز الأخطاء الحالية

محلية وانتقالية:

- `previous_identity_mismatch`
- `previous_version_not_immediate`
- `previous_record_required`
- `previous_record_ref_mismatch`
- `previous_record_identity_mismatch`
- `current_version_not_next`
- `legal_payload_unchanged`
- `editorial_none_schema_changed`

تكامل الغلاف:

- `versioned_entity_result_mismatch`
- `versioned_entity_creator_event_mismatch`
- `versioned_entity_previous_mismatch`

حالة المخزن:

- `entity_already_exists`
- `entity_not_found`
- `version_conflict`
- `immutable_version`

## ما يبقى مؤجلًا

- اعتمدت مخططات الحمولات القانونية الأساسية في `v0.1`، بما فيها `ProtocolEvent` و`ExecutionResourcePlan` و`FinalResult`، إضافة إلى العقود السابقة.
- محلل ديناميكي للمخططات الخارجية عن نواة `v0.1`; أما الأنواع المعتمدة حاليًا فتتحقق حمولاتها عبر مراجع المخططات المثبتة.
- التحقق من محتوى `diff_artifact` وسلامته.
- فحص استقلال جهة التصنيف وربط سجل `Approval`; أصبح نطاق `RoleAssignment` قابلًا للتحقق.
- أصبح التخزين الدائم والمعاملة الذرية بين قبول الحدث وتثبيت الإصدار منفذين في [SessionStore](SESSION-STORE.ar.md).

## تكامل AgentCard

إذا كان `entity.entity_type: agent_card` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:agent-card` ويتحقق من الحمولة بمخطط [AgentCard](AGENT-CARD.ar.md). ويمنع استعمال مخطط البطاقة مع نوع كيان آخر.


## تكامل GoalContract

إذا كان `entity.entity_type: goal_contract` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:goal-contract` ويتحقق من الحمولة بمخطط [GoalContract](GOAL-CONTRACT.ar.md). ويمنع استعمال مخطط العقد مع نوع كيان آخر. وتطبق فوق البنية قواعد التتبع بين المخرجات والقيود ومعايير النجاح وسياسة القرار.

## تكامل TeamPlan

إذا كان `entity.entity_type: team_plan` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:team-plan` ويتحقق من الحمولة بمخطط [TeamPlan](TEAM-PLAN.ar.md). ويمنع استعمال مخطط الخطة مع نوع كيان آخر، أو استعمال كيان `team_plan` مع مخطط حمولة مختلف.


## تكامل ContextGrant

إذا كان `entity.entity_type: context_grant` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:context-grant` ويتحقق من الحمولة بمخطط [ContextGrant](CONTEXT-GRANT.ar.md). ويمنع استعمال مخطط المنحة مع نوع كيان آخر أو استعمال كيان `context_grant` مع مخطط حمولة مختلف.

## تكامل Task

إذا كان `entity.entity_type: task` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:task` ويتحقق من الحمولة بمخطط [Task](TASK.ar.md). ويمنع استعمال مخطط المهمة مع نوع كيان آخر، أو استعمال كيان `task` مع مخطط حمولة مختلف.


## تكامل Contribution

إذا كان `entity.entity_type: contribution` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:contribution` ويتحقق من الحمولة بمخطط [Contribution](CONTRIBUTION.ar.md). ويمنع استعمال مخطط المساهمة مع نوع كيان آخر، أو استعمال كيان `contribution` مع مخطط حمولة مختلف.


## تكامل Evidence

إذا كان `entity.entity_type: evidence` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:evidence` ويتحقق من الحمولة بمخطط [Evidence](EVIDENCE.ar.md). ويمنع استعمال مخطط الدليل مع نوع كيان آخر، أو استعمال كيان `evidence` مع مخطط حمولة مختلف.

## تكامل Verification

إذا كان `entity.entity_type: verification` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:verification` ويتحقق من الحمولة بمخطط [Verification](VERIFICATION.ar.md).

## تكامل Challenge

إذا كان `entity.entity_type: challenge` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:challenge` ويتحقق من الحمولة بمخطط [Challenge](CHALLENGE.ar.md).

## تكامل Review

إذا كان `entity.entity_type: review` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:review` ويتحقق من الحمولة بمخطط [Review](REVIEW.ar.md). ويمنع استعمال مخطط المراجعة مع نوع كيان آخر، أو استعمال كيان `review` مع مخطط حمولة مختلف.

## التحقق من موافقة التصحيح التحريري

بعد اعتماد `Approval` أصبح المرجع البرمجي يتحقق من أن `classification_approval` فعالة ومن النوع `editorial_correction`، وأنها تستهدف الإصدار السابق وتطابق `diff_artifact` و`classification_check` المسجلين في `change_metadata`.


## تكامل Decision

إذا كان `entity.entity_type: decision` يفرض السجل `legal_payload_schema: urn:w1-cip:entity-payload:0.1:decision` ويتحقق من الحمولة بمخطط [Decision](DECISION.ar.md). ويمنع استعمال مخطط القرار مع نوع كيان آخر أو استعمال كيان `decision` مع مخطط حمولة مختلف.

## ProtocolEvent كاستثناء أحادي الإصدار

تستخدم `ProtocolEvent` غلاف `VersionedEntity` لتوحيد التثبيت والمرجعية، لكنها لا تملك سلسلة إصدارات. يجب أن يكون الإصدار `1` دائمًا، ويمنع `previous_version` و`change_metadata`. أي تصحيح يسجل حدث تعويض جديدًا.


## FinalResult

تستخدم النتيجة النهائية `VersionedEntity`. يبقى محتوى النشر الأصلي ثابتًا، وتقتصر الإصدارات اللاحقة على دورة النشر مثل `reassessment_required` أو `superseded` أو `withdrawn`. إعادة تركيب النتيجة تنشئ هوية جديدة.
