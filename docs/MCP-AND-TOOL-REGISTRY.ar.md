# MCP وUnified Tool Registry في W1 Nexus

## 1. الهدف ضمن W1 Nexus الكاملة

هذه الطبقة ليست إضافة جانبية لنسخة أولية مصغرة. هي بوابة الأدوات والتكاملات التي ستستخدمها المنصة الكاملة لربط الوكلاء بالخدمات، المستودعات، قواعد البيانات، تطبيقات المكتب، أدوات التصميم، والمتصفحات من خلال واجهة موحدة قابلة للتدقيق.

تعتمد النسخة المرجعية بروتوكول MCP المستقر `2025-11-25`، مع دعم:

- دورة `initialize` ثم `notifications/initialized`.
- JSON-RPC 2.0.
- `tools/list` و`tools/call`.
- `resources/list` و`resources/read`.
- `prompts/list` و`prompts/get`.
- النقل عبر `stdio` للعميل والخادم.
- Streamable HTTP للعميل، مع JSON وردود SSE المحدودة المنتهية.

## 2. لماذا لا يستدعي المنظم خادم MCP مباشرة؟

تمر كل أداة عبر `ToolRegistry` الموحد:

```text
Local W1 tools ─┐
MCP server A ───┼─> namespaced catalog ─> schema validation
MCP server B ───┘                         ├> risk classification
                                          ├> approval policy
                                          ├> idempotent call id
                                          └> audit journal
```

هذا يمنع تضارب الأسماء، ويسمح للواجهة الرسومية المستقبلية بإظهار مصدر الأداة، مدخلاتها، خطرها، وحالة الموافقة قبل التنفيذ.

## 3. تسمية الأدوات البعيدة

تتحول أداة الخادم:

```text
server_id = github
original tool = create_issue
```

إلى الاسم الداخلي:

```text
mcp.github.create_issue
```

لا يستطيع خادمان امتلاك الاسم الداخلي نفسه.

## 4. الثقة والموافقات

تعليقات MCP مثل `readOnlyHint` أو `destructiveHint` تبقى بيانات وصفية غير موثوقة. لا تمنح موافقة تلقائية.

السياسة الافتراضية:

```text
أي أداة MCP بعيدة
→ تحتاج موافقة W1 محلية مرتبطة ببصمة الاستدعاء
→ استعمال واحد
→ انتهاء زمني
→ لا تصلح لمدخلات مختلفة
```

يمكن للمستخدم أن يضيف أداة محددة إلى:

```json
{
  "auto_approve_tools": ["search_docs"]
}
```

وهذا قرار محلي صريح، وليس ثقة مستنتجة من الخادم.

## 5. حماية الأسرار

ملف الإعداد لا يخزن قيمة المفتاح أو Token. يسجل اسم متغير البيئة فقط:

```json
{
  "bearer_token_env": "COMPANY_MCP_TOKEN"
}
```

بالنسبة إلى `stdio`، يمكن تمرير متغير محدد إلى العملية الفرعية:

```json
{
  "environment": {
    "GITHUB_TOKEN": "W1_GITHUB_TOKEN"
  }
}
```

المفتاح هو اسم المتغير داخل الخادم، والقيمة هي اسم المتغير المصدر داخل بيئة W1. لا تحفظ القيمة السرية في قاعدة بيانات الأدوات.

## 6. الأمان في Streamable HTTP

- يرفض W1 عنوان HTTP بعيدًا غير مشفر.
- يسمح بـHTTP محلي فقط عند تفعيل `allow_insecure_loopback` صراحة.
- يرسل Bearer Token في Header وليس في URI.
- يدعم مهلة لكل طلب.
- لا تدعي هذه الخطوة اكتمال OAuth Discovery أو Dynamic Client Registration.

## 7. W1 كخادم MCP

يمكن تشغيل W1 عبر `stdio`:

```bash
w1 --workspace ./project mcp serve --stdio
```

ويعرض حاليًا أدوات محكومة مثل:

```text
w1.workspace.read_text
w1.workspace.list_files
w1.action.plan
w1.action.execute
w1.session.verify
```

كما يعرض موارد:

```text
w1://capabilities
w1://workspace/config
```

وقوالب:

```text
w1-plan-goal
w1-review-change
```

الأداة الحساسة `w1.action.execute` تمر ببوابتين عند الحاجة:

1. موافقة Tool Registry للسماح باستدعاء الأداة.
2. موافقة Action Runtime الخاصة بالفعل نفسه.

## 8. أوامر CLI

### إدارة الخوادم

```bash
w1 mcp servers list

w1 mcp servers add docs \
  --transport stdio \
  --command-json '["python","-m","company_mcp_server"]'

w1 mcp probe docs
w1 mcp servers remove docs
```

### الأدوات الموحدة

```bash
w1 tools list
w1 tools plan --call-id call-one --name mcp.docs.search --arguments args.json
w1 tools approve --call-id call-one --name mcp.docs.search --arguments args.json --issued-by human-owner
w1 tools call --call-id call-one --name mcp.docs.search --arguments args.json --approval approval.json
w1 tools history
```

### الموارد والقوالب

```bash
w1 resources list --server docs
w1 resources read --server docs --uri docs://architecture
w1 prompts list --server docs
w1 prompts get --server docs --name review --arguments prompt-args.json
```

## 9. الاستدعاء الحتمي

يرتبط كل استدعاء بـ`call_id` وبصمة:

```text
SHA-256(tool_name + canonical arguments)
```

- إعادة نفس `call_id` والمدخلات تعيد النتيجة المخزنة.
- إعادة `call_id` نفسه بمدخلات مختلفة ترفض.
- الموافقة المستهلكة لا تستعمل مرة ثانية.

## 10. الحدود الحالية

المكتمل:

- عميل وخادم `stdio`.
- عميل Streamable HTTP لردود JSON وSSE المنتهية.
- الأدوات والموارد والقوالب.
- التحقق من JSON Schema للمدخلات والمخرجات المنظمة.
- سجل استدعاءات SQLite.
- موافقات HMAC محلية.
- Namespacing واكتشاف متعدد الخوادم.

المؤجل للطبقات التالية من W1 Nexus الكاملة:

- خادم Streamable HTTP تابع لـW1.
- OAuth 2.1 discovery والتسجيل الديناميكي.
- الاشتراك الحي في تغير الموارد والأدوات.
- Sampling وElicitation وTasks الموسعة.
- بث SSE طويل قابل للاستئناف.
- سياسات مؤسساتية مركزية وتوقيعات بمفاتيح خارجية.
- ربط Tool Registry مباشرة بمخطط التشغيل المتوازي متعدد الوكلاء.
