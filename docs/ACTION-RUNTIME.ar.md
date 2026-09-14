# W1 Action Runtime

## الغرض

`W1 Action Runtime` هو طبقة التنفيذ المحلي المحكوم في W1 Nexus. يحول طلبات الأفعال المنظمة إلى عمليات على الملفات والأوامر وGit، مع سياسة صلاحيات وسجل دائم وموافقات مرتبطة بالفعل وإمكان التراجع عندما يكون ذلك ممكنًا.

لا يمثل التنفيذ الحالي حاجزًا أمنيًا على مستوى النواة. تشغيل كود غير موثوق يحتاج إلى حاوية أو آلة افتراضية مستقلة. دور هذه الطبقة هو تقليل الصلاحيات، منع الأخطاء الشائعة، فرض الموافقة، وتوفير سجل قابل للمراجعة.

## أنواع الأفعال

```text
file.read
file.write
file.mkdir
file.move
file.delete
command.run
git.worktree.create
git.worktree.remove
```

كل فعل يحمل:

```json
{
  "action_id": "write-reviewed-output",
  "kind": "file.write",
  "parameters": {
    "path": "outputs/result.json",
    "content": "{\"status\":\"ready\"}"
  },
  "requested_by": "executor-agent",
  "reason": "Materialize the reviewed result."
}
```

## التخطيط قبل التنفيذ

تنتج `ActionRuntime.plan()` وصفًا ثابتًا يتضمن:

- مستوى الخطر.
- هل الفعل قابل للتراجع.
- هل يحتاج إلى موافقة.
- بصمة SHA-256 للنوع والمعاملات.
- المسارات التي سيؤثر فيها.
- التحذيرات التشغيلية.

```bash
w1 actions plan --request action.json
```

لا ينفذ أمر التخطيط أي تغيير.

## حصر مساحة العمل

تُحل جميع المسارات بالنسبة إلى جذر مساحة العمل. يرفض النظام:

- `..` الذي يخرج من الجذر.
- المسارات المطلقة خارج المشروع.
- الروابط الرمزية عندما تكون معطلة في السياسة.
- الكتابة المباشرة داخل `.git`.
- الكتابة المباشرة داخل `.w1nexus`.

تستعمل الكتابات ملفًا مؤقتًا و`os.replace` لتقليل احتمال بقاء ملف جزئي.

## الموافقات

الأفعال الحساسة مثل الحذف، الأوامر المعدلة، وإنشاء أو حذف worktrees تحتاج إلى موافقة افتراضيًا.

الموافقة:

- مرتبطة ببصمة الفعل، لا باسم عام.
- لها وقت انتهاء.
- ذات استعمال واحد افتراضيًا.
- موقعة بـHMAC بمفتاح محلي في `.w1nexus/action-secret.key`.
- لا تصلح لفعل آخر حتى لو كان من النوع نفسه.

```bash
w1 actions approve \
  --request delete-action.json \
  --issued-by human-owner \
  --output approval.json

w1 actions execute \
  --request delete-action.json \
  --approval approval.json
```

للاستخدام التفاعلي المحلي يمكن دمج الخطوتين صراحة:

```bash
w1 actions execute --request delete-action.json --approve --issued-by human-owner
```

## التراجع

تأخذ عمليات الكتابة والحذف والنقل وإنشاء المجلدات نسخًا احتياطية قبل التغيير، ضمن الحد الأقصى المحدد في السياسة.

```bash
w1 actions undo write-reviewed-output
```

التراجع لا يغير سجل الفعل الأصلي؛ يسجل فعلًا جديدًا من النوع `action.undo`.

لا تعد الأوامر العامة و`git.worktree.remove` قابلة للتراجع تلقائيًا.

## تنفيذ الأوامر

تعمل الأوامر بالشروط التالية:

