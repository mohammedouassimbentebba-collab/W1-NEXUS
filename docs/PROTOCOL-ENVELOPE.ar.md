# ProtocolEnvelope — W1-CIP v0.1

## الحالة

هذه أول شريحة تقنية بعد إغلاق الدلالات التأسيسية. يصف المخطط غلافين فقط:

- `command`: أمر يطلب إنشاء كيان أو إصدار تالٍ.
- `event`: واقعة مسجلة من فئة بروتوكولية أو تشغيلية أو أمنية.

لا يعرّف هذا الملف مخططات الحمولات الخاصة بكل نوع كيان، ولا النقل الشبكي، ولا التوقيع. ويعتمد [PrincipalRef](PRINCIPAL-REF.ar.md) لهويات الأطراف و[EntityRef](ENTITY-REF.ar.md) للمراجع المثبتة و[VersionedEntity](VERSIONED-ENTITY.ar.md) لسجل الإصدار القانوني الكامل.

## معيار المخطط

يستخدم المخطط JSON Schema Draft 2020-12، ويجب تشغيل التحقق مع فاحص `format` حتى يفرض `date-time` و`uri` بدل معاملتهما كتعليقات وصفية فقط.

معرّف المخطط:

```text
urn:w1-cip:schema:0.1:protocol-envelope
```

## القرارات البنيوية

### هوية الرسالة

`message_id` هو معرف الغلاف، ويؤدي دور معرف الحدث عندما يكون `kind: event`. لا يوجد `event_id` مكرر في `v0.1`.

يجب أن يكون `message_id` فريدًا داخل الجلسة. لا يستطيع JSON Schema فحص الفردية بين عدة وثائق؛ لذلك تطبق في مخزن الجلسة.


### هوية الأطراف

تستخدم الحقول الآتية `PrincipalRef` بدل معرف نصي مجرد:

- `actor`: الفاعل المباشر.
- `on_behalf_of`: الطرف الذي يمثله الفاعل عند وجود وساطة حقيقية.
- `recorded_by`: Runtime قبل الحدث وسجله.

القيم المسموحة للنوع هي `agent` و`human` و`runtime` و`service` و`tool`. ولا يمنح المرجع سلطة أو قدرة بذاته.

يفرض المخطط أن يكون `recorded_by.principal_type: runtime`. كما يفرض أن يكون فاعل حدث التأسيس Runtime، بينما يتحقق المدقق الدلالي من تطابقه مع المسجل.

إذا ظهر `on_behalf_of` فهو الطرف الفعلي الذي يجب أن يغطيه `authorized_by`؛ وإلا يكون `actor` هو الطرف الفعلي. اعتمد مخطط `RoleAssignment` وأصبح المرجع البرمجي يثبت هذه المطابقة لسندات الدور. أصبح سند `Approval` قابلًا للتحقق من القرار والسريان والهدف والطرف والاستهلاك.

### الأوامر

يجب أن يحمل الأمر `precondition` واحدة فقط:

- `create` مع `expected_absent: true` وهوية الهدف.
- `next_version` مع `expected_entity_version` ومرجع الإصدار السابق.

لا يحمل الأمر `sequence` أو `recorded_at` أو `recorded_by`؛ فالرقم التسلسلي يخص الحدث المقبول، لا الطلب قبل قبوله.

### الأحداث

كل حدث يحمل `event_class` و`sequence` و`received_at` و`recorded_at` و`recorded_by`.

- `protocol_event` يغير الحالة المعيارية، ولذلك يجب أن يحمل `entity` و`precondition` اللذين يثبتان الإصدار الناتج والشرط المقبول.
- `operational_event` و`security_event` لا يغيران الحالة مباشرة، ولذلك يمنع عنهما `precondition`. يجوز أن يحمل `entity` بوصفه موضوع الواقعة، لا نتيجة تعديل.

