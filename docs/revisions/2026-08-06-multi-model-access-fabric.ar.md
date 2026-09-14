# مذكرة اعتماد Multi-Model Access Fabric

**التاريخ:** 2026-08-06  
**الإصدار:** `0.1.0.dev32`

## القرار

تعتمد W1 Nexus بنية محلية أولًا ومحايدة للمزود، ولا تتطلب خوادم W1 أو نموذجًا مركزيًا.

المستخدم يختار **عدة نماذج مفضلة**، وليس نموذجًا واحدًا، ويمكن توزيعها على أدوار التنفيذ والتخصص والمراجعة والتحقق والتركيب.

## المكونات المعتمدة

- `ModelProfile`
- `ModelPortfolio`
- `ModelAccessStore`
- `ModelAccessFabric`
- `PortfolioProviderAdapter`
- `ExternalApplicationConnector`
- `PythonPluginAdapterFactory`
- `EmbeddedW1Runtime`
- `LocalControlServer`
- `W1LocalClient`
- التقييم التنفيذي الموزون

## السياسات

- منع Model-count voting.
- عدم منح نموذج سحابي أولوية لمجرد شهرته.
- عدم الهبوط الصامت في الجودة.
- عدم تخزين قيم المفاتيح.
- منع Local Control API من الارتباط بعنوان عام.
- التطبيقات الخارجية تحتاج واجهة صريحة؛ لا استخراج لجلسات المستخدم أو Cookies.

## ما يؤجل

- OAuth broker عام.
- مزامنة الحسابات والملفات.
- W1 Cloud اختياري.
- Marketplace للإضافات.
- Benchmark جودة حي لنماذج المستخدم الحقيقية.
