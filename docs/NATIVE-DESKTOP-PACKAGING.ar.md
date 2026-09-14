# Native Desktop Packaging — Step 38 + Step 45 RC integration

## الهدف

تحويل W1 Nexus من حزمة Python وDesktop loopback shell إلى منتج يمكن تغليفه كتطبيق سطح مكتب، من دون ربط النواة نفسها بنظام تشغيل واحد أو خادم W1 مركزي.

Step 38 يضيف عقدًا ثابتًا للهوية، deep links وfile associations، دورة حياة للخدمة المحلية، manifest تحديث محكومًا بالسلامة، ومصادر بناء Windows قابلة لإعادة الإنتاج. **لا يدّعي المشروع أن Windows EXE/Installer موقّع قد بُني داخل بيئة Linux الحالية.**

## هوية التطبيق

الهوية الافتراضية:

- App ID: `com.w1.nexus`
- Product: `W1 Nexus`
- URL scheme: `w1://`
- Project descriptor: `.w1nexus`

هذه الهوية مستقلة عن Model Provider وعن أي خدمة سحابية.

## Deep links

`w1://` لا يتحول إلى shell command. parser يسمح فقط بمسارات ثابتة:

- `w1://workspace/open`
- `w1://artifact/open`
- `w1://settings/open`
- `w1://account/open`

المعاملات التي تشبه `command`, `argv`, `shell`, `token`, `secret` مرفوضة، كما تُرفض المسارات المطلقة و`..` في target الخاص بالـworkspace.

## ملفات المشروع

`.w1nexus` ملف JSON صغير وغير سري. `workspace` داخله يجب أن يكون مسارًا نسبيًا إلى مكان descriptor ولا يستطيع الخروج إلى parent directory. الارتباط بالملف يمر عبر `w1cip.native_entrypoint` ولا ينفذ محتوى الملف ككود.

## دورة حياة Desktop service

`NativeServiceController` يشغل `w1 desktop serve` على loopback فقط، ويسجل PID مع marker لهوية عملية التشغيل وhash للأمر دون secrets. قبل الإيقاف يعاد فحص marker؛ إذا أُعيد استخدام PID لعملية أخرى يفشل الإيقاف fail-closed.

ملف الحالة لا يحتوي access tokens أو desktop bearer token.

## تحديثات محكومة بالسلامة

`UpdateManifest` يفرض:

1. HTTPS URL.
2. حجمًا محددًا.
3. SHA-256 محددًا.
4. metadata لهوية التوقيع عند تفعيل trust policy.

`verify_update_artifact` يثبت الحجم وSHA-256 لكنه **لا يساوي Authenticode verification**. التطبيق لا يعتبر `native_signature_verified=true` داخل هذه البيئة. التنزيل/التطبيق التلقائي سيبقى ممنوعًا إلى أن يضاف native signature verifier وtransactional updater في مرحلة hardening.

## Windows build sources

`w1 packaging sources` يولد:

- PyInstaller spec.
- Inno Setup installer source.
- per-user registry scripts.
- build/signing hook.
- manifest مع SHA-256 للملفات المولدة.

Installer يستخدم HKCU و`PrivilegesRequired=lowest`، أي لا يطلب Administrator افتراضيًا. التسجيل يضيف `w1://` و`.w1nexus` فقط.

## Signing

وجود signing hook لا يعني أن الملف موقّع. على Windows، `build.ps1` يستطيع استخدام شهادة موجودة أصلًا في certificate store بواسطة thumbprint من `W1_WINDOWS_SIGN_CERT_SHA1`. لا يُخزن private key داخل repository. بعد التوقيع يجري `Get-AuthenticodeSignature` على EXE والـInstaller ويجب أن تكون الحالة `Valid` قبل اعتبارهما قابلين للنشر.

## CLI

```text
w1 packaging doctor --target windows
w1 packaging benchmark
w1 packaging sources --output packaging/windows
w1 packaging deep-link "w1://settings/open?section=models"
w1 packaging project create project.w1nexus --name "Project"
w1 packaging project show project.w1nexus
w1 packaging service status
w1 packaging service start --port 8766
w1 packaging service stop
w1 packaging verify-update --manifest update.json --artifact W1-Nexus.exe
```

## حدود Step 38

- لم يُبنَ Windows EXE/Installer حيًا في مضيف Linux الحالي.
- لا توجد شهادة Authenticode مملوكة للمشروع داخل بيئة البناء، لذلك لا يوجد signed release claim.
- macOS codesign/notarization وLinux packaging لم يُغلقا بعد.
- آلية auto-update التي تحمل الملف وتستبدل النسخة transactionally لم تُفعّل؛ الموجود هو trust/integrity contract فقط.
- ربط deep link بصفحة بعينها داخل واجهة Desktop ما زال navigation handoff وليس تشغيل actions تلقائيًا.


## تحديث Step 45

أصبحت أصول W1 Nexus™ الإنتاجية جاهزة ومثبتة بالـSHA-256، ويستخدم PyInstaller/Inno Setup أيقونة `.ico` الفعلية وWindows version resource. `rc-check` يثبت المصدر قبل البناء الأصلي. لا يزال Authenticode والتجميع الفعلي على Windows حدًا منفصلًا.