عندما يقبل الحدث إنشاء كيان أو إصدار تالٍ، يستخدم `payload_schema: urn:w1-cip:schema:0.1:versioned-entity` ويحمل `payload` الحمولة القانونية الكاملة وسلسلة الإصدار. يفرض المخطط حينها أن الرسالة حدث بروتوكولي، ويتحقق المدقق من تطابق سجل الإصدار مع `entity` و`message_id` وهدف `precondition`.

### الصلاحية

`authorized_by` يستخدم مخطط `EntityRef` المستقل، ويقيده الغلاف إلى إصدار `role_assignment` أو `approval`. ولا يعوض `PrincipalRef` هذا السند. عندما يكون السند `role_assignment` يقرأ المدقق حمولة [RoleAssignment](ROLE-ASSIGNMENT.ar.md) ويثبت الطرف والحالة والمدة والنطاق.

الاستثناء الوحيد هو حدث `session.bootstrap.completed`. ينشئه Runtime محلي موثوق بوصفه أول حدث في الجلسة، وينشئ `role_assignment` الإصدار `1`. يكون Runtime نفسه `actor` و`recorded_by`، ويحمل الحدث كائن `bootstrap` أحادي الاستخدام ولا يحمل `authorized_by`. لا يوجد أمر تأسيس داخل البروتوكول، ولا يجوز استعمال `bootstrap` مع أي نوع رسالة آخر.

بنية `bootstrap` في هذه الشريحة:

```yaml
mode: local_runtime
bootstrap_id: bootstrap-session-demo-001
scope: create_initial_role_assignment
```

تتحقق طبقة مخزن الجلسة من فردية `bootstrap_id`، وأن الحدث هو الأول فعلًا، وأن `recorded_by` Runtime محلي موثوق.

### السببية

- `causation_id` يشير إلى معرف الرسالة أو الحدث الذي سبب الغلاف مباشرة.
- `correlation_id` يجمع رسائل تشغيل واحد.
- `related_events` للسياق فقط، ولا يجوز أن يعاد فيها السبب المباشر.

## ما يفرضه JSON Schema

- القيم الثابتة لإصدار البروتوكول.
- lowercase وأنماط المعرفات والأنواع، وبنية `PrincipalRef` المصنفة.
- الحقول المطلوبة والممنوعة لكل نوع غلاف.
- تثبيت إصدار سند الصلاحية، واشتراط Runtime في `recorded_by` وفاعل التأسيس، مع استثناء تأسيسي وحيد مضبوط.
- صحة بنية شرطي الإنشاء والإصدار التالي عبر `EntityIdentity` و`EntityRef` المستقلين.
- منع خصائص غير معروفة خارج `extensions`.
- البنية الأساسية للأزمنة وعناوين مخططات الحمولات، والتحقق البنيوي من `VersionedEntity` عندما يعلن الغلاف مخططه.

## القواعد الدلالية خارج المخطط

ينفذ المرجع البرمجي الحالي القواعد المحلية الآتية:

- `actor` لا يساوي `on_behalf_of`.
- Runtime الفاعل يطابق Runtime المسجل في حدث التأسيس.
- `recorded_at` لا يسبق `received_at`.
- السبب المباشر لا يظهر داخل `related_events`.
- نتيجة `create` تطابق هوية الهدف ويكون إصدارها `1`.
- نتيجة `next_version` تطابق الهوية ويكون رقمها الإصدار المتوقع زائد واحد.
- إصدار مرجع الهدف يطابق `expected_entity_version`.
- سجل `VersionedEntity` يطابق كيان الغلاف والحدث المنشئ ومرجع الإصدار السابق.
- نوع الأمر يطابق العملية المشتقة من هدف `precondition`.
- سلطة التأسيس تحمل النطاق الأدنى فقط وتبدأ عند `recorded_at` من دون انتهاء.

وتبقى القواعد التي تحتاج حالة جلسة لمرحلة مخزن الأحداث:

