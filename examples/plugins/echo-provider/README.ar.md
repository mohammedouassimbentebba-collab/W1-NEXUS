# مثال Managed Provider Plugin

هذا المثال يعتمد فقط على `w1cip.sdk`. اختبره أولًا:

```bash
w1 plugins conformance examples/plugins/echo-provider
```

ثم ثبته في Workspace محلي:

```bash
w1 plugins install examples/plugins/echo-provider --enable
```

بعد ذلك يمكن لـModel Profile من نوع `custom_plugin` استخدام `plugin_factory` بالقيمة `w1-plugin:org.w1.examples.echo-provider`.
