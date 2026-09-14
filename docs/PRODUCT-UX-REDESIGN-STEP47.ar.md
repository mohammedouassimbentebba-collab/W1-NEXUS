# W1 Nexus™ — Step 47 Product UX Redesign

هذا الإصدار يحوّل Desktop Shell من واجهة هندسية/تشخيصية إلى واجهة منتج تتمحور حول هدف المستخدم وفريق الذكاء الاصطناعي.

## المبادئ

- **Warm Light افتراضي:** بيج/عاجي مائل للأبيض مع Cyan لهوية W1.
- **Dark Mode كامل:** نفس بنية المنتج بهوية Graphite + Cyan.
- **Theme persistence:** يحفظ الاختيار محليًا، مع خيار اتباع إعداد النظام.
- **Home-first:** الهدف، نمط التعاون، سياسة الكلفة/الجودة، تدفق الفريق، ثم الاتصالات.
- **Developer is secondary:** Terminal وGit انتقلا إلى صفحة Developer بدل احتلال الواجهة الرئيسية.
- **English-first:** تجنب خلط RTL/LTR داخل نفس الواجهة؛ الترجمة الكاملة يمكن إضافتها لاحقًا كوضع مستقل.
- **No remote UI dependency:** كل HTML/CSS/JS يعمل محليًا دون CDN أو مكتبة واجهات خارجية.

## Home

يوفر Home خمسة أنماط:

- Solo
- Team
- Parallel
- Verify
- Challenge

ويحوّل `Team` افتراضيًا إلى تعاون `challenge` عند التخطيط التكيفي. زر التخطيط يستخدم `/api/v1/ai/capacity/plan` ويحفظ Portfolio حقيقي عندما تكون العمليات مفعلة.

## Dreamer / Adaptive policy

- Cost optimization: Maximum Free → Balanced → Maximum Quality.
- Quality floor من 0.50 إلى 0.95.
- عرض free/local candidates الفعلية من Capacity Store.
- لا يتم افتراض أن اشتراك تطبيق استهلاكي يمنح API entitlement.

## AI Team Flow

يقرأ أعضاء الـPortfolio الفعليين ويعرض انتقال الأدوار مثل Producer → Challenger → Synthesizer. عند وجود خطة حديثة، يعرض الفريق الذي اختاره Adaptive Capacity Router.

## الصفحات

- Home
- Chat
- AI Teams
- Connections
- Tasks
- Files
- Artifacts
- Computer
- Developer
- Settings

## Light / Dark

يُخزن الاختيار تحت `w1-theme` في `localStorage`، والقيم المدعومة:

- `light`
- `dark`
- `system`

## حدود هذه الخطوة

- Chat conversational live inference لم يُربط بعد بمسار تنفيذ provider حي؛ الصفحة توضّح ذلك بدلاً من ادعاء وظيفة غير موجودة.
- Home ينفذ **تخطيط فريق حقيقي** وليس provider inference حيًا.
- OAuth interactive authorization سيبنى كتدفق منتج مستقل؛ Connections تعرض OAuth الموجود في Credential Broker ولا تتظاهر بتحويل اشتراكات التطبيقات إلى API.