- فردية `message_id` و`sequence` داخل الجلسة.
- صلاحية `authorized_by` عند `recorded_at` للأحداث أو وقت قبول صريح للأوامر.
- وجود أحدث إصدار من `RoleAssignment` وتطابق صاحبه وحالته ومدته ونطاقه.
- وجود الكيان أو غيابه فعليًا.
- منع إعادة استخدام الهوية.
- منع تعديل إصدار مقبول.
- أن حدث التأسيس هو أول حدث وحيد في الجلسة وأن `bootstrap_id` لم يستخدم سابقًا.
- أن `recorded_by` Runtime موثوق وفق إعداد المضيف لكل حدث، لا حدث التأسيس فقط.
- أن سند `authorized_by` يخص الطرف الفعلي (`on_behalf_of` عند وجوده وإلا `actor`) ويغطي نطاق العملية؛ أصبح ذلك مطبقًا لسندات `RoleAssignment`.
- التحقق من سندات `Approval` الدقيقة وأحادية الاستخدام عبر حالة الموافقات.

## رموز الأخطاء الدلالية الحالية

- `actor_same_as_on_behalf_of`
- `bootstrap_actor_recorder_mismatch`
- `recorded_before_received`
- `causation_listed_as_related`
- `result_identity_mismatch`
- `created_entity_version_not_one`
- `precondition_target_version_mismatch`
- `result_version_not_next`
- `versioned_entity_result_mismatch`
- `versioned_entity_creator_event_mismatch`
- `versioned_entity_previous_mismatch`
- `previous_identity_mismatch`
- `previous_version_not_immediate`
- `command_type_precondition_mismatch`
- `bootstrap_authority_scope_not_minimal`
- `bootstrap_authority_start_mismatch`
- `bootstrap_authority_must_not_expire`

رموز سلطة `RoleAssignment` مفصلة في [وثيقة RoleAssignment](ROLE-ASSIGNMENT.ar.md).

رموز فحص حالة التأسيس:

- `session_not_bootstrapped`
- `bootstrap_not_first_event`
- `bootstrap_already_completed`
- `bootstrap_id_reused`
- `bootstrap_recorder_untrusted`
- `event_recorder_untrusted`

## تتبع الثوابت التأسيسية

| الثابت | تطبيقه في هذه الشريحة |
|---|---|
| هوية الأطراف المصنفة | مخطط `PrincipalRef` للفاعل والمُمثَّل والمسجل |
| lowercase للمعرفات | أنماط `identifier` و`entityType` و`typeName` |
| `expected_absent` عند الإنشاء | فرع `createPrecondition` |
| `expected_entity_version` للإصدار التالي | فرع `nextVersionPrecondition` والفحص الدلالي |
| سند صلاحية مثبت | `authorized_by` يدمج `EntityRef` ويقيد النوع إلى `role_assignment` أو `approval` |
| جذر السلطة الأول | `session.bootstrap.completed` فقط، Runtime فاعل ومسجل واحد، `sequence: 1`، وكيان `role_assignment` الإصدار `1` |
| ترتيب `session_id + sequence` | يثبته [SessionStore](SESSION-STORE.ar.md) بقفل كتابة ومفتاح فريد ومعاملة ذرية |
| فصل فئات الأحداث | `event_class` وشروط كل فئة |
| السبب المباشر مقابل العلاقات | `causation_id` وفحص عدم تكراره في `related_events` |
| البيانات الحساسة | مثال الحدث الأمني يستخدم بيانات حجب مجردة |
| الحمولة القانونية الكاملة | أحداث القبول تحمل `VersionedEntity` كاملًا داخل `payload` |
| سلسلة الإصدار | تطابق `payload.previous_version` مع شرط `next_version` |

يطبق `VersionedEntity` ثوابت الحمولة القانونية وتصنيف التغيير، بينما يحافظ الغلاف على الربط بين الحدث والإصدار الناتج.

## تشغيل الاختبارات

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## ملفات الاختبار

