# مذكرة اعتماد PrincipalRef — 4 أغسطس 2026

## السبب

كان `actor` و`on_behalf_of` و`recorded_by` معرفات نصية غير مصنفة، ما جعل التمييز بين الوكيل والإنسان والـRuntime والخدمة والأداة غير ممكن بنيويًا.

## القرار

اعتمد عقد مستقل `PrincipalRef` يتكون من `principal_type` و`principal_id`، بالأنواع:

```text
agent | human | runtime | service | tool
```

المرجع يثبت الهوية الجلسية فقط ولا يثبت السلطة أو القدرات أو الثقة.

## أثره في ProtocolEnvelope

- أصبح `actor` و`on_behalf_of` من نوع `PrincipalRef`.
- أصبح `recorded_by` مرجعًا من النوع `runtime` فقط.
- يجب أن يكون Runtime التأسيس هو `actor` و`recorded_by` معًا.
- يرفض `on_behalf_of` إذا ساوى `actor`.
- تتحقق حالة الجلسة من ثقة مسجل كل حدث، لا حدث التأسيس فقط.

## الطرف الفعلي للصلاحية

إذا ظهر `on_behalf_of` فهو الطرف الذي يجب أن يغطيه سند `authorized_by`، وإلا يكون `actor` هو الطرف الفعلي. أصبح هذا الفحص مطبقًا لسندات `RoleAssignment` بعد اعتماد مخططها، ويبقى `Approval` مؤجلًا.

## نتيجة الاختبارات

أضيف مخطط مستقل وأمثلة قبول ورفض واختبارات تكامل مع `ProtocolEnvelope`، مع اختبارات دلالية لتكرار الفاعل في `on_behalf_of` واختلاف Runtime التأسيس عن المسجل.
