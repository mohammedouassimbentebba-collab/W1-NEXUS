# W1 Nexus™ — Brand & Release Identity

الإصدار المرجعي: `0.1.0.dev43`.

## القرار

اعتمد المشروع **Apache License 2.0** للكود، مع `NOTICE` و`BRAND-POLICY.md` منفصلين. لا يدّعي المشروع في هذه المرحلة أن W1 شركة مسجلة أو أن W1 Nexus علامة `®` مسجلة.

الاسم المعروض هو **W1 Nexus™**، بينما تبقى هوية التطبيق التقنية `com.w1.nexus` والبروتوكول `w1://` وامتداد المشروع `.w1nexus`.

## صاحب/هوية الإشعار الحالية

يستخدم `NOTICE` الصياغة:

`Copyright 2026 Wassim and W1 Nexus contributors.`

ولا يستخدم أسماء كيانات غير موجودة مثل `W1 Inc.` أو `W1 Corporation`.

## فصل الكود عن الهوية

Apache-2.0 يحكم حقوق استخدام/تعديل/توزيع الكود. سياسة العلامة منفصلة وهدفها منع الالتباس بين النسخة الرسمية للمشروع وبين fork أو منتج طرف ثالث. يجوز وصف مشروع آخر بدقة بأنه مبني على W1 Nexus أو متوافق معه دون الإيحاء بأنه الإصدار الرسمي.

## الشعار المرجعي

تم اعتماد التصميم الهندسي **W + 1** الذي اختاره صاحب المشروع كـMaster Concept وحفظه دون تعديل في:

`brand/reference/W1-Nexus-master-concept.jpeg`

ويثبت `brand/brand-manifest.json` بصمته SHA-256. هذا الملف **مرجع تصميم** وليس vector master صالحًا مباشرةً لـWindows/macOS packaging.

## أصول الإنتاج المطلوبة

الـmanifest يحدد الأصول التالية كعقود إنتاج منفصلة:

- `brand/production/w1-nexus-symbol.svg`
- `brand/production/w1-nexus-symbol-1024.png`
- `brand/production/w1-nexus.ico`
- `brand/production/w1-nexus.icns`
- `brand/production/favicon.svg`
- `brand/production/w1-nexus-splash.png`

لا تعتبر هذه الملفات جاهزة حتى تُشتق من الرمز المختار نفسه وتُراجع بصريًا. لا يسمح باستبدال الرمز بشعار AI مولّد مختلف تحت اسم W1 Nexus.

## Release Gate

`w1 release gate --public` يتحقق الآن من وجود الترخيص المقصود وهوية الإصدار الصحيحة. أما نشر Installer أصلي للعامة فيبقى له حد مستقل: أصول إنتاج أصلية، بناء فعلي على منصة الهدف، وتوقيع/تحقق native حقيقي.

## عدم الادعاء الزائد

- وجود `™` لا يُعرض هنا على أنه تسجيل رسمي.
- لا يوجد ادعاء بوجود شركة W1 قانونية حاليًا.
- صورة الـMaster Concept ليست SVG vector ولا يتم الادعاء بعكس ذلك.
- اختيار Apache-2.0 لا يحول fork خارجي تلقائيًا إلى إصدار رسمي من W1 Nexus.
