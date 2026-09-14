# مثال W1 Scientific Loop

بعد تثبيت W1 وتهيئة مساحة عمل:

```bash
w1 --workspace ./science-demo init

w1 --workspace ./science-demo science create \
  --plan examples/scientific-loop/pump-current-study.json

w1 --workspace ./science-demo science preregister pump-current-study

w1 --workspace ./science-demo science observe pump-current-study \
  --file examples/scientific-loop/observations.json

w1 --workspace ./science-demo science analyze pump-current-study \
  --analysis-id current-difference

w1 --workspace ./science-demo science evaluate pump-current-study

w1 --workspace ./science-demo science review pump-current-study \
  --reviewer-id independent-reviewer \
  --outcome approved \
  --rationale "Preregistration, observations, and planned analysis verified."

w1 --workspace ./science-demo science package pump-current-study \
  --output ./science-demo/reports/pump-current-study.zip

w1 --workspace ./science-demo science bridge pump-current-study
w1 --workspace ./science-demo science verify --study-id pump-current-study
```

البيانات تركيبية للاختبار، وليست قياسات اعتماد لمنتج حقيقي.
