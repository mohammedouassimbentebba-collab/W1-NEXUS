# RoleAssignment — W1-CIP v0.1

## الحالة

`RoleAssignment` هو أول مخطط حمولة قانونية لكيان أساسي بعد العقود التأسيسية. يربط طرفًا محددًا بدور جلسي وصلاحيات صريحة ومدة سريان، وبذلك يصبح `authorized_by` قابلًا للتحقق بدل أن يكون مجرد `EntityRef` صحيح البنية.

معرّف مخطط الحمولة القانونية:

```text
urn:w1-cip:entity-payload:0.1:role-assignment
```

لا يمثل المخطط كيانًا مستقلًا خارج نظام الإصدارات؛ بل يحفظ داخل `VersionedEntity.legal_payload` عندما يكون `entity.entity_type: role_assignment`.

## البنية

```yaml
subject:
  principal_type: agent
  principal_id: agent-electronics-01
role: executor
authority_scope:
  operations:
    - contribution.create
    - contribution.next_version
status: active
valid_from: 2026-08-04T15:02:00Z
valid_until: 2026-08-04T17:00:00Z
assignment_reason: prepare the component-selection proposal
basis:
  - entity_type: agent_card
    entity_id: agent-electronics-01
    entity_version: 1
  - entity_type: team_plan
    entity_id: team-plan-01
    entity_version: 1
```

- `subject`: الطرف الذي تمنح له الصلاحية، باستخدام `PrincipalRef`.
- `role`: الوظيفة الحوكمية داخل الجلسة.
- `authority_scope`: الصلاحيات الصريحة، ولا تستنتج من اسم الدور وحده.
- `status`: حالة الإسناد المسجلة.
- `valid_from` و`valid_until`: نافذة الصلاحية الزمنية؛ نهاية المدة غير شاملة.
- `assignment_reason`: سبب الإسناد المسجل.
- `basis`: مراجع مثبتة تبرر الإسناد. عندما يكون `subject` وكيلاً يجب أن تتضمن مرجعًا واحدًا إلى `AgentCard` مطابق المعرف؛ ويمكن أن تتضمن أيضًا خطة فريق أو عقد هدف.

## الأطراف المسموح بإسناد الدور إليها

تقبل `v0.1` الأنواع الآتية بوصفها `subject`:

```text
agent | human | service
```

لا يسند الدور مباشرة إلى `runtime` أو `tool`:

- الـRuntime يسجل الأحداث ولا يكتسب سلطة بسبب التسجيل.
- الأداة تعمل تحت سلطة الطرف الذي استدعاها ولا تصبح صاحب قرار مستقلًا.

ويجب أن يكون `human_owner` طرفًا من النوع `human`.

عندما يكون الطرف `agent` يجب أن يثبت `basis` بطاقة من النوع `agent_card` تحمل `entity_id` نفسه. ولا يكفي وجود البطاقة: يجب أن تكون نشطة وأن تدرج الدور ضمن `eligible_roles`. التحديث الدلالي الأحدث للبطاقة يوقف أهلية الإسناد حتى تحديثه، بينما لا تفعل ذلك التصحيحات التحريرية المعتمدة غير المؤثرة.

## الأدوار

```text
orchestrator
executor
reviewer
verifier
decision_authority
synthesizer
human_owner
```

اسم الدور لا يمنح صلاحيات تلقائية. يجب تسجيل كل صلاحية داخل `authority_scope`.

## نطاق الصلاحية

### العمليات

`authority_scope.operations` قائمة عمليات دقيقة، مثل:

```yaml
authority_scope:
  operations:
    - contribution.create
    - contribution.next_version
```

لا توجد wildcards في `v0.1`. لا تعني صلاحية `contribution.create` صلاحية إنشاء `evidence` أو `decision`. ويجب أن يحتوي النطاق على عملية واحدة أو قدرة واحدة على الأقل؛ `decision_subjects` قيد وليست منحة سلطة مستقلة.

بالنسبة إلى الأوامر والأحداث التي تغير كيانًا، تشتق العملية المطلوبة من هدف `precondition`:

```text
create       → <entity_type>.create
next_version → <entity_type>.next_version
```