- `examples/principal-ref`: أمثلة قبول ورفض لعقد هوية الأطراف.
- `examples/entity-ref`: أمثلة قبول ورفض للمرجع المثبت.
- `examples/entity-identity`: أمثلة قبول ورفض لهوية الإنشاء غير المصدّرة.
- `examples/versioned-entity`: أمثلة للبنية والانتقالات وتصنيف التغيير.
- `examples/role-assignment`: أمثلة الدور والنطاق والحالة والمدة والرفض الدلالي.
- `examples/protocol-envelope/valid`: أمثلة تمر بالبنية والدلالة المحلية.
- `examples/protocol-envelope/invalid-structural`: أمثلة يرفضها JSON Schema.
- `examples/protocol-envelope/invalid-semantic`: أمثلة تمر بنيويًا ويرفضها المدقق الدلالي.

اعتمد `EntityRef` و`VersionedEntity` مستقلين. لا توجد تعريفات معاينة محلية، وتتحقق أحداث قبول الإصدارات من السجل القانوني الكامل عبر مرجع المخطط المستقل.


## تكامل GoalContract

عند حمل حدث بروتوكولي `VersionedEntity` من النوع `goal_contract` يشغل المدقق قواعد [GoalContract](GOAL-CONTRACT.ar.md) المحلية، بما فيها فردية المعرفات وتغطية المخرجات والقيود وسياسة المخاطر. أما فحص السلطة المثبتة وحالة الجلسة فيبقى في طبقة الحالة.

## تكامل TeamPlan

عند حمل حدث بروتوكولي `VersionedEntity` من النوع `team_plan` يشغل المدقق قواعد [TeamPlan](TEAM-PLAN.ar.md) المحلية، مثل فردية فتحات الفريق، وملء الحد الأدنى للأدوار، وتسجيل الاستقلال، وربط سلطات القرار بأعضاء الفريق. أما فحص عقد الهدف وإسنادات الأدوار الفعلية فيتم في طبقة حالة الجلسة.


## تكامل ContextGrant

عند حمل حدث بروتوكولي `VersionedEntity` من النوع `context_grant` يشغل المدقق القواعد المحلية للمنحة، ومنها نافذة الصلاحية والحالة الابتدائية وعدم التأريخ للخلف. أما فحص عضوية المستلم في `TeamPlan` وتوافق `RoleAssignment` و`AgentCard` فيتم في طبقة حالة الجلسة.

## تكامل Task

عند حمل حدث بروتوكولي `VersionedEntity` من النوع `task` يشغل المدقق قواعد [Task](TASK.ar.md) المحلية، مثل فردية معرفات المخرجات، وتغطية المخرجات الإلزامية، والحالة الابتدائية `created`. أما فحص عقد الهدف وخطة الفريق والمسؤول والميزانية والاعتماديات فيتم في طبقة حالة الجلسة.


## تكامل Decision

عند قبول إصدار `Decision` يتحقق الغلاف محليًا من بنية القرار ودلالاته، ومن تطابق `authorized_by` مع `last_transition_by_role_assignment`. أما صلاحية الإسناد ونطاق موضوع القرار ومصادر الحكم فتتحقق عبر حالة الجلسة.

## ProtocolEvent داخل الغلاف

عندما تحمل `VersionedEntity` حمولة قانونية من النوع `urn:w1-cip:entity-payload:0.1:protocol-event`، يجب أن:

- يكون `entity_type: protocol_event` و`entity_version: 1`.
- يساوي `entity_id` قيمة `message_id`.
- يطابق `type` الصيغة `protocol_event.<event_type>`.
- يوجد `causation_id`.
- يكون `authorized_by` إسناد دور صالحًا يغطي `protocol_event.create`.

يبقى `ProtocolEnvelope` مسؤولًا عن النقل والتسلسل والفاعل والسلطة، بينما تحدد [ProtocolEvent](PROTOCOL-EVENT.ar.md) الأثر القانوني.


## نشر FinalResult

يحتاج حدث إنشاء النتيجة إلى `authorized_by` من نوع `RoleAssignment` يطابق `generated_by_role_assignment` ويغطي `final_result.create`.
