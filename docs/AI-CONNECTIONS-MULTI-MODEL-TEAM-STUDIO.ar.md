# AI Connections & Multi-Model Team Studio

تضيف هذه الطبقة واجهة موحدة لربط نماذج المستخدم بـ W1 Nexus دون تحويل W1 إلى مزود مركزي أو افتراض أن اشتراك تطبيق استهلاكي يمنح وصول API.

## نموذج الاتصال

يتدرج المورد من `Provider -> Connection/Account -> ModelProfile -> AI Team -> Role`.

- `OpenAI`: اتصال API credential يملكه المستخدم.
- `Claude / Anthropic`: اتصال API credential يملكه المستخدم.
- `Gemini / Google`: API credential أو حساب OAuth موجود في Credential Broker.
- `Grok / xAI`: اتصال API credential يملكه المستخدم.
- `Local / OpenAI-compatible`: loopback HTTP محلي فقط.
- `Custom OpenAI-compatible`: HTTPS بعيد، أو loopback HTTP، مع API credential أو بدون مصادقة؛ OAuth ممكن فقط عندما يكون حساب W1 الموافق للمزود مهيأ صراحة.

الأسرار لا تحفظ في قاعدة `ai-connections.sqlite3`. تخزن API credentials في OS-native Credential Vault، وتحتفظ Connections فقط بمراجع `w1-credential:` أو `w1-account:`.

## حماية endpoint

لا يسمح W1 بتحويل credential لمزود built-in إلى host مختلف. إذا اختار المستخدم OpenAI أو Anthropic أو Gemini أو xAI، يبقى hostname مقيدًا بالنطاق الرسمي لذلك connector. endpoints الأخرى تسجل كـ Custom Provider، مع HTTPS للوجهات البعيدة أو HTTP على loopback فقط.

## AI Team Studio

يدعم Team Studio الأنماط التالية:

- `solo`: نموذج واحد عبر `best_fit`.
- `fallback`: سلسلة نماذج مرتبة عبر `fallback_chain`.
- `parallel`: عدة نماذج تعمل بالتوازي عبر `parallel_collect`.
- `verify`: منتجون ثم verifier مستقل عبر `verified_synthesis`.
- `challenge`: منتجون، ثم Challenger يرى مخرجاتهم، ثم Synthesizer يرى المرشحين والاعتراضات عبر `challenge_synthesis`.

لا يستخدم W1 التصويت العددي بين النماذج كقرار نهائي. المخرجات تبقى مساهمات قابلة للتحقق والمراجعة وفق W1-CIP.

## واجهات الاستخدام

CLI:

```text
w1 connections catalog
w1 connections list
w1 connections add-api-key ...
w1 connections attach-account ...
w1 connections add-local ...
w1 connections add-custom ...
w1 connections add-model ...
w1 connections discover ...
w1 connections benchmark
w1 teams add --file team.json
w1 teams list
w1 teams show <team-id>
w1 teams benchmark
```

Desktop Shell يعرض AI Connections وAI Teams ويستعمل نفس الـstores والـCredential Broker، وليس قاعدة بيانات أسرار مستقلة.

## حدود هذه المرحلة

- لا تدّعي W1 إمكانية تسجيل الدخول باشتراك تطبيق مزود ما لم يقدم المزود مسار OAuth/API رسميًا لذلك الاستخدام.
- Model discovery يعتمد على endpoint المدعوم من المزود؛ لا يفترض أسماء موديلات ثابتة.
- `challenge_synthesis` هو اعتراض ومراجعة متعددة النماذج، وليس Verification رسميًا بحد ذاته.
- W1 لا يملك Cloud إلزامية لهذه الميزة.
