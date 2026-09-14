# مذكرة تأسيس سلطة الجلسة — 4 أغسطس 2026

## المشكلة

كان كل غلاف يتطلب `authorized_by` من `role_assignment` أو `approval`، بينما لا يوجد أي منهما عند إنشاء الجلسة. أدى ذلك إلى دائرة تمنع إنشاء أول سلطة.

## القرار

يعتمد `v0.1` حدثًا جذريًا واحدًا هو `session.bootstrap.completed`:

- أول حدث في الجلسة (`sequence: 1`).
- `protocol_event` ينشئ `role_assignment` الإصدار `1`.
- يستخدم `expected_absent: true`.
- يحمل `bootstrap` أحادي الاستخدام بنطاق `create_initial_role_assignment`.
- لا يحمل `authorized_by` أو `causation_id` أو `on_behalf_of`.
- يسجله Runtime محلي وثقه المضيف خارج بروتوكول الرسائل.

بعد هذا الحدث، كل غلاف عادي يتطلب `authorized_by` مثبتًا. لا يوجد `command` للتأسيس داخل البروتوكول.

## حدود الثقة

لا يقدم هذا التصميم إثباتًا تشفيريًا أو ثقة بين المجالات. تتحقق طبقة الجلسة محليًا من Runtime المسجل وفردية الحدث ومعرف التأسيس.

## التنفيذ المرجعي

أضيف `SessionValidationState` وفحص `validate_protocol_envelope_session_semantics` لتطبيق شروط: أول حدث، أحادية الاستعمال، Runtime الموثوق، ومنع الرسائل العادية قبل اكتمال التأسيس.

## تحديث RoleAssignment

بعد اعتماد مخطط `RoleAssignment` أصبحت حمولة الجذر محددة قانونيًا: طرف بشري، دور `human_owner`، حالة `active`، بداية تطابق `recorded_at`، وعدم وجود `valid_until`، ونطاق عمليات يقتصر على `role_assignment.create` و`role_assignment.next_version`.