- `shell=False` دائمًا.
- `argv` مصفوفة صريحة.
- لا تقبل محارف Shell مثل `;`, `|`, `` ` ``, `$`, و`>`.
- قائمة أوامر مسموحة وقائمة أوامر ممنوعة.
- مهلة قصوى.
- حد أقصى للخرج.
- قتل مجموعة العملية عند انتهاء المهلة على الأنظمة الداعمة.
- إزالة متغيرات البيئة الحساسة.
- إزالة إعدادات Proxy.
- منع أوامر الشبكة المعروفة افتراضيًا.

قراءة Git مثل `git status` لا تحتاج إلى موافقة. أوامر Python وpytest تحتاج إلى موافقة لأنها تستطيع تنفيذ كود وتعديل الملفات.

### حد أمني صريح

منع الشبكة في التنفيذ الحالي هو **best effort**. برنامج Python وافق المستخدم على تشغيله قد ينشئ اتصالًا شبكيًا بنفسه. العزل القوي يحتاج إلى حاوية أو VM وسياسة شبكة على مستوى النظام.

## Git worktrees

يمكن إنشاء مساحة عمل مستقلة لكل وكيل:

```bash
w1 worktrees create agent-one \
  --branch w1/agent-one \
  --ref HEAD \
  --approve
```

ينشأ الـworktree داخل:

```text
.w1nexus/worktrees/<agent-id>
```

ويحمل فرعًا مستقلًا. هذا يسمح لعدة وكلاء بالعمل بالتوازي دون الكتابة في شجرة العمل نفسها.

```bash
w1 worktrees list
w1 worktrees remove agent-one --approve
```

## السجل الدائم

يحفظ `action-runtime.sqlite3`:

- الطلب الأصلي.
- خطة المخاطر.
- الموافقة المستعملة.
- وقت البدء والانتهاء.
- الخرج والخطأ بعد تحديد الحجم.
- المسارات المتغيرة.
- النسخ الاحتياطية.
- worktrees المسجلة.
- أفعال التراجع.

إعادة إرسال `action_id` نفسه بالمحتوى نفسه تعيد النتيجة المخزنة. استعمال المعرف نفسه لمحتوى مختلف يرفض.

## التكامل مع Orchestrator Core

أصبح `ProviderResponse` يدعم:

```json
{
  "output_type": "contribution",
  "payload": {"summary": "Prepared output"},
  "context_fields_used": [],
  "action_requests": [
    {
      "action_id": "write-agent-output",
      "kind": "file.write",
      "parameters": {
        "path": "agent-output.txt",
        "content": "hello"
      }
    }
  ]
}
```

السلوك:

1. يتحقق الموصل من بنية الطلب.
2. يخطط Action Runtime للفعل.
3. ينفذ الفعل الآمن مباشرة.
4. إذا احتاج الفعل إلى موافقة، تتحول حالة التشغيل إلى `awaiting_user`.
5. بعد موافقة المستخدم وتنفيذ الفعل، يستأنف المنظم من الاستجابة المحفوظة.
6. لا يستدعي النموذج مرة ثانية.
7. تضاف نتائج الأفعال إلى `_w1_action_results` في مخرج المهمة.

## إعداد السياسة

توجد السياسة في `.w1nexus/workspace.json`:

```json
{
  "action_runtime": {
    "policy": {
      "allowed_commands": ["git", "python", "python3", "pytest"],
      "max_command_seconds": 60,
      "max_output_bytes": 1000000,
      "max_backup_bytes": 25000000,
      "require_approval_for_writes": false,
      "require_approval_for_delete": true,
      "require_approval_for_commands": true,
      "require_approval_for_worktrees": true,
      "allow_symlinks": false,
      "allow_network": false
    }
  }
}
```

## الحدود المؤجلة

لم تنفذ بعد:

- حاويات أو VMs لكل وكيل.
- منع الشبكة على مستوى النواة.
- حدود CPU وRAM على مستوى cgroups أو Job Objects.
- تشغيل رسومي عام Computer Use.
- MCP client/server.
- دمج الفروع وحل التعارضات تلقائيًا.
- توقيع الموافقات بمفتاح خارجي أو جهاز أمني.