لذلك لا تستخدم قيمة الحدث العامة `entity.version.created` لتوسيع السلطة عبر جميع أنواع الكيانات. فحدث إنشاء الإصدار الثاني من `contribution` يحتاج `contribution.next_version`.

أما الأحداث التشغيلية أو الأمنية التي لا تحمل `precondition` فتستخدم قيمة `type` نفسها بوصفها العملية المطلوبة.

### القدرات الخاصة

تدعم هذه الشريحة قدرتين مثبتتين في الحوكمة:

```text
classify_editorial_correction
accept_risk
```

- `classify_editorial_correction` لا تمنح في `v0.1` إلا لدور `reviewer` أو `verifier` أو `human_owner`. ويبقى شرط الاستقلال عن مؤلف التغيير مطلوبًا عند فحص الحالة الفعلية.
- `accept_risk` لا تمنح إلا لـ`human_owner`.

### موضوعات القرار

إذا كان الدور `decision_authority` يجب أن يسجل:

```yaml
decision_subjects:
  - motor_driver_selection
```

ويمنع الحقل عن بقية الأدوار. وجود الموضوع لا يعوض العملية المطلوبة؛ بل يقيد فئة القرار التي يجوز لصاحب الدور اعتمادها عندما يصمم مخطط `Decision`.

## إدارة إسنادات الأدوار

في `v0.1` لا يجوز أن تحتوي `operations` على:

```text
role_assignment.create
role_assignment.next_version
```

إلا عندما يكون الدور `human_owner`.

هذا يمنع المنفذ أو المنظم أو المراجع من منح نفسه دورًا أو توسيع صلاحياته. لا يطبق النظام تفويضًا متسلسلًا عامًا في هذه النسخة؛ إدارة الأدوار تبقى جذرية لدى المالك البشري.

## التحقق من authorized_by

عند استعمال `EntityRef` من نوع `role_assignment` داخل `authorized_by` يتحقق المرجع البرمجي من الآتي:

1. وجود الإصدار المشار إليه في مخزن الجلسة.
2. كونه أحدث إصدار مقبول لذلك الإسناد عند لحظة القبول. يجب أن تمثل حالة التحقق لقطة الجلسة السابقة مباشرة للغلاف الجاري، لا آخر حالة مستقبلية بعد اكتمال السجل.
3. استخدامه مخطط حمولة `RoleAssignment` الصحيح.
4. تطابق `subject` مع الطرف الفعلي: `on_behalf_of` عند وجوده وإلا `actor`.
5. كون `status: active`.
6. وقوع لحظة القبول داخل نافذة `valid_from <= time < valid_until`.
7. احتواء `authority_scope.operations` على العملية المطلوبة بدقة.
8. بقاء إدارة الأدوار محصورة في `human_owner`.

تقيم الأحداث عند `recorded_at`. أما الأمر، لأنه لا يحمل `recorded_at` بعد، فيمرر الـRuntime لحظة القبول المقترحة صراحة إلى المدقق؛ ولا يعتمد المدقق على ساعة النظام ضمنيًا.

إذا كان `authorized_by` من نوع `approval` يبقى التحقق التفصيلي مؤجلًا إلى مخطط `Approval`، مع بقاء المرجع مثبتًا بالإصدار بنيويًا.

## دورة الحياة والإصدارات

الحالات:

```text
active | suspended | revoked
```

- `active`: يمكن استعمال الإسناد إذا استوفى الزمن والنطاق.
- `suspended`: يمنع استعماله مؤقتًا، ويجب تسجيل `status_reason`.
- `revoked`: سحب نهائي للإسناد، ويجب تسجيل `status_reason`.

`revoked` حالة نهائية؛ لا يعاد تنشيط الهوية نفسها. ينشأ إسناد جديد بهوية جديدة إذا لزم منح الدور لاحقًا.

عبر إصدارات `RoleAssignment` تبقى الحقول التالية ثابتة:

- `subject`
- `role`
- `valid_from`

يجوز لإصدار تالٍ تعديل نطاق الصلاحية أو تاريخ النهاية أو الحالة مع سبب تغيير مسجل في `VersionedEntity`. لا يعدل الإصدار السابق. وعند إنشاء الإصدار الأول لا يجوز أن يسبق `valid_from` وقت `recorded_at` للحدث المنشئ؛ يمكن أن يبدأ الإسناد فورًا أو في المستقبل، لكنه لا يمنح سلطة بأثر رجعي.

