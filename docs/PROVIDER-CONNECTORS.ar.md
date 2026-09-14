# موصلات مزودي النماذج — Provider Connectors

تربط هذه الطبقة `OrchestratorCore` بمزودي النماذج السحابية والمحلية عبر واجهة موحدة، من دون إدخال مفاتيح API أو تفاصيل HTTP في سجل الجلسة القانوني.

## الموصلات المنفذة

| الموصل | الواجهة | الحالة |
|---|---|---|
| `OpenAIResponsesConnector` | OpenAI Responses API | منفذ |
| `AnthropicMessagesConnector` | Anthropic Messages API | منفذ |
| `GeminiInteractionsConnector` | Gemini Interactions API الحالية | منفذ |
| `GeminiGenerateContentConnector` | Gemini `generateContent` للتوافق | منفذ |
| `OpenAICompatibleConnector` | `/v1/chat/completions` مثل Ollama وvLLM | منفذ |

كل الموصلات متزامنة وغير متدفقة في `v0.1`. النقل قابل للاستبدال عبر `HTTPTransport`، لذلك لا تحتاج الاختبارات إلى اتصال أو مفاتيح حقيقية.

## العقد الموحد

يستقبل الموصل `ProviderRequest` من المنظم، ويبني مطالبة تحتوي فقط على:

- وصف المهمة والمرحلة والدور.
- السياق الممنوح للمهمة فقط.
- المخرجات السابقة التي تعتمد عليها المهمة.
- نوع المخرج المطلوب.

ويجب أن يعيد النموذج كائن JSON واحدًا:

```json
{
  "output_type": "contribution",
  "payload": {},
  "context_fields_used": ["supply-voltage"],
  "protocol_envelopes": []
}
```

يرفض النظام النص الزائد، والحقول المجهولة، ونوع المخرج المختلف، والقوائم غير الصحيحة قبل أن تصل النتيجة إلى المهمة.

## المفاتيح السرية

تخزن الإعدادات **اسم متغير البيئة فقط**:

```python
ConnectorConfig(
    resource_id="openai-primary",
    model="model-id",
    api_key_reference="OPENAI_API_KEY",
)
```

ويقرأ `EnvironmentSecretResolver` القيمة وقت الاستدعاء. لا تدخل القيمة في:

- `ProviderRequest`.
- `ProviderResponse.metadata`.
- `OrchestratorJournal`.
- أحداث `SessionStore`.
- رسائل الأخطاء المستقرة.

`StaticSecretResolver` مخصص للاختبارات فقط.

## حدود الاستخدام والحصص

تفصل الطبقة بين حالتين:

### حد متجدد

```text
ProviderRateLimited
```

مثل حد الطلبات أو التوكنات في الدقيقة. لا يعلن المورد منهكًا بصورة دائمة؛ يستبعد من المهمة الحالية ويستطيع المنظم التحول إلى بديل.

### نفاد حقيقي

```text
ProviderQuotaExhausted
```

مثل نفاد الرصيد، حد الإنفاق أو حصة يومية صريحة. يحدث عندها تحديث لخطة الموارد إلى `exhausted` وفق قواعد المنظم.

لا يُفسر كل رد `429` على أنه نفاد حقيقي. يفحص الموصل نوع الخطأ وتفاصيل نافذة الحصة.

## ملاحظات الحصة

تقرأ الموصلات ما يتيحه الرد:

- OpenAI: حدود الطلبات والتوكنات المتبقية وأزمنة إعادة الضبط.
- Anthropic: الطلبات وتوكنات الإدخال والإخراج المتبقية.
- Gemini: تفاصيل خطأ الحصة ووقت إعادة المحاولة عندما يرسله الخادم.
- المزود المحلي: الاستهلاك الموجود في الرد أو تقدير معلّم بأنه تقريبي.

تُحفظ الملاحظات في جدول:

```text
provider_quota_observations
```

داخل `OrchestratorJournal`. لا تستبدل هذه الملاحظات تلقائيًا حصة الخطة لأن نافذة رأس المزود قد تكون دقيقة، بينما نافذة الخطة يومية أو أسبوعية.

## قياس التوكنات

عندما يقدم المزود حقول الاستهلاك، تحفظ القيم الفعلية في:

```text
ProviderResponse.metadata.usage
```

وعند غيابها يستخدم النظام تقديرًا محايدًا محددًا ويضع:

```json
{"estimated": true}
```

حتى لا يُعرض التقدير على أنه فاتورة دقيقة.

يحدد `accounting_meter` طريقة خصم الوحدات من خطة الموارد:

```text
requests
أو
tokens
```

## الأمان الشبكي

- الاتصالات البعيدة تحتاج HTTPS.
- HTTP مسموح فقط عند تفعيل `allow_insecure_loopback` ولعنوان loopback مثل `127.0.0.1` أو `localhost`.
- التحقق من TLS مفعّل في `UrllibTransport`.
- يوجد حد لحجم المدخل ووقت انتظار لكل موصل.
- مهلة بعد إرسال الطلب تتحول إلى `ProviderOutcomeUncertain`، لا إلى إعادة آمنة مفترضة.

## Idempotency

لا تدعي الموصلات السحابية أن الاستدلال idempotent افتراضيًا:

```python
supports_idempotency = False
```

`X-Client-Request-Id` في موصل OpenAI يستخدم للتتبع فقط. يمكن تفعيل `supports_idempotency=True` فقط عندما يكون أمام المزود Gateway موثوق يضمن إعادة نفس النتيجة للمفتاح نفسه.

إذا أصبحت نتيجة طلب غير idempotent مجهولة، ينتقل المنظم إلى `awaiting_reconciliation` بدل تكرار أثر خارجي محتمل.

## الأخطاء الموحدة

| الخطأ | معنى المنظم |
|---|---|
| `ProviderAuthenticationError` | إعداد المفتاح أو الصلاحية غير صالح |
| `ProviderRateLimited` | حد متجدد؛ جرّب بديلًا أو انتظر |
| `ProviderQuotaExhausted` | حصة أو رصيد مستهلك |
| `ProviderTransientError` | عطل مؤقت آمن نسبيًا لإعادة المحاولة |
| `ProviderOutcomeUncertain` | قد يكون الطلب نُفذ؛ لا تكرر بلا ضمان |
| `ProviderPermanentError` | الطلب أو الإعداد غير قابل للنجاح كما هو |
| `ProviderSafetyBlocked` | حجب المزود للمطالبة أو النتيجة |
| `OutputContractError` | الرد لا يطابق عقد W1-CIP |

## الحدود الحالية

لم تُنفذ بعد:

- الاستجابات المتدفقة SSE.
- استدعاء الأدوات متعدد الجولات داخل الموصل.
- رفع الملفات والصور.
- قارئات تقارير الإنفاق الإدارية التي تحتاج مفاتيح Admin منفصلة.
- Vault خارجي أو HSM للمفاتيح.
- حساب التكلفة بالعملة وفق جداول أسعار متغيرة.

تلك الإضافات لا تغير واجهة `ProviderAdapter` الأساسية.