## إسناد الدور الأول

ينشئ `session.bootstrap.completed` الإصدار الأول من `role_assignment` بهذه الحمولة المقيدة:

```yaml
subject:
  principal_type: human
  principal_id: human-owner-001
role: human_owner
authority_scope:
  operations:
    - role_assignment.create
    - role_assignment.next_version
status: active
valid_from: <recorded_at للحدث نفسه>
assignment_reason: establish the initial human owner for this session
```

ويجب أن:

- يطابق `valid_from` وقت `recorded_at`.
- يغيب `valid_until`.
- تكون قائمة العمليات هي القائمتين أعلاه فقط، بلا صلاحيات إضافية.

بذلك يمنح جذر الجلسة أقل سلطة لازمة لإنشاء الأدوار التالية، ولا يمنح Runtime أو المالك الأول صلاحية تنفيذ كل عمليات البروتوكول تلقائيًا.

## التكامل مع VersionedEntity

إذا كان `entity.entity_type: role_assignment` يفرض `VersionedEntity`:

```text
legal_payload_schema: urn:w1-cip:entity-payload:0.1:role-assignment
```

ويتحقق من `legal_payload` بمخطط `RoleAssignment`. وبالعكس، لا يجوز استعمال مخطط الحمولة هذا مع نوع كيان آخر.

## رموز الأخطاء الحالية

بنية ودلالة الإسناد:

- `role_assignment_invalid_validity_interval`
- `role_assignment_backdated`
- `role_assignment_management_requires_human_owner`
- `editorial_classification_role_not_allowed`
- `risk_acceptance_requires_human_owner`
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

انتقال الإصدارات:

- `role_assignment_subject_changed`
- `role_assignment_role_changed`
- `role_assignment_valid_from_changed`
- `role_assignment_revocation_final`

التحقق من السلطة:

- `authority_role_assignment_not_found`
- `authority_version_not_current`
- `authority_payload_schema_mismatch`
- `authority_subject_mismatch`
- `authority_inactive`
- `authority_evaluation_time_required`
- `authority_not_yet_valid`
- `authority_expired`
- `authority_scope_denied`
- `authority_capability_denied`

قيود التأسيس والعملية:

- `command_type_precondition_mismatch`
- `bootstrap_authority_scope_not_minimal`
- `bootstrap_authority_start_mismatch`
- `bootstrap_authority_must_not_expire`

## ما يبقى مؤجلًا

- التحقق من السلطة القائمة على `Approval`.
- مصفوفة أوسع للقدرات بعد تصميم بقية الكيانات.
- تفويض الصلاحيات بين المجالات أو التوقيع التشفيري.
- فصل الأدوار داخل الفريق أصبح مطبقًا عبر `TeamPlan`، وأصبح مؤلف الادعاء الفعلي قابلًا للحسم عبر `Contribution`. اكتمل منع المتحقق من فحص ادعائه عبر `Verification` مع تطبيق قواعد الاستقلال المسجلة في `TeamPlan`.
- فحص موضوع القرار الفعلي حتى يعتمد مخطط `Decision`.


## حماية عقد الهدف

تقصر عمليتا `goal_contract.create` و`goal_contract.next_version` على دور `human_owner`.

## إدارة خطة الفريق

العمليتان `team_plan.create` و`team_plan.next_version` لا تقبلان إلا من إسناد يحمل دور `orchestrator` أو `human_owner`. لا يستطيع المنفذ أو المراجع إدارة الخطة حتى إذا أدرجت العملية نصيًا في نطاقه.


## إدارة منح السياق

العمليتان `context_grant.create` و`context_grant.next_version` مقصورتان في `v0.1` على إسناد يحمل دور `human_owner`. لا يستطيع المنظم أو أي وكيل إصدار منحة لمجرد وجود اسم العملية في نطاقه.

## إدارة الموافقات

يمكن منح `approval.create` و`approval.next_version` إلى `reviewer` أو `verifier` أو `decision_authority` أو `human_owner`، لكن نوع الموافقة يفرض قيودًا أدق: قبول المخاطر للمالك البشري فقط، واعتماد التصحيح التحريري يحتاج قدرة `classify_editorial_correction`.
